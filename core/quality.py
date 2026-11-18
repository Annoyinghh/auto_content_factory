"""Quality gate checks, review decisions, and zero-API multi-channel notifications."""

import datetime as dt
import ipaddress
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests

from config import DOUYIN_BRIEF_SECONDS, PLATFORM_PRESETS
from core.accounts import protect_json, unprotect_json
from core.team_client import MAX_VIDEO_BYTES
from core.workflow import _lock, write_json


PLATFORM_LABELS = {"douyin": "抖音", "xiaohongshu": "小红书", "bilibili": "B站"}


def _mp4_ok(path, size):
    try:
        with path.open("rb") as file:
            header = file.read(16)
    except OSError:
        return False
    return len(header) == 16 and header[4:8] == b"ftyp" and 16 <= int.from_bytes(header[:4], "big") <= size


def duration_bounds(platform, target, *, source_limited=False):
    if platform in {"douyin", "xiaohongshu"} and target in DOUYIN_BRIEF_SECONDS:
        low = round(target * 0.8)
        return min(low, 60) if source_limited else low, min(180, round(target * 1.2))
    if target:
        return 600, 900
    return None


def _source_limited(item):
    reports = (item.get('editorial') or {}).get('reports', [])
    return ((item.get('topic_job') or {}).get('scope') == 'topic'
            and len({r.get('url') for r in reports if r.get('url')}) == 1)


def _media_summary(item):
    from core.media_review import media_state

    assets = item.get("media") or []
    approved = sum(1 for asset in assets if media_state(item, asset) == "approved")
    pending = sum(1 for asset in assets if media_state(item, asset) == "pending")
    return {"approved": approved, "pending": pending, "total": approved + pending}


