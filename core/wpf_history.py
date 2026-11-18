"""Switch saved creations under the same locks used by production jobs."""

import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path

from config import PLATFORM_PRESETS
from core.workflow import _edit_edition, _lock, edition_path, read_edition
from core.works_library import _file_candidates, _video, cleared_work_ids, video_work_id


STAGES = ("media", "import_media", "ai", "preview", "render")
BUSY_MESSAGE = "这份作品正在由后台处理，暂时不能编辑或切换版本。可以查看其他作品，任务结束后会自动恢复操作。"


def _key(item):
    return hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode()).hexdigest() if item else ""


def _paths(path, platform):
    return [path.parent / f"{platform}-selection.sqlite3",
            *(path.parent / platform / f"{stage}.sqlite3" for stage in STAGES)]


def _busy(path, platform):
    for lockfile in _paths(path, platform):
        if not lockfile.exists():
            continue
        # Roll back a lock probe; never create a database or change its contents.
        with closing(sqlite3.connect(lockfile.as_uri() + "?mode=rw", uri=True, timeout=0)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY:
                    return True
                raise
            finally:
                connection.rollback()
    return False


def creation_state(output_dir, day, platform):
    if platform not in PLATFORM_PRESETS:
        raise ValueError("请选择作品平台。")
    from core.quality import inspect_item

    path = edition_path(output_dir, day).resolve()
    item = read_edition(path).get("items", {}).get(platform, {})
    busy = _busy(path, platform)
    recent = []
    if not item:
        recent = [row for row in creation_list(output_dir).get("rows", ()) if row.get("active")][:6]
    return {
        "item": item,
        "inspection": inspect_item(item, platform) if item else {},
        "recent": recent,
        "busy": busy,
        "message": BUSY_MESSAGE if busy else "",
    }


def _creation_artifacts(root, path, platform, item, cleared):
    """Use the same catalogue eligibility when listing and when restoring a saved selection."""
    recycle = root / ".works-recycle"
    def removed(video):
        identifier = video_work_id(root, video)
        return identifier in cleared or (recycle / identifier).is_file()

    outputs = [(field, work) for field in ("package", "full_preview", "preview")
               if (work := _video(platform, "preview", "", path.parent.name, item.get(field), path.parent, ""))]
    visible = [(field, work) for field, work in outputs if not removed(work.package["video"])]
    has_draft = any(item.get(field) for field in ("edit", "suggestion", "editorial", "chapters"))
    hidden = False
    if not visible and not has_draft:
        saved_paths = [(item.get(field) or {}).get("video") for field in ("package", "full_preview", "preview")]
        saved_paths = [value for value in saved_paths if isinstance(value, str) and value]
        hidden = bool(outputs) or bool(saved_paths) and all(
            any(removed(candidate) for candidate in _file_candidates(path.parent, stored)) for stored in saved_paths)
    state = ("已有成片" if any(field == "package" for field, _ in visible) else
             "已有试剪" if visible else "仅有草稿" if has_draft else "视频文件缺失")
    return state, hidden


def creation_list(output_dir):
    rows, errors = [], []
    root = Path(output_dir).resolve()
    cleared = cleared_work_ids(root / ".works-recycle")
    for path in sorted(Path(output_dir).resolve().glob("daily/*/edition.json"), reverse=True):
        try:
            day = dt.date.fromisoformat(path.parent.name).isoformat()
            edition = read_edition(path)
            for platform in PLATFORM_PRESETS:
                current = edition.get("items", {}).get(platform, {})
                saved = edition.get("previous_items", {}).get(platform, [])
                try:
                    busy = _busy(path, platform)
                except sqlite3.Error as error:
                    errors.append(f"{day} · {platform}：无法确认任务状态（{error}）")
                    continue
                for item, active in [(current, True), *((item, False) for item in reversed(saved))]:
                    title = item.get("story", {}).get("title", "").strip()
                    if not title or not any(item.get(field) for field in ("edit", "suggestion", "editorial", "chapters", "package", "preview", "full_preview")):
                        continue
                    artifact_state, hidden = _creation_artifacts(root, path, platform, item, cleared)
                    # Cleared legacy exports have no editable draft to restore. Keep real drafts and running jobs.
                    if hidden and not busy:
                        continue
                    rows.append({"key": _key(item), "current_key": _key(current), "day": day,
                                 "id": item.get("id"),
                                 "platform": platform, "title": title, "active": active,
                                 "busy": busy, "state": "当前工作稿" if active else "历史创作",
                                 "artifact_state": artifact_state,
                                 "scenes": len((item.get("edit") or item.get("suggestion") or {}).get("scenes", []))})
        except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error) as error:
            errors.append(f"{path.parent.name}：无法读取创作记录（{error}）")
    return {"rows": rows, "errors": errors}


def open_creation(output_dir, day, platform, payload):
    if platform not in PLATFORM_PRESETS or not isinstance(payload.get("key"), str):
        raise ValueError("请选择一份已保存的创作。")
    path = edition_path(output_dir, day).resolve()
    if not path.is_file():
        raise ValueError("这份创作已不存在，请刷新列表。")
    edition = read_edition(path)
    current = edition.get("items", {}).get(platform, {})
    if (_key(current) == payload["key"] or payload.get("active") is True
            and payload.get("id") and payload["id"] == current.get("id")):
        # Viewing an already-current creation is allowed while it is generating.
        return {"day": str(day), "platform": platform, "restored": False}
    (path.parent / platform).mkdir(exist_ok=True)
    with ExitStack() as claims:
        try:
            for lockfile in _paths(path, platform):
                claims.enter_context(_lock(lockfile))
        except RuntimeError as error:
            raise RuntimeError(BUSY_MESSAGE) from error
        # Catalogue writes use this same lock and never acquire production or edition locks.
        # Hold it through the edition commit so a stale picker cannot race a recycle operation.
        root = Path(output_dir).resolve()
        recycle = root / ".works-recycle"
        recycle.mkdir(parents=True, exist_ok=True)
        claims.enter_context(_lock(recycle / ".writes.sqlite3", timeout=5))

        def switch(edition):
            current = edition.get("items", {}).get(platform, {})
            if _key(current) != payload.get("current_key"):
                raise ValueError("当前工作稿已变化，请刷新列表后重新选择。")
            saved = edition.get("previous_items", {}).get(platform, [])
            index = next((i for i, item in enumerate(saved) if _key(item) == payload["key"]), None)
            if index is None:
                raise ValueError("这份历史创作已变化，请刷新列表。")
            if _creation_artifacts(root, path, platform, saved[index], cleared_work_ids(recycle))[1]:
                raise ValueError("这份创作已从作品库移除，请刷新列表后选择其他作品。")
            selected = saved.pop(index)
            if current:
                saved.append(current)
            edition.setdefault("items", {})[platform] = selected

        _edit_edition(path, switch)
    return {"day": str(day), "platform": platform, "restored": True}
