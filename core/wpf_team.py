"""Bridge between the native team pages and the authenticated team clients."""

import hashlib
import json
import re
import time
from pathlib import Path

from core.accounts import unprotect_json
from core.operations_client import JOB_MODELS, MAX_RESULT, OperationsClient, OperationsRunner
from core.team_client import MAX_VIDEO_BYTES, TeamClient, TeamError
from core.workflow import _edit_edition, _lock, edition_path, read_edition, write_json


CLIENTS = {"operations": OperationsClient, "review": TeamClient}
REVIEW_DECISIONS = {"comment", "changes_requested", "approved"}


def _load(desk, root):
    try:
        return CLIENTS[desk].load(root)
    except (KeyError, OSError, TypeError, ValueError, RuntimeError):
        raise RuntimeError("无法读取团队连接，请重新连接。") from None


def _required(desk, root):
    client = _load(desk, root)
    if client is None:
        raise ValueError("请先设置此工作区的团队连接。")
    return client


def _connect(root, payload):
    desk = payload.get("desk")
    if desk not in CLIENTS:
        raise ValueError("请选择要连接的团队工作区。")
    try:
        token = unprotect_json(payload["sealed"])["token"]
    except (KeyError, OSError, TypeError, ValueError, RuntimeError):
        raise RuntimeError("无法读取这次输入的连接码，请重新填写。") from None
    address = payload.get("address", "")
    if not isinstance(token, str) or not isinstance(address, str):
        raise ValueError("请填写服务地址和个人连接码。")
    client = CLIENTS[desk](address, token)
    if desk == "review":
        client.review_page()
    else:
        client.snapshot()
    client.save(root)
    return {"address": client.address}


def _release(output, day, platform):
    item = read_edition(edition_path(output, day))["items"].get(platform, {})
    package = item.get("package") or {}
    if (not package.get("title") or not package.get("video") or
            item.get("edit_revision") != item.get("package_revision") or
            item.get("held_video") == package["video"]):
        raise ValueError("请从作品库选择一份已导出且可发布的当前成片。")
    path = Path(package["video"]).resolve()
    if not path.is_relative_to(output) or path.suffix.lower() != ".mp4" or not path.is_file():
        raise ValueError("找不到当前成片的本地 MP4。")
    stat = path.stat()
    if not 0 < stat.st_size <= MAX_VIDEO_BYTES:
        raise ValueError("团队审片需要不超过 2 GiB 的 MP4。")
    share = {"title": package["title"], "caption": package.get("caption") or "", "video": str(path)}
    fingerprint = (item.get("id"), item.get("package_revision"), share, stat.st_size, stat.st_mtime_ns)
    version = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    return share, {"title": share["title"], "caption": share["caption"], "version": version, "bytes": stat.st_size}


def _results(root, client, member):
    scope = hashlib.sha256(f"{client.address}\0{member}".encode()).hexdigest()
    return root / "operations" / "results" / scope


def _archived(path):
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
        if (re.fullmatch(r"[a-f0-9]{32}", path.stem) and item["id"] == path.stem
                and item["kind"] in JOB_MODELS and item["state"] == "completed"
                and isinstance(item["created_at"], str) and isinstance(item["result"], str)
                and 0 < len(item["result"]) <= MAX_RESULT):
            return item
    except (KeyError, OSError, TypeError, ValueError):
        pass
    return None


def _operations(root, client):
    snapshot = client.snapshot()
    directory = _results(root, client, snapshot["member"]["name"])
    jobs = {item["id"]: item for item in snapshot["jobs"]}
    for path in directory.glob("*.json"):
        item = _archived(path)
        if item:
            jobs.setdefault(item["id"], {key: item[key] for key in ("id", "kind", "state", "created_at")})
            jobs[item["id"]]["archived"] = True
    snapshot["jobs"] = sorted(jobs.values(), key=lambda item: item["created_at"], reverse=True)
    return {"address": client.address, **snapshot}


