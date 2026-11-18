"""Local workspace data shared with the native Windows workbench."""

import datetime as dt
import hashlib
import json
import re
import subprocess
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from config import DATA_DIR, get_ffmpeg
from core.account_metrics import import_metrics, read_metrics, write_template
from core.accounts import PLATFORMS as AUTHORIZED_PLATFORMS, account_hint
from core.daily_news import story_key
from core.douyin_profiles import douyin_profiles, save_profile
from core.intake import REFRESH_MINUTES, has_report, match_hotspots, read_intake
from core.matrix_accounts import PLATFORMS as MATRIX_PLATFORMS, local_accounts, save_local_account
from core.workflow import _lock, edition_path, read_edition, release_directory, run_stage, write_json
from core.works_library import load_works, cleared_work_ids as _cleared_works


PLATFORM_NAMES = {"douyin": "抖音", "xiaohongshu": "小红书", "bilibili": "B站"}


def _account(platform, connection, identity, label):
    # Existing metrics are indexed by this key, independently of display name.
    key = hashlib.sha256(f"{platform}/{connection}/{identity}".encode()).hexdigest()
    return {"key": key, "label": label, "platform": platform, "platform_name": PLATFORM_NAMES[platform],
            "source": "本机浏览器配置" if connection == "browser" else "开放平台账号"}


def _known_accounts(data_dir, *, strict=False):
    try:
        profiles, error = douyin_profiles(data_dir), ""
    except (ValueError, OSError):
        if strict:
            raise
        profiles, error = {}, "抖音账号列表无法读取。"
    rows = [{**_account("douyin", "browser", profile_id, label), "profile_id": profile_id}
            for profile_id, label in profiles.items()]
    # OAuth belongs to the configured application root. An alternate workspace
    # must never inherit those identities or associate them with its CSV data.
    if data_dir == DATA_DIR.resolve():
        for platform in AUTHORIZED_PLATFORMS:
            try:
                label, identity = account_hint(platform)
            except (TypeError, AttributeError):
                continue
            if isinstance(label, str) and isinstance(identity, str) and identity:
                rows.append(_account(platform, "authorized", identity, label))
    names = Counter((row["platform_name"], row["label"]) for row in rows)
    for row in rows:
        row["importable"] = names[row["platform_name"], row["label"]] == 1
    return rows, error


def _account_matrix(data_dir, include_team):
    local, local_error = _known_accounts(data_dir)
    rows = [{**row, "id": row["key"], "owner": "我", "shared": False,
             "can_publish": bool(row.get("profile_id")) or row["platform"] == "bilibili",
             "access": "本机创作者窗口" if row.get("profile_id") else "开放平台已授权"}
            for row in local]
    try:
        for row in local_accounts(data_dir):
            entry = {**row, "owner": "我", "shared": False, "local_registration": True, "access": "本机登记"}
            if row["platform"] == "xiaohongshu":
                entry.update(profile_id=row["id"], can_publish=True, access="本机创作者窗口")
            rows.append(entry)
    except (ValueError, OSError):
        local_error = " ".join(part for part in (local_error, "本机矩阵账号记录无法读取。") if part)
    catalog = [{"platform": key, "platform_name": name, "category": category, "can_share": key in PLATFORM_NAMES}
               for key, (name, category) in MATRIX_PLATFORMS.items()]
    platforms = {entry["platform"]: (index, entry) for index, entry in enumerate(catalog)}

    def decorate():
        for row in rows:
            _, platform = platforms[row["platform"]]
            row.update(category=platform["category"], platform_name=platform["platform_name"],
                       can_share=platform["can_share"] and not row["shared"])
        rows.sort(key=lambda row: (platforms[row["platform"]][0], row["label"].casefold(),
                                   "key" not in row and not row.get("local_registration"), row["owner"]))

    decorate()
    result = {"accounts": rows, "platforms": catalog, "local_error": local_error, "team_status": "loading"}
    if not include_team:
        return result
    from core.wpf_team import _load
    try:
        client = _load("operations", data_dir)
        if client is None:
            result["team_status"] = "unconfigured"
            return result
        snapshot = client.snapshot()
        member, shared = snapshot["member"]["name"], snapshot["accounts"]
        if not isinstance(member, str) or not isinstance(shared, list):
            raise ValueError("Invalid account directory.")
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        result["team_status"] = "unavailable"
        return result
    result["team_status"] = "connected"
    for account in shared:
        if not isinstance(account, dict):
            continue
        account_id, platform, label, owner = (account.get(key) for key in ("id", "platform", "label", "owner"))
        if not all(isinstance(value, str) and value for value in (account_id, platform, label, owner)):
            continue
        if platform not in MATRIX_PLATFORMS:
            continue
        matches = [row for row in rows if not row["shared"] and row["platform"] == platform
                   and row["label"] == label and owner == member]
        if len(matches) == 1:
            matches[0].update(owner=owner, shared=True)
        else:
            rows.append({"id": "team-" + account_id, "platform": platform, "label": label,
                         "owner": owner, "shared": True, "access": "已登记到矩阵"})
    decorate()
    return result


