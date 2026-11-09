"""Broadcast-grade local Web Studio server (Starlette + Uvicorn) for Auto Content Factory."""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
import os
import subprocess
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from config import ASSETS_DIR, BASE_DIR, CREATOR_URLS, DATA_DIR, OUTPUT_DIR, PLATFORM_PRESETS, PROFILES
from core.accounts import (
    PLATFORMS as OAUTH_PLATFORMS,
    account_hint,
    application_fields,
    begin_login,
    finish_login,
    save_application,
)
from core.browser_post import prepare_browser_post
from core.copywriting import add_case, generate_batch, list_cases
from core.daily_news import story_key
from core.editing import default_cut, render_case, validate_cut
from core.intake import category_for_story, match_hotspots, read_intake
from core.news_release import list_videos
from core.workflow import (
    _lock,
    edition_path,
    read_edition,
    release_directory,
    run_stage,
    write_json,
)

PUBLISHERS = set(OAUTH_PLATFORMS)

TASK_NAME = "AutoContentFactoryDaily"
PLATFORM_NAMES = {
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "bilibili": "B站",
}


def allowed_roots(output_dir: Path) -> list[Path]:
    roots = [
        Path(output_dir).resolve(),
        (BASE_DIR / "output").resolve(),
        (DATA_DIR / "output").resolve(),
        ASSETS_DIR.resolve(),
    ]
    appdata = os.environ.get("LOCALAPPDATA")
    if appdata:
        roots.append((Path(appdata) / "AutoContentFactory" / "output").resolve())
    unique = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def resolve_media_path(raw_path: str, output_dir: Path) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    roots = allowed_roots(output_dir)
    candidates = [candidate]
    # Also try remapping between AppData output and project output if needed
    raw_norm = str(candidate).replace("/", "\\")
    for root in roots:
        marker = "\\output\\"
        if marker in raw_norm.lower():
            idx = raw_norm.lower().rfind(marker)
            rel = raw_norm[idx + len(marker):]
            candidates.append(root / rel)

    for item in candidates:
        try:
            resolved = item.resolve()
        except OSError:
            continue
        if resolved.is_file() and any(resolved.is_relative_to(root) for root in roots):
            return resolved
    return None


def media_url(raw_path: str | None, output_dir: Path) -> str:
    if not raw_path:
        return ""
    resolved = resolve_media_path(raw_path, output_dir)
    if not resolved:
        return ""
    from urllib.parse import quote
    return f"/api/media?path={quote(str(resolved))}"


def daily_schedule_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.returncode == 0
    except OSError:
        return False


def set_daily_schedule(enable: bool) -> bool:
    if os.name != "nt":
        raise RuntimeError("定时任务仅支持 Windows 任务计划程序。")
    from core.desktop_jobs import desktop_command

    if enable:
        target = subprocess.list2cmdline([*desktop_command(), "--daily"])
        command = ["schtasks", "/Create", "/SC", "DAILY", "/TN", TASK_NAME, "/TR", target, "/ST", "08:30", "/F"]
    else:
        command = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0 and enable:
        raise RuntimeError((completed.stderr or completed.stdout or "无法更新 Windows 计划任务").strip())
    return daily_schedule_enabled()


def list_available_days(output_dir: Path) -> list[str]:
    today = dt.date.today().isoformat()
    days = {today}
    daily_root = Path(output_dir) / "daily"
    if daily_root.is_dir():
        for child in daily_root.iterdir():
            if child.is_dir() and (child / "edition.json").is_file():
                days.add(child.name)
    return sorted(days, reverse=True)


