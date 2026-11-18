"""Local account and publication operations for the native Windows workbench."""

import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from config import BASE_DIR, CREATOR_URLS, DATA_DIR, PROFILES
from core.accounts import PLATFORMS, account_hint, application_fields, begin_login, finish_login, save_application, unprotect_json
from core.browser_post import BROWSER_POST_PLATFORMS, prepare_browser_post
from core.douyin_profiles import profile_directory
from core.matrix_accounts import PERSONAL_POST_PLATFORMS, personal_profiles
from core.workflow import _lock, edition_path, read_edition, release_directory, run_stage, write_json


def _identity_key(identity):
    return hashlib.sha256(identity.encode()).hexdigest() if identity else ""


def _preferred_profiles(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _find_note(day, output_dir, note_id, *, now=None):
    from core.wpf_workspace import workspace_stage

    albums = workspace_stage("workspace_works", day, output_dir, None, {}, now=now)["albums"]
    return next((work for album in albums for work in album["works"]
                 if work["id"] == note_id and work["platform"] == "xiaohongshu" and work["kind"] == "image"), None)


def _available_release_days(output_dir):
    days = []
    for candidate in sorted(Path(output_dir).glob("daily/*/edition.json"), reverse=True):
        try:
            items = read_edition(candidate).get("items", {})
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if any(Path((entry.get("package") or {}).get("video", "")).is_file() for entry in items.values() if isinstance(entry, dict)):
            days.append(candidate.parent.name)
    return days


def _release_list(day, output_dir, note_id=None, *, now=None, auto_fallback=False):
    from core.quality import inspect_item

    available_days = _available_release_days(output_dir)
    active_day = str(day)
    path = edition_path(output_dir, active_day)
    edition = read_edition(path)
    has_day_video = any(Path((edition["items"].get(p, {}).get("package") or {}).get("video", "")).is_file() for p in PROFILES)
    if auto_fallback and not has_day_video and not note_id and available_days:
        active_day = available_days[0]
        path = edition_path(output_dir, active_day)
        edition = read_edition(path)
    studio = Path(os.environ.get("ACF_STUDIO_ROOT") or (Path(sys.executable).parent if getattr(sys, "frozen", False) else BASE_DIR))
    extension_dir = str((studio / "browser_extension").resolve())
    rows = []
    for platform in PROFILES:
        item = edition["items"].get(platform, {})
        package = item.get("package") or {}
        video = package.get("video", "")
        if not video or not Path(video).is_file():
            continue
        directory = release_directory(path, platform, item)
        submitted = (directory / "submitted.txt").is_file()
        uncertain = (directory / "submitting.txt").exists()
        current = item.get("edit_revision") == item.get("package_revision")
        held = item.get("held_video") == video
        account, identity = account_hint(platform) if platform == "bilibili" else ("", None)
        eligible = current and not held and not submitted and not uncertain
        inspection = inspect_item(item, platform)
        audit_failed = not inspection.get("ready", True)
        state = ("已提交审核" if submitted else "提交待核对" if uncertain else
                 "草稿已更新" if not current else
                 "质检未通过 · 已暂缓" if held and audit_failed else
                 "已暂缓" if held else
                 "可直投发布" if platform in BROWSER_POST_PLATFORMS else "待手动投稿")
        notice = item.get("quality_notice") or {}
        rows.append({
            "day": active_day,
            "platform": platform, "title": package.get("title") or item.get("story", {}).get("title") or Path(video).stem,
            "video": video, "cover": package.get("cover", ""),
            "duration": package.get("duration"),
            "caption": package.get("caption", ""), "state": state, "account": account,
            "identity_key": _identity_key(identity), "held": held, "can_hold": not submitted and not uncertain,
            "can_fill": eligible and platform in BROWSER_POST_PLATFORMS,
            "can_submit": eligible and bool(identity), "can_review": current and not held,
            "audit_failed": audit_failed,
            "audit_message": inspection.get("message", "") if audit_failed else "",
            "inspection": inspection,
            "noticed": bool(notice.get("notice_id") or notice.get("review_id")),
            "notice_channels": notice.get("channels") or [],
            "creator_url": CREATOR_URLS.get(platform, ""),
            "extension_dir": extension_dir,
        })
    if note_id:
        note = _find_note(active_day, output_dir, note_id, now=now)
        if note:
            draft = note["draft"]
            rows.append({"day": active_day, "platform": "xiaohongshu", "note_id": note_id, "title": note["title"],
                         "caption": draft.get("caption") or draft.get("script") or "",
                         "state": "图文 · 可直投发布", "can_fill": True,
                         "creator_url": CREATOR_URLS.get("xiaohongshu", "")})
    profiles = {platform: personal_profiles(output_dir.parent, platform) for platform in PERSONAL_POST_PLATFORMS}
    saved = _preferred_profiles(output_dir.parent / "accounts" / "posting-profiles.json")
    preferred = {platform: profile_id for platform, profile_id in saved.items()
                 if isinstance(profile_id, str) and profile_id in profiles.get(platform, {})}
    return {"rows": rows, "active_day": active_day, "available_days": available_days,
            "personal_profiles": profiles, "profiles": profiles["douyin"],
            "preferred_profiles": preferred}


def _release_all(day, output_dir, note_id=None, *, now=None):
    """Every exported package across days, newest first, for the one-button posting list."""
    days = _available_release_days(output_dir)[:30]
    if note_id and str(day) not in days:
        days.insert(0, str(day))
    listing, rows = None, []
    for current in days:
        page = _release_list(current, output_dir, note_id if current == str(day) else None, now=now)
        rows.extend(page["rows"])
        listing = listing or page
    return {**(listing or _release_list(day, output_dir, now=now)), "rows": rows}


@contextmanager
def _personal_panel(root, platform, profile_id, owner):
    if type(owner) is not int or not 0 < owner < 2**63:
        raise ValueError("找不到工作台窗口，无法打开投稿面板。")
    profiles = personal_profiles(root, platform)
    if not isinstance(profile_id, str) or profile_id not in profiles:
        raise ValueError("请重新选择这个平台的本机账号。")
    profile = (profile_directory(root, profile_id) if platform == "douyin" else
               root / "browser" / "xiaohongshu-accounts" / profile_id)
    label = profiles[profile_id]
    profile.mkdir(parents=True, exist_ok=True)
    studio = Path(os.environ.get("ACF_STUDIO_ROOT") or (Path(sys.executable).parent if getattr(sys, "frozen", False) else BASE_DIR))
    extension = studio / "browser_extension"
    helper = (BASE_DIR if getattr(sys, "frozen", False) else BASE_DIR / "build") / "post_panel" / "PostingPanel.exe"
    if not helper.is_file() or not (extension / "manifest.json").is_file():
        raise RuntimeError("找不到创作者面板或投稿助手，请完整解压工作台。")
    with _lock(profile.with_suffix(".panel.sqlite3"), timeout=0.1):
        def open_panel(request, **draft):
            preferred = root / "accounts" / "posting-profiles.json"
            preferred.parent.mkdir(parents=True, exist_ok=True)
            with _lock(preferred.with_suffix(".sqlite3"), timeout=5):
                saved = _preferred_profiles(preferred)
                if saved.get(platform) != profile_id:
                    write_json(preferred, {**saved, platform: profile_id})
            process = subprocess.Popen(
                [str(helper), str(owner), str(profile), str(extension), label, platform],
                stdin=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with process.stdin as channel:
                channel.write(f"{request}\n{json.dumps(draft)}\n".encode("ascii"))
            return process

        yield open_panel, label


def publish_stage(stage, day, output_dir, platform, payload, *, now=None):
    output_dir = Path(output_dir).resolve()
    if output_dir.parent != DATA_DIR.resolve():
        raise ValueError("投稿操作必须使用当前用户的工作台目录。")
    payload = payload or {}
    if stage == "publish_list" and payload.get("all") is True:
        return _release_all(day, output_dir, payload.get("note_id"), now=now)
    if stage == "publish_list":
        return _release_list(day, output_dir, payload.get("note_id"), now=now,
                             auto_fallback=payload.get("auto_fallback") is True)
    if stage == "publish_personal_profiles":
        return personal_profiles(output_dir.parent, platform)
    if stage == "publish_account_info":
        if platform not in PLATFORMS:
            raise ValueError("请选择支持开放平台授权的账号。")
        client, redirect, partition = application_fields(platform)
        label, identity = account_hint(platform)
        return {"client": client, "redirect": redirect, "partition": partition,
                "account": label, "authorized": bool(identity)}
    if stage == "publish_platform_works":
        if platform not in PLATFORMS:
            raise ValueError("请选择支持读取作品的开放平台。")
        label, identity = account_hint(platform)
        if not identity:
            raise ValueError(f"{label}，请先完成开放平台授权。")
        key = _identity_key(identity)
        cursor = payload.get("cursor", 0) if payload.get("identity_key") == key else 0
        result = run_stage("videos", day, output_dir, platform, {"cursor": cursor, "expected_account": identity}, now=now)
        if account_hint(platform)[1] != identity:
            raise RuntimeError("账号已切换，请重新读取作品。")
        return {**result, "account": label, "identity_key": key}
    if stage == "publish_application_save":
        if platform not in PLATFORMS:
            raise ValueError("请选择支持开放平台授权的账号。")
        save_application(platform, **unprotect_json(payload["sealed"]))
        return True
    if stage == "publish_authorize_start":
        if platform not in PLATFORMS:
            raise ValueError("请选择支持开放平台授权的账号。")
        return begin_login(platform)
    if stage == "publish_authorize_finish":
        if platform not in PLATFORMS:
            raise ValueError("请选择支持开放平台授权的账号。")
        finish_login(platform, **unprotect_json(payload["sealed"]))
        label, identity = account_hint(platform)
        return {"account": label, "authorized": bool(identity)}
    if stage in {"publish_douyin_login", "publish_personal_login"}:
        platform = "douyin" if stage == "publish_douyin_login" else platform
        with _personal_panel(output_dir.parent, platform, payload["profile_id"], payload["owner"]) as (open_panel, _):
            return open_panel("login").wait() == 0
    if stage == "publish_personal_note":
        if platform != "xiaohongshu":
            raise ValueError("请选择小红书图文笔记。")
        note = _find_note(day, output_dir, payload.get("id"), now=now)
        if note is None:
            raise ValueError("找不到这份图文笔记，请刷新作品库。")
        draft = note["draft"]
        package = {"title": note["title"], "caption": draft.get("caption") or draft.get("script") or "",
                   "pictures": [picture["file"] for picture in note["pictures"]]}
        with _personal_panel(output_dir.parent, platform, payload["profile_id"], payload["owner"]) as (open_panel, label):
            status = prepare_browser_post(platform, package, timeout=660, open_panel=open_panel)
            return {"status": status, "account": label}
    if stage == "publish_prepare":
        if platform in PERSONAL_POST_PLATFORMS:
            with _personal_panel(output_dir.parent, platform, payload["profile_id"], payload["owner"]) as (open_panel, label):
                status = run_stage("browser_post", day, output_dir, platform, payload, now=now, open_post_panel=open_panel)
                return {"status": status, "account": label}
        status = run_stage("browser_post", day, output_dir, platform, payload, now=now)
        return {"status": status}
    if stage == "publish_submit":
        if platform not in PLATFORMS:
            raise ValueError("该平台请在投稿页手动发布。")
        _, identity = account_hint(platform)
        if not identity or _identity_key(identity) != payload.get("identity_key"):
            raise ValueError("投稿账号已变化，请刷新发布中心并重新核对。")
        return run_stage("publish", day, output_dir, platform, {"account": identity, "video": payload["video"]}, now=now)
    raise ValueError(f"无法识别账号或投稿操作：{stage}")