def _change_recycle(directory, stage, payload, work_ids):
    directory.mkdir(parents=True, exist_ok=True)
    with _lock(directory / ".writes.sqlite3", timeout=5):
        cleared = _cleared_works(directory)
        if stage == "workspace_work_empty":
            ids = payload.get("ids")
            if not isinstance(ids, list) or any(not isinstance(item, str) or
                                               not re.fullmatch(r"[a-f0-9]{64}", item) for item in ids):
                raise ValueError("无法识别回收站中的作品。")
            requested = set(ids) - cleared
            if any(not (directory / item).is_file() for item in requested):
                raise ValueError("回收站内容已变化，请刷新后再清空。")
            if requested:
                cleared.update(requested)
                write_json(directory / "cleared.json", sorted(cleared))
            return cleared
        work_id, restore = payload.get("id"), payload.get("restore")
        if not isinstance(work_id, str) or not isinstance(restore, bool) or work_id not in work_ids:
            raise ValueError("找不到要操作的作品，请重新打开作品库。")
        if work_id in cleared:
            raise ValueError("这件作品已从回收站清空。")
        marker = directory / work_id
        if restore:
            marker.unlink(missing_ok=True)
        else:
            # Only the catalogue record moves. Other works may share these files.
            marker.touch(exist_ok=True)
        return cleared


def _metrics_rows(data_dir, day, accounts):
    try:
        metrics, error = read_metrics(data_dir, day=day), False
    except (OSError, ValueError):
        metrics, error = {}, True
    return [{**row, "metrics": list(metrics.get(row["key"], (None,) * 5))} for row in accounts], error


def _release_rows(path, items):
    states = {}
    for platform, item in items.items():
        if item.get("release_dir") or (item.get("package") or {}).get("video"):
            directory = release_directory(path, platform, item)
            if (directory / "submitted.txt").is_file():
                states[platform] = {"state": "已提交审核"}
            elif (directory / "submitting.txt").exists():
                states[platform] = {"state": "待核对 · 不重发"}
    return states


def _editions(output_dir, day):
    monday = day - dt.timedelta(days=day.weekday())
    summaries = []
    for offset in range(7):
        current = monday + dt.timedelta(days=offset)
        path = edition_path(output_dir, current)
        try:
            items = read_edition(path).get("items", {})
            states = _release_rows(path, items)
        except (AttributeError, OSError, TypeError, ValueError):
            continue
        summaries.append({"date": current.isoformat(), "items": len(items),
                          "packages": sum(bool(item.get("package")) for item in items.values()),
                          "published": sum(row["state"] == "已提交审核" for row in states.values())})
    return summaries


def _article(story):
    return {**story, "id": story_key(story)} if story is not None else None


def _snapshot(snapshot, output_dir, day, now, *, summary_only=False):
    date = now.astimezone().date()
    accounts, accounts_error = _known_accounts(output_dir.parent)
    accounts, metrics_error = _metrics_rows(output_dir.parent, date, accounts)
    today = snapshot["today"]
    result = {
        "date": date.isoformat(), "day": day.isoformat(),
        "today": {**today, "articles": [_article(story) for story in today["articles"]]},
        "checked": snapshot["checked"], "errors": snapshot["errors"], "accounts": accounts,
        "accounts_error": accounts_error, "metrics_error": metrics_error, "refresh_minutes": REFRESH_MINUTES,
    }
    if summary_only:
        return result
    matched = match_hotspots(snapshot, now)
    checked = snapshot["checked"]
    if checked and not any(row.get("article") for rows in matched.values() for row in rows):
        matched = match_hotspots(snapshot, dt.datetime.fromtimestamp(checked, dt.timezone.utc))
    result.update(
        articles=[_article(story) for story in snapshot["articles"]],
        ranks={platform: {**board, "entries": [
            {**row, "article": _article(row["article"]), "has_report": has_report(row["article"])}
            for row in matched.get(platform, ())]} for platform, board in snapshot["ranks"].items()},
        editions=_editions(output_dir, day),
    )
    return result