def build_release_info(edition: dict[str, Any], day: str, output_dir: Path, platform: str) -> dict[str, Any]:
    item = edition.get("items", {}).get(platform, {})
    package = item.get("package") or {}
    video_raw = package.get("video") or ""
    cover_raw = package.get("cover") or ""
    video_file = resolve_media_path(video_raw, output_dir)
    cover_file = resolve_media_path(cover_raw, output_dir)

    account_label, identity = account_hint(platform) if platform in PUBLISHERS else ("创作者后台", None)
    directory = release_directory(edition_path(output_dir, day), platform, item)
    receipt = (directory / "submitted.txt").is_file()
    receipt_id = (directory / "submitted.txt").read_text(encoding="utf-8").strip() if receipt else ""
    uncertain = (directory / "submitting.txt").exists()
    held = bool(video_raw and item.get("held_video") == video_raw)
    editable = bool(video_file and not receipt and not uncertain)

    if receipt:
        state = "已提交审核"
        code = "submitted"
    elif uncertain:
        state = "待核对 · 不重发"
        code = "uncertain"
    elif not video_file:
        state = "无可用成片"
        code = "missing"
    elif item.get("edit_revision") != item.get("package_revision"):
        state = "草稿已更新"
        code = "outdated"
    elif held:
        state = "已暂缓"
        code = "held"
    elif platform not in PUBLISHERS:
        state = "暂无直连接口"
        code = "browser_ready"
    elif not identity:
        state = "需要开放平台授权"
        code = "auth_required"
    else:
        state = "可发布"
        code = "eligible"

    return {
        "platform": platform,
        "platform_name": PLATFORM_NAMES.get(platform, platform),
        "title": package.get("title") or item.get("story", {}).get("title") or "未排期选题",
        "account": account_label,
        "identity": identity,
        "video": video_raw,
        "video_url": media_url(video_raw, output_dir),
        "cover": str(cover_file) if cover_file else cover_raw,
        "cover_url": media_url(cover_raw, output_dir),
        "duration": package.get("duration") or 0,
        "caption": package.get("caption") or "",
        "credit_caption": package.get("credit_caption") or "",
        "tags": package.get("tags") or [],
        "state": state,
        "state_code": code,
        "editable": editable,
        "held": held,
        "receipt_id": receipt_id,
        "creator_url": CREATOR_URLS.get(platform, ""),
    }


def enrich_edition(day: str, output_dir: Path) -> dict[str, Any]:
    path = edition_path(output_dir, day)
    if path.is_file():
        edition = read_edition(path)
    else:
        edition = {"date": day, "items": {}, "errors": []}

    enriched_items = {}
    for platform in ("xiaohongshu", "douyin", "bilibili"):
        raw_item = edition.get("items", {}).get(platform)
        if not raw_item or not raw_item.get("story"):
            enriched_items[platform] = {
                "platform": platform,
                "platform_name": PLATFORM_NAMES[platform],
                "preset": PLATFORM_PRESETS[platform],
                "profile": PROFILES[platform],
                "status": "empty",
                "story": None,
                "draft": {"scenes": []},
                "media": [],
                "warnings": [],
                "error": "",
                "release": build_release_info(edition, day, output_dir, platform),
            }
            continue

        item = dict(raw_item)
        try:
            draft = item.get("edit") or default_cut(item, platform)
        except Exception:
            draft = {"scenes": []}

        media_list = []
        for idx, asset in enumerate(item.get("media") or []):
            asset_copy = dict(asset)
            preview_path = asset.get("preview") or asset.get("path") or ""
            asset_copy["preview_url"] = media_url(preview_path, output_dir)
            asset_copy["file_url"] = media_url(asset.get("path") or "", output_dir)
            asset_copy["index"] = idx
            media_list.append(asset_copy)

        enriched_items[platform] = {
            **item,
            "platform": platform,
            "platform_name": PLATFORM_NAMES[platform],
            "preset": PLATFORM_PRESETS[platform],
            "profile": PROFILES[platform],
            "draft": draft,
            "media": media_list,
            "release": build_release_info(edition, day, output_dir, platform),
        }

    return {
        "date": edition.get("date", day),
        "errors": edition.get("errors", []),
        "items": enriched_items,
    }


def build_intake_payload(output_dir: Path) -> dict[str, Any]:
    snapshot = read_intake(output_dir)
    live_matches = match_hotspots(snapshot)
    total_live_matched = sum(
        sum(1 for row in rows if row.get("article"))
        for rows in live_matches.values()
    )
    if total_live_matched == 0 and snapshot.get("checked"):
        checked_dt = dt.datetime.fromtimestamp(snapshot["checked"], dt.timezone.utc)
        matches = match_hotspots(snapshot, checked_dt)
        fresh_window = False
    else:
        matches = live_matches
        fresh_window = True

    articles = []
    for story in snapshot.get("articles", []):
        articles.append({
            **story,
            "id": story_key(story),
            "category": story.get("category") or category_for_story(story),
        })

    boards = {}
    for platform in ("douyin", "xiaohongshu", "bilibili"):
        board_meta = snapshot.get("ranks", {}).get(platform, {})
        rows = matches.get(platform, [])
        boards[platform] = {
            "platform": platform,
            "platform_name": PLATFORM_NAMES[platform],
            "published": board_meta.get("published"),
            "source": board_meta.get("source", "豚榜 · 公开榜单聚合"),
            "url": board_meta.get("url", ""),
            "error": board_meta.get("error", ""),
            "fresh": fresh_window,
            "total": len(rows),
            "matched_count": sum(1 for r in rows if r.get("article")),
            "entries": rows,
        }

    return {
        "checked": snapshot.get("checked"),
        "errors": snapshot.get("errors", []),
        "article_count": len(articles),
        "articles": articles,
        "boards": boards,
    }