def _compute_job(root, client, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("计算任务编号无效。")
    member = client.snapshot()["member"]["name"]
    directory = _results(root, client, member)
    path = directory / f"{job_id}.json"
    saved = _archived(path)
    if saved:
        return {**saved, "archived": True}
    result = client.job(job_id)
    if result.get("id") != job_id:
        raise TeamError("团队服务返回了不同的计算任务。")
    text = result.get("result")
    if result.get("state") == "completed":
        if not isinstance(text, str) or not 0 < len(text) <= MAX_RESULT:
            raise TeamError("团队服务返回了无效的计算结果。")
        entry = {key: result[key] for key in ("id", "kind", "state", "created_at", "updated_at", "result")}
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with _lock(directory / ".writes.sqlite3", timeout=5):
                write_json(path, entry)
        except (OSError, RuntimeError):
            return {**result, "archive_error": "结果已取回，本机保存失败。"}
        result["archived"] = True
    return result


def _publication_prepared(root, platform, payload):
    title, label = payload.get("title"), payload.get("account_label")
    client = _load("operations", root) if platform and title and label else None
    if not client:
        return {"changed": False}
    snapshot = client.snapshot()
    member = snapshot["member"]["name"]
    matches = [row for row in snapshot["publications"]
               if (row["owner"], row["platform"], row["account_label"], row["title"]) == (member, platform, label, title)
               and row["status"] in {"planned", "failed"}]
    if len(matches) != 1:
        return {"changed": False}
    item = matches[0]
    client.set_publication(item["id"], item["revision"], "prepared")
    return {"changed": True}


def _run_compute(root, client):
    directory = root / "operations"
    directory.mkdir(parents=True, exist_ok=True)
    with _lock(directory / ".worker.sqlite3"):
        runner = OperationsRunner()
        # A desktop worker is short-lived. Keep the runner in this process long enough
        # to retry a lost completion with the same lease.
        for delay in (15, 30, 0):
            try:
                return {"worked": runner.run_once(client, enabled=True)}
            except TeamError as error:
                if not delay or error.status is not None and error.status != 429 and error.status < 500:
                    raise
                time.sleep(delay)


def team_stage(stage, day, output_dir, platform=None, payload=None, *, now=None):
    output = Path(output_dir).resolve()
    root = output.parent
    payload = payload or {}

    if stage == "team_connect":
        return _connect(root, payload)
    if stage == "team_publication_prepared":
        return _publication_prepared(root, platform, payload)
    if stage in {"team_share_preview", "team_share"}:
        package, preview = _release(output, day, platform)
        if stage == "team_share_preview":
            return preview
        if preview["version"] != payload.get("version"):
            raise ValueError("成片或配文已更新，请重新确认后送审。")
        client = _required("review", root)
        review_id = client.share(platform, package, previous_id=payload.get("previous_id"),
                                 resume_dir=root / "team-cache" / "uploads")
        return {"id": review_id}
    if stage == "team_flag":
        reason = payload.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 500:
            raise ValueError("标记说明不超过 500 字。")
        package, preview = _release(output, day, platform)
        if preview["version"] != payload.get("version"):
            raise ValueError("成片或配文已更新，请重新确认后标记。")
        from core.quality import dispatch_quality_notice

        findings = [reason.strip()] if reason.strip() else ["作者复核后认为这版成片不合适。"]
        receipt = dispatch_quality_notice(output, str(day), platform, package["title"], findings,
                                          source="manual", package=package)
        path = edition_path(output, day)

        def hold(edition):
            current = edition["items"][platform]
            if (current.get("package") or {}).get("video") != package["video"]:
                raise RuntimeError("成片已更换，请重新检查。")
            current["held_video"] = package["video"]
            current["quality_review"] = {"decision": "rejected", "reason": findings[0]}
            current["quality_notice"] = {**receipt, "manual": True}

        _edit_edition(path, hold)
        return {"id": receipt.get("review_id") or receipt.get("notice_id"), "receipt": receipt}
    if stage == "team_reviews":
        client = _load("review", root)
        if not client:
            return {}
        items, cursor = client.review_page(payload.get("search", ""), payload.get("status", ""),
                                          payload.get("before", ""))
        return {"address": client.address, "items": items, "next_cursor": cursor}
    if stage == "team_ops":
        client = _load("operations", root)
        return _operations(root, client) if client else {}
    if stage in {"team_review", "team_review_video", "team_feedback"}:
        client = _required("review", root)
        review_id = payload.get("id")
        if stage == "team_review":
            return client.review(review_id)
        if stage == "team_review_video":
            return str(client.download(client.review(review_id), root / "team-cache"))
        decision, text = payload.get("decision"), payload.get("text", "")
        if decision not in REVIEW_DECISIONS or not isinstance(text, str) or len(text) > 2000:
            raise ValueError("请选择审核决定并填写不超过 2,000 字的意见。")
        if decision != "approved" and not text.strip():
            raise ValueError("请先填写审片意见。")
        return client.feedback(review_id, payload.get("revision"), decision, text.strip())
    if stage == "team_compute_once" and payload.get("enabled") is not True:
        return {"worked": False}
    client = _required("operations", root)
    if stage == "team_account_add":
        return client.add_account(platform, payload.get("label"))
    if stage == "team_publication_add":
        return client.add_publication(payload.get("account_id"), payload.get("title"))
    if stage == "team_publication_update":
        return client.set_publication(payload.get("id"), payload.get("revision"), payload.get("status"))
    if stage == "team_compute_submit":
        return client.submit_job(payload.get("id"), payload.get("kind"), payload.get("summary"))
    if stage == "team_compute_job":
        return _compute_job(root, client, payload.get("id"))
    if stage == "team_compute_once":
        return _run_compute(root, client)
    raise ValueError("未知的团队操作。")