def _ai_status():
    import os
    import sys
    from config import BASE_DIR
    from core import local_ai

    models = local_ai.installed_models(local_only=True)
    writing = local_ai.model_for("writing")
    design = local_ai.model_for("design")
    editing = local_ai.model_for("editing")
    studio_root = os.environ.get("ACF_STUDIO_ROOT")
    directory = Path(studio_root) if studio_root else (
        Path(sys.executable).parent if getattr(sys, "frozen", False) else BASE_DIR / "dist")
    portable = (directory / "local-ai" / "runtime" / "ollama.exe").is_file()
    return {
        "ready": bool(models),
        "models": sorted(models),
        "writing_model": writing,
        "design_model": design,
        "editing_model": editing,
        "writing_ready": writing in models,
        "visual_ready": design in models and editing in models,
        "portable_available": portable,
    }


def _recent_creations(output_dir, limit=12):
    from core.media_review import media_state
    from core.quality import inspect_item
    from core.wpf_history import creation_list

    listing = creation_list(output_dir)
    enriched = []
    for row in listing.get("rows", ())[:limit]:
        path = edition_path(output_dir, row["day"])
        edition = read_edition(path)
        item = (edition.get("items", {}).get(row["platform"], {}) if row.get("active") else None)
        if not item:
            item = next((entry for entry in edition.get("previous_items", {}).get(row["platform"], ())
                         if entry.get("id") == row.get("id")), {})
        assets = item.get("media") or []
        pending_media = sum(1 for asset in assets if media_state(item, asset) == "pending")
        approved_media = sum(1 for asset in assets if media_state(item, asset) == "approved")
        inspection = inspect_item(item, row["platform"]) if item else {"ready": False, "message": "", "stage": "empty"}
        package = item.get("package") or {}
        held = bool(package.get("video")) and item.get("held_video") == package.get("video")
        enriched.append({
            **row,
            "platform_name": PLATFORM_NAMES.get(row["platform"], row["platform"]),
            "pending_media": pending_media,
            "approved_media": approved_media,
            "has_package": bool(package.get("video")) and Path(package["video"]).is_file(),
            "has_preview": bool((item.get("full_preview") or {}).get("video") or (item.get("preview") or {}).get("video")),
            "held": held,
            "quality_ready": inspection.get("ready"),
            "quality_message": inspection.get("message", ""),
        })
    return enriched, listing.get("errors", [])


def _pipeline(output_dir, day, now):
    """Comprehensive home hub snapshot: AI engine, notifications, collection, cross-date active works."""
    from core.quality import list_notices, webhook_summary

    snapshot = read_intake(output_dir, now)
    today = snapshot["today"]
    collection = {"reports": len(today.get("articles") or []),
                  "hotspots": sum(len(board.get("entries") or ()) for board in snapshot.get("ranks", {}).values()),
                  "checked": snapshot["checked"], "errors": snapshot.get("errors") or []}
    path = edition_path(output_dir, day)
    items = read_edition(path).get("items", {})
    states = _release_rows(path, items)
    team_connected = False
    ops_connected = False
    try:
        from core.wpf_team import _load
        team_connected = _load("review", output_dir.parent) is not None
        ops_connected = _load("operations", output_dir.parent) is not None
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        pass
    platforms = {}
    for platform in PLATFORM_NAMES:
        item = items.get(platform) or {}
        audit = item.get("production_audit") or {}
        package = item.get("package") or {}
        platforms[platform] = {
            "selected": bool(item),
            "topic": (item.get("story") or {}).get("title", ""),
            "has_draft": bool(item.get("edit") or item.get("suggestion")),
            "draft_saved": bool(item.get("edit")),
            "progress": item.get("editorial_progress") or "",
            "status": item.get("status", ""),
            "package": bool(package.get("video")),
            "audit_ready": audit.get("ready"),
            "audit_message": audit.get("message", ""),
            "held": bool(package.get("video")) and item.get("held_video") == package.get("video"),
            "release": (states.get(platform) or {}).get("state", ""),
            "noticed": bool((item.get("quality_notice") or {}).get("notice_id")
                            or (item.get("quality_notice") or {}).get("review_id")),
        }
    recent, creation_errors = _recent_creations(output_dir, limit=10)
    return {
        "collection": collection,
        "platforms": platforms,
        "ai": _ai_status(),
        "webhook": webhook_summary(output_dir.parent),
        "ops_connected": ops_connected,
        "team_connected": team_connected,
        "notices": list_notices(output_dir, limit=10),
        "recent_creations": recent,
        "creation_errors": creation_errors,
    }