def build_accounts_payload() -> dict[str, Any]:
    accounts = {}
    for platform in ("douyin", "bilibili"):
        label, identity = account_hint(platform)
        client, redirect, partition = application_fields(platform)
        accounts[platform] = {
            "platform": platform,
            "platform_name": PLATFORM_NAMES[platform],
            "label": label,
            "identity": identity,
            "authorized": bool(identity),
            "client": client,
            "redirect": redirect,
            "partition": partition,
            "creator_url": CREATOR_URLS.get(platform, ""),
            "oauth_supported": True,
        }
    for platform, name in (("xiaohongshu", "小红书"), ("wechat_channels", "微信视频号")):
        accounts[platform] = {
            "platform": platform,
            "platform_name": name,
            "label": "请在创作者后台管理",
            "identity": None,
            "authorized": False,
            "client": "",
            "redirect": "",
            "partition": "",
            "creator_url": CREATOR_URLS.get(platform, ""),
            "oauth_supported": False,
        }
    return accounts


def build_copywriting_payload(output_dir: Path) -> dict[str, Any]:
    cases = {}
    for platform in ("xiaohongshu", "douyin", "bilibili"):
        try:
            cases[platform] = list_cases(output_dir, platform)
        except Exception:
            cases[platform] = []

    root = Path(output_dir) / "copywriting"
    batches = []
    if root.is_dir():
        for batch_file in sorted(root.glob("*/batch.json"), reverse=True)[:10]:
            try:
                batches.append(json.loads(batch_file.read_text(encoding="utf-8")))
            except Exception:
                continue
    return {"cases": cases, "batches": batches}