def inspect_package(item, platform, package):
    """Verify an exported package: readable MP4, narration duration in range, real footage coverage."""
    checks = {}
    findings = []

    video = Path(package.get("video") or "")
    size = video.stat().st_size if video.is_file() else 0
    file_ok = bool(video.is_file() and 0 < size <= MAX_VIDEO_BYTES and _mp4_ok(video, size))
    checks["file"] = {"path": str(video), "bytes": size, "ok": file_ok}
    if not file_ok:
        findings.append("成片文件缺失、损坏或超出大小限制。")

    duration = package.get("duration")
    bounds = duration_bounds(platform, item.get("target_seconds"), source_limited=_source_limited(item))
    if bounds is not None:
        low, high = bounds
        duration_ok = type(duration) in {int, float} and low <= duration <= high
        checks["duration"] = {"seconds": duration, "range": [low, high], "measured": True, "ok": duration_ok}
        if not duration_ok:
            actual = f"{duration:.0f} 秒" if type(duration) in {int, float} else "未知"
            findings.append(f"成片时长不符合要求（实测 {actual}，需在 {low}–{high} 秒内）。")

    media_info = _media_summary(item)
    checks["media"] = media_info
    draft = item.get("edit") or item.get("suggestion") or {}
    if isinstance(draft.get("scenes"), list) and draft["scenes"]:
        from core.directing import audit_visuals
        from core.media_review import usable_media

        audit = audit_visuals(draft, usable_media(item))
        checks["visuals"] = audit
        if not audit["ready"]:
            extra = f"（当前还有 {media_info['pending']} 项素材待审核通过）" if media_info["pending"] else ""
            findings.append(audit["message"] + extra)

    message = "；".join(findings)
    return {"ready": not findings, "findings": findings, "message": message, "checks": checks,
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def inspect_item(item, platform):
    """Inspect either an exported package or an in-progress draft/preview for UI guidance."""
    if not item:
        return {"stage": "empty", "ready": False, "findings": ["尚未创建工作稿。"], "message": "尚未创建工作稿。", "checks": {}}
    package = item.get("package") or {}
    if package.get("video"):
        report = inspect_package(item, platform, package)
        report["stage"] = "package"
    else:
        checks = {"media": _media_summary(item)}
        findings = []
        draft = item.get("edit") or item.get("suggestion") or {}
        scenes = draft.get("scenes") if isinstance(draft.get("scenes"), list) else []
        if scenes:
            from core.directing import audit_visuals
            from core.media_review import usable_media

            audit = audit_visuals(draft, usable_media(item))
            checks["visuals"] = audit
            if not audit["ready"]:
                extra = f"（有 {checks['media']['pending']} 项待审核素材可先点通过）" if checks["media"]["pending"] else ""
                findings.append(audit["message"] + extra)
            preview = item.get("full_preview") or {}
            bounds = duration_bounds(platform, item.get("target_seconds"), source_limited=_source_limited(item))
            if bounds is not None:
                low, high = bounds
                measured = preview.get("duration") if item.get("full_preview_revision") == item.get("edit_revision") else None
                chars = sum(len((shot.get("text") or "").strip()) for shot in scenes)
                seconds = measured if type(measured) in {int, float} else round(chars / 5.0, 1)
                ok = low <= seconds <= high
                checks["duration"] = {"seconds": seconds, "range": [low, high],
                                      "measured": measured is not None, "ok": ok}
                if not ok:
                    kind = "试剪实测" if measured is not None else "按字数预估"
                    findings.append(f"{kind}口播约 {seconds:.0f} 秒，目标范围 {low}–{high} 秒。")
        else:
            findings.append("尚未生成分镜草稿。")
        checks["file"] = {"path": "", "bytes": 0, "ok": None}
        report = {"stage": "draft", "ready": not findings and bool(scenes), "findings": findings,
                  "message": "；".join(findings), "checks": checks,
                  "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}

    review = item.get("quality_review")
    if isinstance(review, dict):
        report["review"] = review
        if review.get("decision") == "rejected":
            report["ready"] = False
            reason = (review.get("reason") or "").strip()
            if reason and reason not in report["findings"]:
                report["findings"] = [f"人工退回：{reason}", *report["findings"]]
                report["message"] = "；".join(report["findings"])
    return report


def flagged_caption(package, findings, *, source):
    header = {"auto": "【自动检测未通过】", "manual": "【人工标记不合适】"}[source]
    issues = "\n".join(f"· {line}" for line in findings if line)
    caption = (package.get("caption") or "").strip()
    return f"{header}\n{issues}\n\n{caption}".strip() if issues else f"{header}\n\n{caption}".strip()


def share_flagged(client, platform, package, findings, *, source, resume_dir=None):
    flagged = {**package, "caption": flagged_caption(package, findings, source=source)}
    return client.share(platform, flagged, resume_dir=resume_dir)


def detect_webhook_kind(url):
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith(("feishu.cn", "larksuite.com")):
        return "feishu", "飞书群机器人"
    if host.endswith("qyapi.weixin.qq.com"):
        return "wecom", "企业微信群机器人"
    if host.endswith("dingtalk.com"):
        return "dingtalk", "钉钉群机器人"
    if host.endswith(("discord.com", "discordapp.com")):
        return "discord", "Discord Webhook"
    if host.endswith("hooks.slack.com"):
        return "slack", "Slack Webhook"
    return "generic", "自定义 Webhook"


def validate_webhook_url(url):
    value = (url or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    host = parts.hostname or ""
    if not host or parts.username is not None or parts.fragment:
        raise ValueError("请输入完整的群机器人或 Webhook HTTPS 地址。")
    loopback = parts.scheme == "http" and host in {"127.0.0.1", "localhost"}
    if parts.scheme != "https" and not loopback:
        raise ValueError("通知 Webhook 必须使用 HTTPS 地址。")
    if not loopback:
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            addr = None
        if addr is not None and (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved):
            raise ValueError("不允许使用内网私有地址作为远程 Webhook。")
    return value


def _mask_url(url):
    if not url:
        return ""
    parts = urlsplit(url)
    tail = (parts.path + ("?" + parts.query if parts.query else ""))
    if len(tail) > 18:
        tail = tail[:10] + "…" + tail[-6:]
    return f"{parts.scheme}://{parts.netloc}{tail}"


def load_webhook(data_dir):
    path = Path(data_dir) / "accounts" / "webhook.json"
    if not path.is_file():
        return {"url": "", "enabled": False, "label": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        record = unprotect_json(raw["sealed"])
        url = validate_webhook_url(record.get("url", ""))
        return {"url": url, "enabled": bool(record.get("enabled", True)) and bool(url),
                "label": str(record.get("label") or "").strip()[:60]}
    except (KeyError, OSError, TypeError, ValueError, RuntimeError):
        return {"url": "", "enabled": False, "label": ""}


def save_webhook(data_dir, url, *, enabled=True, label=""):
    clean_url = validate_webhook_url(url)
    clean_label = str(label or "").strip()[:60]
    path = Path(data_dir) / "accounts" / "webhook.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not clean_url:
        path.unlink(missing_ok=True)
        return webhook_summary(data_dir)
    record = {"sealed": protect_json({"url": clean_url, "enabled": bool(enabled), "label": clean_label})}
    with _lock(path.with_suffix(".sqlite3"), timeout=5):
        write_json(path, record)
    return webhook_summary(data_dir)


def webhook_summary(data_dir):
    config = load_webhook(data_dir)
    url = config["url"]
    kind_id, kind_name = detect_webhook_kind(url) if url else ("", "未配置")
    return {"configured": bool(url), "enabled": config["enabled"], "kind": kind_id,
            "kind_name": kind_name, "label": config["label"] or kind_name, "masked_url": _mask_url(url)}


def send_webhook(url, headline, lines):
    clean_url = validate_webhook_url(url)
    if not clean_url:
        raise ValueError("请先填写通知 Webhook 地址。")
    kind, _ = detect_webhook_kind(clean_url)
    body_text = "\n".join([headline, *(line for line in lines if line)])
    if kind == "feishu":
        payload = {"msg_type": "text", "content": {"text": body_text}}
    elif kind in {"wecom", "dingtalk"}:
        payload = {"msgtype": "text", "text": {"content": body_text}}
    elif kind == "discord":
        payload = {"content": body_text[:1950]}
    elif kind == "slack":
        payload = {"text": body_text}
    else:
        payload = {"text": body_text, "title": headline, "content": body_text, "lines": list(lines)}
    try:
        response = requests.post(clean_url, json=payload, timeout=(5, 10), allow_redirects=False,
                                 headers={"User-Agent": "AutoContentFactory-Notice/1.0"})
    except requests.RequestException as error:
        raise RuntimeError("无法连接通知 Webhook，请检查地址与网络。") from error
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Webhook 返回 HTTP {response.status_code}，请核对机器人地址。")
    try:
        reply = response.json()
    except ValueError:
        reply = None
    if isinstance(reply, dict):
        code = reply.get("code", reply.get("errcode", 0))
        if code not in (0, None, "0"):
            msg = reply.get("msg") or reply.get("errmsg") or f"code {code}"
            raise RuntimeError(f"群机器人拒绝了消息（{msg}）。如开启了关键词安全设置，请包含「内容工作台」或关闭签名限制。")
    return True


def _notices_path(output_dir):
    return Path(output_dir) / "quality_notices.json"


def list_notices(output_dir, *, limit=20):
    path = _notices_path(output_dir)
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    active = [row for row in rows if isinstance(row, dict) and not row.get("resolved")]
    return active[:limit]


def record_notice(output_dir, entry):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _notices_path(output_dir)
    with _lock(path.with_suffix(".sqlite3"), timeout=5):
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, ValueError):
            existing = []
        if not isinstance(existing, list):
            existing = []
        filtered = [row for row in existing if isinstance(row, dict)
                    and not (row.get("day") == entry.get("day") and row.get("platform") == entry.get("platform")
                             and not row.get("resolved"))]
        notice = {"id": uuid.uuid4().hex, "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                  "resolved": False, **entry}
        write_json(path, [notice, *filtered][:60])
    return notice


def resolve_notices(output_dir, *, day=None, platform=None, notice_id=None):
    path = _notices_path(output_dir)
    if not path.is_file():
        return False
    changed = False
    with _lock(path.with_suffix(".sqlite3"), timeout=5):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, dict) or row.get("resolved"):
                continue
            if (notice_id and row.get("id") == notice_id) or (
                    day and platform and row.get("day") == str(day) and row.get("platform") == platform):
                row["resolved"] = True
                changed = True
        if changed:
            write_json(path, rows[:60])
    return changed


def dispatch_quality_notice(output_dir, day, platform, title, findings, *, source, package=None, fingerprint=None):
    """Record locally and push to all configured zero-API channels (Webhook, Operations, Team Review)."""
    output_dir = Path(output_dir).resolve()
    data_dir = output_dir.parent
    platform_name = PLATFORM_LABELS.get(platform, platform or "未指定")
    tag = "自动质检未通过" if source == "auto" else "审片标记不合适"
    clean_findings = [str(item).strip() for item in (findings or ()) if str(item).strip()] or ["需人工复核并调整。"]

    channels = ["本机待改清单"]
    errors = []
    review_id = None

    webhook = load_webhook(data_dir)
    if webhook["enabled"] and webhook["url"]:
        headline = f"【内容工作台 · {tag}】{platform_name}《{title}》"
        lines = [
            f"创作日期：{day}",
            *(f"· {line}" for line in clean_findings),
            "状态：已自动暂缓发布，请回「热采放映室 → ② 审素材与剪辑」调整后重新导出。",
        ]
        try:
            send_webhook(webhook["url"], headline, lines)
            _, kind_name = detect_webhook_kind(webhook["url"])
            channels.append(webhook["label"] or kind_name)
        except Exception as error:
            errors.append(f"群通知未送达：{error}")

    try:
        from core.operations_client import OperationsClient

        ops = OperationsClient.load(data_dir)
        if ops is not None:
            snapshot = ops.snapshot()
            member = snapshot["member"]["name"]
            matched = [row for row in snapshot.get("publications", ())
                       if (row.get("owner"), row.get("platform"), row.get("title")) == (member, platform, title)
                       and row.get("status") != "failed"]
            for row in matched[:1]:
                ops.set_publication(row["id"], row["revision"], "failed")
                channels.append("共享运营进度已标为失败")
    except Exception as error:
        errors.append(f"共享运营同步未完成：{error}")

    if package and package.get("video") and Path(package["video"]).is_file():
        try:
            from core.team_client import TeamClient

            team = TeamClient.load(data_dir)
            if team is not None:
                review_id = share_flagged(team, platform, package, clean_findings,
                                          source=source, resume_dir=data_dir / "team-cache" / "uploads")
                channels.append("已送入团队审片清单")
        except Exception as error:
            errors.append(f"团队审片未上传：{error}")

    notice = record_notice(output_dir, {
        "day": str(day), "platform": platform, "platform_name": platform_name,
        "title": title, "source": source, "tag": tag, "findings": clean_findings,
        "channels": channels, "errors": errors, "review_id": review_id, "render_key": fingerprint,
    })
    return {"notice_id": notice["id"], "review_id": review_id, "render_key": fingerprint,
            "channels": channels, "errors": errors}