def _verify_ffmpeg():
    status = {"path": "", "ok": False, "version": "", "error": ""}
    try:
        status["path"] = get_ffmpeg()
        check = subprocess.run([status["path"], "-version"], capture_output=True, encoding="utf-8", errors="replace",
                               timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if check.returncode:
            status["error"] = check.stderr.strip()[:500] or f"FFmpeg 退出代码 {check.returncode}"
        else:
            status.update(ok=True, version=check.stdout.partition("\n")[0])
    except (ImportError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        status["error"] = str(error)
    return status


def workspace_stage(stage, day, output_dir, platform, payload, *, now=None):
    """Read or update local workspace data using the desktop job envelope."""
    output_dir = Path(output_dir).resolve()
    data_dir = output_dir.parent
    now = now or dt.datetime.now().astimezone()
    date = now.astimezone().date()
    day = dt.date.fromisoformat(str(day)) if day else date
    payload = payload or {}
    if stage in {"workspace_creation_list", "workspace_creation_state", "workspace_creation_open"}:
        from core.wpf_history import creation_list, creation_state, open_creation
        if stage == "workspace_creation_list":
            return creation_list(output_dir)
        if stage == "workspace_creation_state":
            return creation_state(output_dir, day, platform)
        return open_creation(output_dir, day, platform, payload)
    if stage in {"workspace_snapshot", "workspace_collection"}:
        snapshot = read_intake(output_dir, now)
        refreshed = False
        if stage == "workspace_collection":
            refresh = payload.get("refresh", "due")
            if refresh not in {"due", "force", "never"}:
                raise ValueError("请选择有效的采集刷新方式。")
            due = not snapshot["checked"] or now.timestamp() - snapshot["checked"] >= REFRESH_MINUTES * 60
            if refresh == "force" or (refresh == "due" and due):
                snapshot = run_stage("intake", day, output_dir, now=now)
                refreshed = True
        result = _snapshot(snapshot, output_dir, day, now, summary_only=payload.get("summary_only"))
        if stage == "workspace_collection":
            result["refreshed"] = refreshed
        return result
    if stage == "workspace_edition":
        return read_edition(edition_path(output_dir, day))
    if stage == "workspace_pipeline":
        return _pipeline(output_dir, day, now)
    if stage == "workspace_ai_status":
        return _ai_status()
    if stage == "workspace_ai_start":
        from core import local_ai
        local_ai.start_portable_ai(wait=True)
        return _ai_status()
    if stage == "workspace_approve_all_media":
        if platform not in PLATFORM_NAMES:
            raise ValueError("请选择有效的作品平台。")
        from core.media_review import media_state
        from core.workflow import _edit_edition

        path = edition_path(output_dir, day)
        approved_count = 0

        def approve_all(edition):
            nonlocal approved_count
            item = edition.get("items", {}).get(platform)
            if not item:
                raise ValueError("当天还没有这个平台的工作稿。")
            reviews = dict(item.get("media_review") or {})
            for asset in item.get("media") or ():
                if media_state(item, asset) == "pending":
                    reviews[asset["page_url"]] = "approved"
                    approved_count += 1
            item["media_review"] = reviews

        _edit_edition(path, approve_all)
        return {"approved": approved_count}
    if stage == "workspace_quick_create":
        if platform not in PLATFORM_NAMES:
            raise ValueError("请选择制作平台。")
        title = (payload.get("title") or "").strip()
        script = (payload.get("script") or "").strip()
        if not 1 <= len(title) <= 160:
            raise ValueError("请填写 1–160 字的选题标题。")
        if len(script) < 40:
            raise ValueError("请粘贴至少 40 字的文案、提纲或讲述要点，方便本地 AI 准确编排分镜。")
        chunks = [script[index:index + 1800].strip() for index in range(0, min(len(script), 12000), 1800)]
        chunks = [chunk for chunk in chunks if chunk][:8]
        raw_assets = payload.get("asset_paths") or []
        if not isinstance(raw_assets, list) or len(raw_assets) > 8:
            raise ValueError("最多同时加入 8 个本地媒体文件。")
        assets = [str(entry).strip() for entry in raw_assets if str(entry).strip()]
        seconds = payload.get("target_seconds")
        if type(seconds) is not int:
            seconds = 720 if platform == "bilibili" else 120
        sequence = [*(f"note:{i}" for i in range(len(chunks))), *(f"asset:{i}" for i in range(len(assets)))]
        created = run_stage("editorial_create", day, output_dir, platform, {
            "anchor": {"kind": "custom", "title": title, "url": ""},
            "source_ids": [],
            "target_seconds": seconds,
            "brief": {"notes": chunks, "asset_paths": assets, "sequence": sequence, "focus_id": "note:0"},
        }, now=now)
        return {"id": created["id"], "day": day.isoformat(), "platform": platform}
    if stage == "workspace_quality_inspect":
        if platform not in PLATFORM_NAMES:
            raise ValueError("请选择作品平台。")
        from core.quality import inspect_item
        from core.workflow import _update_item

        path = edition_path(output_dir, day)
        item = read_edition(path).get("items", {}).get(platform, {})
        if not item:
            raise ValueError("未找到该平台的工作稿。")
        report = inspect_item(item, platform)
        _update_item(path, platform, production_audit=report)
        return report
    if stage == "workspace_quality_decide":
        if platform not in PLATFORM_NAMES:
            raise ValueError("请选择作品平台。")
        from core.quality import dispatch_quality_notice, inspect_item, resolve_notices
        from core.workflow import _edit_edition

        decision = payload.get("decision")
        if decision not in {"approved", "rejected"}:
            raise ValueError("请选择质检结论。")
        reason = (payload.get("reason") or "").strip()
        if len(reason) > 500:
            raise ValueError("原因说明不超过 500 字。")
        path = edition_path(output_dir, day)
        snapshot_item = read_edition(path).get("items", {}).get(platform)
        if not snapshot_item:
            raise ValueError("未找到该平台的工作稿。")
        package = snapshot_item.get("package") or {}
        title = package.get("title") or (snapshot_item.get("story") or {}).get("title") or platform
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        receipt = None
        if decision == "rejected":
            final_reason = reason or "审片认为当前版本画面、节奏或内容不合适，需退回调整。"
            existing_report = inspect_item(snapshot_item, platform)
            findings = [final_reason, *(f for f in existing_report.get("findings", ()) if f != final_reason)]
            receipt = dispatch_quality_notice(
                output_dir, day.isoformat(), platform, title, findings,
                source="manual", package=package if package.get("video") else None,
                fingerprint=snapshot_item.get("production_render_key"),
            )

            def mark_rejected(edition):
                current = edition["items"][platform]
                current["quality_review"] = {"decision": "rejected", "reason": final_reason, "updated_at": stamp}
                if (current.get("package") or {}).get("video"):
                    current["held_video"] = current["package"]["video"]
                current["error"] = final_reason
                current["quality_notice"] = receipt

            updated = _edit_edition(path, mark_rejected)["items"][platform]
        else:
            resolve_notices(output_dir, day=day.isoformat(), platform=platform)

            def mark_approved(edition):
                current = edition["items"][platform]
                current["quality_review"] = {"decision": "approved", "reason": reason, "updated_at": stamp}
                current["held_video"] = None
                current["error"] = None

            updated = _edit_edition(path, mark_approved)["items"][platform]
        report = inspect_item(updated, platform)
        return {"inspection": report, "receipt": receipt}
    if stage == "workspace_notice_dismiss":
        from core.quality import list_notices, resolve_notices
        resolve_notices(output_dir, notice_id=payload.get("id"))
        return {"notices": list_notices(output_dir, limit=10)}
    if stage in {"workspace_webhook_get", "workspace_webhook_save", "workspace_webhook_test"}:
        from core.accounts import unprotect_json
        from core.quality import load_webhook, save_webhook, send_webhook, webhook_summary

        if stage == "workspace_webhook_get":
            return webhook_summary(data_dir)
        if stage == "workspace_webhook_save":
            if payload.get("clear") is True:
                return save_webhook(data_dir, "", enabled=False)
            unsealed = unprotect_json(payload["sealed"])
            return save_webhook(data_dir, unsealed.get("url", ""),
                                enabled=payload.get("enabled") is not False,
                                label=payload.get("label", ""))
        url = ""
        if payload.get("sealed"):
            url = unprotect_json(payload["sealed"]).get("url", "")
        if not url:
            url = load_webhook(data_dir)["url"]
        send_webhook(url, "【内容工作台 · 通知联通测试】", [
            f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}",
            "说明：当成片自动质检不过关或审片退回时，工作台会把问题原因直接推送到这里，无需申请社交平台开放 API。",
        ])
        return {"ok": True}
    if stage == "workspace_media_preview":
        from core.material_search import save_material
        from core.source_media import save_source_media
        from core.news_media import preview_video_asset
        asset = (save_source_media(output_dir, payload.get("source_report_id"), payload["source_media_id"])
                 if payload.get("source_media_id") else save_material(output_dir, payload.get("page_id")))
        return {"video": preview_video_asset(asset), "cover": asset.get("preview", "")}
    if stage in {"workspace_source_media", "workspace_source_media_save"}:
        from core.source_media import source_options, save_source_media

        if stage == "workspace_source_media_save":
            return save_source_media(output_dir, payload.get("report_id"), payload.get("media_id"))
        report, assets = source_options(output_dir, payload.get("report_id"))
        return {"title": report["title"], "items": assets, "provider_name": "所选原文"}
    if stage in {"workspace_media_search", "workspace_media_library", "workspace_media_save"}:
        from core.material_search import saved_materials, save_material, search_materials

        if stage == "workspace_media_search":
            return search_materials(payload.get("query"), payload.get("kind"))
        if stage == "workspace_media_library":
            return {"items": saved_materials(output_dir)}
        return save_material(output_dir, payload.get("page_id"))
    if stage == "workspace_account_matrix":
        return _account_matrix(data_dir, payload.get("include_team") is not False)
    if stage == "workspace_matrix_account_save":
        platform, label = payload.get("platform"), payload.get("label")
        if platform == "douyin":
            if not isinstance(label, str):
                raise ValueError("请输入账号备注。")
            profile = save_profile(data_dir, label, profile_id=payload.get("profile_id"))
            return {"id": _account(platform, "browser", profile, label)["key"], "profile_id": profile}
        return save_local_account(data_dir, platform, label, account_id=payload.get("id"))
    if stage in {"workspace_works", "workspace_work_recycle", "workspace_work_empty"}:
        path = edition_path(output_dir, day)
        try:
            states = _release_rows(path, read_edition(path).get("items", {}))
        except (AttributeError, OSError, TypeError, ValueError):
            states = {}
        albums = load_works(output_dir, day=day, release_rows=states)
        recycle_dir = output_dir / ".works-recycle"
        cleared = (_cleared_works(recycle_dir) if stage == "workspace_works" else
                   _change_recycle(recycle_dir, stage, payload, {work.id for album in albums for work in album.works}))
        active, recycled = [], []
        for album in albums:
            visible = []
            for work in album.works:
                if work.id in cleared:
                    continue
                row = asdict(work)
                if (recycle_dir / work.id).is_file():
                    recycled.append({**row, "topic": album.title})
                else:
                    visible.append(row)
            if visible:
                active.append({"title": album.title, "works": visible})
        return {"albums": active, "recycled": recycled}
    if stage == "workspace_metrics_import":
        accounts, _ = _known_accounts(data_dir, strict=True)
        identities = {(row["platform_name"], row["label"]): row["key"] for row in accounts if row["importable"]}
        imported = import_metrics(data_dir, payload["path"], identities, day=date)
        accounts, metrics_error = _metrics_rows(data_dir, date, accounts)
        return {"imported": imported, "accounts": accounts, "metrics_error": metrics_error}
    if stage == "workspace_metrics_template":
        write_template(payload["path"])
        return {"path": str(Path(payload["path"]).resolve())}
    if stage == "workspace_profile_save":
        label = payload["label"].strip()
        profile_id = save_profile(data_dir, label, profile_id=payload.get("profile_id"))
        return {"profile_id": profile_id, "label": label}
    if stage == "workspace_verify":
        return {"ffmpeg": _verify_ffmpeg()}
    raise ValueError(f"无法识别工作台操作：{stage}")