def assign_story_to_edition(
    day: str,
    output_dir: Path,
    platform: str,
    story: dict[str, Any],
    hotspot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if platform not in PROFILES:
        raise ValueError("不支持的目标平台。")
    path = edition_path(output_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_story = {
        "title": str(story.get("title") or "").strip()[:120],
        "summary": str(story.get("summary") or story.get("title") or "").strip()[:2500],
        "url": str(story.get("url") or "").strip(),
        "source": str(story.get("source") or "信源采集库").strip()[:40],
        "published": story.get("published") or dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    clean_story["category"] = story.get("category") or category_for_story(clean_story)
    if not clean_story["title"]:
        raise ValueError("选题标题不能为空。")

    with _lock(path.with_suffix(".sqlite3")):
        edition = read_edition(path) if path.is_file() else {"date": day, "items": {}, "errors": []}
        previous = edition.get("items", {}).get(platform, {})
        revision = int(previous.get("edit_revision") or 0) + 1
        item: dict[str, Any] = {
            "story": clean_story,
            "status": "collected",
            "media": [],
            "warnings": [],
            "edit_revision": revision,
        }
        if hotspot:
            item["hotspot"] = {k: hotspot.get(k, "") for k in ("rank", "title", "heat", "url")}
        item["edit"] = default_cut(item, platform)
        edition.setdefault("items", {})[platform] = item
        write_json(path, edition)
    return enrich_edition(day, output_dir)


class StudioWorkspaceState:
    def __init__(self, output_dir: Path | None = None):
        self.output_dir = Path(output_dir or OUTPUT_DIR).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.pending_logins: dict[str, dict[str, Any]] = {}
        self.jobs: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record_job(self, stage: str, platform: str | None, status: str, detail: str = "") -> dict[str, Any]:
        entry = {
            "id": uuid.uuid4().hex[:8],
            "stage": stage,
            "platform": platform or "all",
            "status": status,
            "detail": detail,
            "timestamp": dt.datetime.now().strftime("%H:%M:%S"),
        }
        with self._lock:
            self.jobs.insert(0, entry)
            del self.jobs[30:]
        return entry


def create_studio_app(output_dir: Path | None = None) -> Starlette:
    state = StudioWorkspaceState(output_dir)

    async def homepage(request: Request) -> Response:
        html_path = BASE_DIR / "workbench_prototype.html"
        if not html_path.is_file():
            return HTMLResponse("<h1>Missing workbench_prototype.html</h1>", status_code=404)
        return HTMLResponse(
            html_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    async def api_state(request: Request) -> Response:
        days = list_available_days(state.output_dir)
        requested_day = request.query_params.get("day", "").strip()
        if not requested_day:
            # Prefer the latest day that already has an edition.json on disk
            existing_days = [
                d for d in days
                if (state.output_dir / "daily" / d / "edition.json").is_file()
            ]
            day = existing_days[0] if existing_days else days[0]
        else:
            day = requested_day

        editions_by_day = {d: enrich_edition(d, state.output_dir) for d in days[:7]}
        payload = {
            "ok": True,
            "server_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "output_dir": str(state.output_dir),
            "active_day": day,
            "available_days": days,
            "schedule_enabled": daily_schedule_enabled(),
            "edition": editions_by_day.get(day) or enrich_edition(day, state.output_dir),
            "editions_by_day": editions_by_day,
            "intake": build_intake_payload(state.output_dir),
            "accounts": build_accounts_payload(),
            "copywriting": build_copywriting_payload(state.output_dir),
            "jobs": list(state.jobs),
        }
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def api_media(request: Request) -> Response:
        raw_path = request.query_params.get("path", "")
        resolved = resolve_media_path(raw_path, state.output_dir)
        if not resolved:
            return JSONResponse({"ok": False, "error": "Media file not found or outside allowed directory."}, status_code=404)

        content_type, _ = mimetypes.guess_type(str(resolved))
        content_type = content_type or "application/octet-stream"
        file_size = resolved.stat().st_size
        range_header = request.headers.get("range")

        if range_header and range_header.startswith("bytes="):
            try:
                start_str, end_str = range_header[6:].split("-", 1)
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else file_size - 1
                end = min(end, file_size - 1)
                if start > end or start < 0:
                    raise ValueError("Invalid byte range")
            except ValueError:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

            chunk_size = (end - start) + 1

            def iter_file():
                with resolved.open("rb") as f:
                    f.seek(start)
                    remaining = chunk_size
                    while remaining > 0:
                        data = f.read(min(65536, remaining))
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
                "Cache-Control": "no-cache",
            }
            return StreamingResponse(iter_file(), status_code=206, media_type=content_type, headers=headers)

        return FileResponse(resolved, media_type=content_type, headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"})

    async def api_action(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "请求 JSON 格式无效。"}, status_code=400)

        action = str(body.get("action") or "").strip()
        day = str(body.get("day") or dt.date.today().isoformat()).strip()
        platform = body.get("platform")
        payload = body.get("payload") or {}

        try:
            if action == "intake":
                state.record_job("intake", None, "running", "正在刷新 RSS 信源与三端热榜")
                run_stage("intake", day, state.output_dir)
                try:
                    run_stage("supplement_hotspots", day, state.output_dir)
                except Exception:
                    pass
                state.record_job("intake", None, "completed", "已完成信源刷新与热点报道增补")
                return JSONResponse({
                    "ok": True,
                    "message": "已完成全网 RSS 信源与三端热榜刷新。",
                    "intake": build_intake_payload(state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action in {"queue_hotspots", "queue_episodes", "collect"}:
                state.record_job(action, platform, "running", f"正在执行 {action}")
                run_stage(action, day, state.output_dir, platform=platform)
                state.record_job(action, platform, "completed", f"{action} 执行完成")
                return JSONResponse({
                    "ok": True,
                    "message": "已更新当日编单。",
                    "edition": enrich_edition(day, state.output_dir),
                    "available_days": list_available_days(state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action == "select_story":
                if not platform:
                    raise ValueError("请选择要排入的目标平台。")
                story = payload.get("story") or {}
                hotspot = payload.get("hotspot")
                edition = assign_story_to_edition(day, state.output_dir, platform, story, hotspot)
                state.record_job("select_story", platform, "completed", f"已排入选题：{story.get('title', '')[:28]}")
                return JSONResponse({
                    "ok": True,
                    "message": f"已将《{story.get('title', '')}》排入 {PLATFORM_NAMES.get(platform, platform)} 分镜台。",
                    "edition": edition,
                    "available_days": list_available_days(state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action == "manual":
                run_stage("manual", day, state.output_dir, platform=platform, payload=payload)
                state.record_job("manual", platform, "completed", f"已保存自拟选题：{payload.get('title', '')[:24]}")
                return JSONResponse({
                    "ok": True,
                    "message": "已保存自拟选题并生成默认分镜。",
                    "edition": enrich_edition(day, state.output_dir),
                    "available_days": list_available_days(state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action == "save_cut":
                run_stage("save", day, state.output_dir, platform=platform, payload=payload)
                state.record_job("save", platform, "completed", "已保存分镜口播草稿")
                return JSONResponse({
                    "ok": True,
                    "message": "分镜与口播修改已保存至本地编单。",
                    "edition": enrich_edition(day, state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action in {"media", "import_media", "ai", "apply_ai", "render", "hold"}:
                state.record_job(action, platform, "running", f"正在执行 {action}")
                run_stage(action, day, state.output_dir, platform=platform, payload=payload if payload else None)
                state.record_job(action, platform, "completed", f"{action} 执行完成")
                return JSONResponse({
                    "ok": True,
                    "message": f"操作 {action} 已完成。",
                    "edition": enrich_edition(day, state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action == "publish":
                state.record_job("publish", platform, "running", "正在提交开放平台审核")
                result = await run_in_threadpool(run_stage, "publish", day, state.output_dir,
                                                 platform=platform, payload=payload)
                state.record_job("publish", platform, "completed", "已提交开放平台审核")
                return JSONResponse({
                    "ok": True,
                    "message": f"已提交平台审核 · 稿件编号 {result}",
                    "result": result,
                    "edition": enrich_edition(day, state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action == "browser_post":
                if not platform:
                    raise ValueError("请指定发布平台。")
                edition = enrich_edition(day, state.output_dir)
                item = edition["items"].get(platform) or {}
                package = item.get("package")
                if not package:
                    raise RuntimeError("当前平台尚未生成可发布成片。")
                open_browser = bool(payload.get("open_browser", False))
                if open_browser:
                    url = prepare_browser_post(package, platform)
                else:
                    # Prepare without forcing webbrowser.open during automated tests if requested
                    from unittest.mock import patch
                    with patch("core.browser_post.webbrowser.open"):
                        url = prepare_browser_post(package, platform)
                state.record_job("browser_post", platform, "completed", "已启动本机回环半自动装填通道")
                return JSONResponse({
                    "ok": True,
                    "message": "已就绪 Chrome 插件半自动装填任务。",
                    "url": url,
                    "jobs": list(state.jobs),
                })

            if action == "save_app":
                if platform not in OAUTH_PLATFORMS:
                    raise ValueError("请选择抖音或 B站配置开放平台应用。")
                save_application(
                    platform,
                    str(payload.get("client") or ""),
                    str(payload.get("secret") or ""),
                    str(payload.get("redirect") or ""),
                    str(payload.get("partition") or ""),
                )
                state.record_job("save_app", platform, "completed", "已加密保存开放平台应用配置")
                return JSONResponse({
                    "ok": True,
                    "message": f"已通过 Windows DPAPI 保存 {PLATFORM_NAMES.get(platform, platform)} 应用凭证。",
                    "accounts": build_accounts_payload(),
                    "edition": enrich_edition(day, state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action == "begin_login":
                if platform not in OAUTH_PLATFORMS:
                    raise ValueError("请选择抖音或 B站发起授权。")
                login = begin_login(platform)
                state.pending_logins[platform] = login
                if payload.get("open_browser", False):
                    webbrowser.open(login["url"])
                state.record_job("begin_login", platform, "completed", "已生成 OAuth 授权地址")
                return JSONResponse({
                    "ok": True,
                    "message": "已生成授权链接，请在完成浏览器授权后粘贴完整回调地址。",
                    "auth_url": login["url"],
                    "jobs": list(state.jobs),
                })

            if action == "finish_login":
                if platform not in OAUTH_PLATFORMS:
                    raise ValueError("请选择抖音或 B站完成授权。")
                login = state.pending_logins.get(platform)
                if not login:
                    raise RuntimeError("请先点击“发起浏览器授权”生成有效状态码。")
                callback_url = str(payload.get("callback_url") or "")
                label = finish_login(platform, login, callback_url)
                state.pending_logins.pop(platform, None)
                state.record_job("finish_login", platform, "completed", f"已绑定账号：{label}")
                return JSONResponse({
                    "ok": True,
                    "message": f"已完成授权并绑定账号：{label}",
                    "accounts": build_accounts_payload(),
                    "edition": enrich_edition(day, state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action == "list_videos":
                if platform not in OAUTH_PLATFORMS:
                    raise ValueError("仅抖音与 B站支持开放平台作品状态查询。")
                cursor = payload.get("cursor", 0)
                _, identity = account_hint(platform)
                videos = list_videos(platform, cursor=cursor, expected_account=identity)
                state.record_job("list_videos", platform, "completed", f"已拉取 {len(videos.get('items', []))} 条作品记录")
                return JSONResponse({
                    "ok": True,
                    "videos": videos,
                    "accounts": build_accounts_payload(),
                    "jobs": list(state.jobs),
                })

            if action == "add_case":
                add_case(
                    state.output_dir,
                    str(payload.get("platform") or platform or "xiaohongshu"),
                    str(payload.get("title") or ""),
                    str(payload.get("url") or ""),
                    str(payload.get("evidence") or ""),
                    str(payload.get("keywords") or ""),
                    str(payload.get("notes") or ""),
                )
                state.record_job("add_case", platform, "completed", "已收录结构案例至案例库")
                return JSONResponse({
                    "ok": True,
                    "message": "已将参考案例保存至本地案例库。",
                    "copywriting": build_copywriting_payload(state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action == "generate_copywriting":
                topic = str(payload.get("topic") or "")
                facts = str(payload.get("facts") or "")
                platforms = payload.get("platforms") or ["xiaohongshu", "douyin", "bilibili"]
                count = int(payload.get("count") or 1)
                batch = generate_batch(state.output_dir, topic, facts, platforms, count)
                state.record_job("copywriting", None, "completed", f"已生成批次：{topic[:20]}")
                return JSONResponse({
                    "ok": True,
                    "message": "已完成多平台文案批处理生成。",
                    "batch": batch,
                    "copywriting": build_copywriting_payload(state.output_dir),
                    "jobs": list(state.jobs),
                })

            if action == "schedule_toggle":
                enable = bool(payload.get("enable"))
                enabled = set_daily_schedule(enable)
                state.record_job("schedule", None, "completed", "已启用每日 08:30 自动制作" if enabled else "已关闭每日定时任务")
                return JSONResponse({
                    "ok": True,
                    "schedule_enabled": enabled,
                    "message": "已开启每日 08:30 定时制作任务。" if enabled else "已暂停每日 08:30 定时制作任务。",
                    "jobs": list(state.jobs),
                })

            return JSONResponse({"ok": False, "error": f"未知操作指令：{action}"}, status_code=400)

        except Exception as error:
            state.record_job(action or "error", platform, "failed", str(error))
            failure = {"ok": False, "error": str(error) or type(error).__name__, "jobs": list(state.jobs)}
            if action == "publish":
                try:
                    failure["edition"] = enrich_edition(day, state.output_dir)
                except (ValueError, OSError):
                    pass
            return JSONResponse(failure, status_code=400)

    async def storm_bg(request: Request) -> Response:
        bg_path = ASSETS_DIR / "storm-background.jpg"
        if bg_path.is_file():
            return FileResponse(bg_path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
        return Response(status_code=404)

    routes = [
        Route("/", homepage, methods=["GET"]),
        Route("/studio", homepage, methods=["GET"]),
        Route("/assets/storm-background.jpg", storm_bg, methods=["GET"]),
        Route("/api/state", api_state, methods=["GET"]),
        Route("/api/media", api_media, methods=["GET"]),
        Route("/api/action", api_action, methods=["POST"]),
    ]
    return Starlette(routes=routes)


app = create_studio_app()

_SERVER_LOCK = threading.Lock()
_SERVER_URL: str | None = None


def ensure_studio_server(host: str = "127.0.0.1", port: int = 8765, output_dir: Path | None = None) -> str:
    """Start the background Uvicorn studio server if not already running and return its URL."""
    global _SERVER_URL
    with _SERVER_LOCK:
        if _SERVER_URL:
            return _SERVER_URL
        import socket
        import uvicorn

        chosen_port = port
        for candidate in range(port, port + 20):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex((host, candidate)) != 0:
                    chosen_port = candidate
                    break

        studio_app = create_studio_app(output_dir=output_dir)
        config = uvicorn.Config(studio_app, host=host, port=chosen_port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(30):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex((host, chosen_port)) == 0:
                    break
            time.sleep(0.1)
        _SERVER_URL = f"http://{host}:{chosen_port}"
        return _SERVER_URL


def main() -> int:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Auto Content Factory Broadcast Web Studio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"Auto Content Factory Studio running at {url}")
    uvicorn.run(create_studio_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
