"""Local edition state and independent collection, edit, render and release steps."""

import datetime as dt
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

from config import OUTPUT_DIR, PROFILES


MANUAL_TITLE_LIMIT = 72


def edition_path(output_dir, day):
    return Path(output_dir) / "daily" / dt.date.fromisoformat(str(day)).isoformat() / "edition.json"


def read_edition(path):
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"date": path.parent.name, "items": {}, "feed_errors": []}


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            temp.replace(path)
            return
        except PermissionError as error:
            # Windows can deny replacement while the desktop briefly reads the old file.
            if getattr(error, "winerror", None) not in (5, 32) or attempt == 4:
                raise
            time.sleep(0.05)


@contextmanager
def _lock(path, timeout=0.1):
    with closing(sqlite3.connect(path, timeout=timeout)) as lock:
        try:
            lock.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY:
                raise RuntimeError("这个环节正在运行，其他环节可继续使用。") from error
            raise
        yield


def _edit_edition(path, change):
    with _lock(path.with_suffix(".sqlite3"), timeout=5):
        edition = read_edition(path)
        change(edition)
        write_json(path, edition)
        return edition


def _update_item(path, platform, **changes):
    _edit_edition(path, lambda edition: edition["items"][platform].update(changes))


def _merge_media(path, platform, assets, **changes):
    def merge(edition):
        item = edition["items"][platform]
        retained = {asset["page_url"]: asset for asset in item.get("media", ())}
        for asset in assets:
            url = asset["page_url"]
            previous = retained.get(url, {})
            if not previous and not asset.get("local"):
                item.setdefault("media_review", {}).setdefault(url, "pending")
            if previous.get("chapters") or asset.get("chapters"):
                asset = {**asset, "chapters": sorted(set(previous.get("chapters", [])) | set(asset.get("chapters", [])))}
            retained[url] = asset
        item.update(media=list(retained.values()), **changes)

    return _edit_edition(path, merge)["items"][platform]["media"]


def _collect(path, now):
    from core.daily_news import collect_news, select_stories, story_key

    with _lock(path.parent / "collect.sqlite3"):
        edition = read_edition(path)
        if set(PROFILES) <= edition["items"].keys():
            return edition
        seen = {
            (platform, story_key(item["story"]))
            for old in path.parent.parent.glob("*/edition.json") if old < path
            for platform, item in read_edition(old)["items"].items()
            if item["story"].get("url") and (item.get("package") or item["status"] == "ready")
        }
        stories, errors = collect_news(now)
        matches = select_stories(stories, seen)

        def apply_selection(current):
            current["feed_errors"] = errors
            for platform, story in matches.items():
                current["items"].setdefault(platform, {"story": story, "status": "pending"})

        return _edit_edition(path, apply_selection)


def _create_story(path, platform, payload):
    from core.editing import validate_cut

    title = payload.get("title") if isinstance(payload, dict) else None
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= MANUAL_TITLE_LIMIT:
        raise ValueError(f"自拟选题标题需为 1–{MANUAL_TITLE_LIMIT} 字。")
    cut = validate_cut(payload.get("cut"), ())
    story = {"title": title.strip(), "category": "自拟", "source": "作者自拟", "url": ""}

    def create(edition):
        if platform in edition["items"]:
            raise RuntimeError("该平台当天已有选题，请保留或编辑已有草稿。")
        edition["items"][platform] = {
            "story": story, "status": "pending", "edit": cut, "edit_revision": uuid.uuid4().hex,
        }

    _edit_edition(path, create)
    return cut


def release_directory(path, platform, item):
    if item.get("release_dir"):
        return Path(item["release_dir"])
    if item.get("package"):
        return Path(item["package"]["video"]).parent
    return path.parent / platform / "release"


def _queue_hotspots(path, output_dir, now):
    from core.daily_news import story_key
    from core.intake import category_for_story, match_hotspots, read_intake

    snapshot = read_intake(output_dir, now)
    matches = match_hotspots(snapshot, now)
    seen = {
        (platform, story_key(item["story"]))
        for old in path.parent.parent.glob("*/edition.json") if old < path
        for platform, item in read_edition(old)["items"].items()
        if item["story"].get("url") and item.get("package")
    }

    def queue(edition):
        used = {story_key(item["story"]) for item in edition["items"].values()}
        edition["feed_errors"] = snapshot["errors"]
        for platform in PROFILES:
            if platform in edition["items"]:
                continue
            for row in matches.get(platform, []):
                story = row["article"]
                if not story:
                    continue
                key = story_key(story)
                if key in used or (platform, key) in seen:
                    continue
                used.add(key)
                edition["items"][platform] = {
                    "story": {**story, "category": category_for_story(story)}, "status": "pending",
                    "hotspot": {**{k: row[k] for k in ("rank", "title", "url", "heat")},
                                "published": snapshot["ranks"][platform]["published"]},
                }
                break

    return _edit_edition(path, queue)


def _queue_episodes(path, output_dir, now):
    from core.episodes import EPISODE_SECONDS, select_chapters
    from core.intake import read_intake

    snapshot = read_intake(output_dir, now)
    choices = {platform: select_chapters(snapshot, platform, now) for platform in PROFILES}
    def queue(edition):
        for platform, chapters in choices.items():
            previous = edition["items"].get(platform)
            if (previous and (previous.get("chapters") or previous.get("editorial"))) or len(chapters) < 3:
                continue
            if previous:
                edition.setdefault("previous_items", {}).setdefault(platform, []).append(previous)
            edition["items"][platform] = {"status": "pending", "chapters": chapters,
                "release_dir": str(path.parent / platform / "episode_releases" / uuid.uuid4().hex),
                "target_seconds": EPISODE_SECONDS, "hotspot": chapters[0]["hotspot"],
                "story": {"title": f"{now:%m月%d日}热点资讯｜{len(chapters)}条看点", "category": "综合",
                          "source": "多来源报道", "url": "", "summary": "", "published": now.isoformat()}}
    return _edit_edition(path, queue)


def _queue_selected_episode(path, output_dir, platform, selection, now):
    from core.episodes import EPISODE_SECONDS, selected_chapters
    from core.intake import read_intake

    with _lock(path.parent / f"{platform}-selection.sqlite3"):
        current = read_edition(path)["items"].get(platform, {})
        if current.get("selected_hotspots") == selection and current.get("chapters"):
            return current
        chapters = selected_chapters(read_intake(output_dir, now), platform, now, selection)
        episode = {
            "id": uuid.uuid4().hex, "status": "pending", "chapters": chapters,
            "selected_hotspots": selection, "target_seconds": EPISODE_SECONDS,
            "story": {"title": f"{now:%m月%d日}热点资讯｜{len(chapters)}条看点", "category": "综合",
                      "source": "多来源报道", "url": "", "summary": "", "published": now.isoformat()},
        }
        episode["release_dir"] = str(path.parent / platform / "episode_releases" / episode["id"])

        def queue(edition):
            previous = edition["items"].get(platform)
            if previous:
                edition.setdefault("previous_items", {}).setdefault(platform, []).append(previous)
            edition["items"][platform] = episode

        directory = path.parent / platform
        directory.mkdir(exist_ok=True)
        with ExitStack() as claims:
            for stage in ("media", "import_media", "ai", "preview", "render"):
                claims.enter_context(_lock(directory / f"{stage}.sqlite3"))
            return _edit_edition(path, queue)["items"][platform]


def _queue_editorial(path, output_dir, platform, payload, now):
    from core.editorial import confirm_sources
    from core.intake import read_intake

    directory = path.parent / platform
    directory.mkdir(exist_ok=True)
    with ExitStack() as claims:
        for lock in (path.parent / f"{platform}-selection.sqlite3",
                     *(directory / f"{step}.sqlite3" for step in ("media", "import_media", "ai", "preview", "render"))):
            claims.enter_context(_lock(lock))
        current = read_edition(path)["items"].get(platform, {})
        saved = current.get("editorial")
        if saved and saved["anchor"] == payload.get("anchor") and [r["id"] for r in saved["reports"]] == payload.get("source_ids"):
            return current
        editorial = confirm_sources(read_intake(output_dir, now), payload, now, saved=saved)
        episode = {"id": uuid.uuid4().hex, "status": "pending", "editorial": editorial,
                   "target_seconds": 720, "story": {"title": editorial["anchor"]["title"],
                   "category": "综合", "source": "多来源报道", "url": "", "summary": "", "published": now.isoformat()}}
        episode["release_dir"] = str(directory / "episode_releases" / episode["id"])
        if saved and saved["anchor"] == editorial["anchor"]:
            episode["script_id"] = current.get("script_id", current["id"])
            if current.get("chapters") and current.get("editorial_plan"):
                episode["previous_chapters"] = current["chapters"]
                episode["previous_plan"] = current["editorial_plan"]
            episode["media"] = [asset for asset in current.get("media", [])
                                if asset.get("local") or asset.get("source_id") in payload["source_ids"]]
            episode["media_review"] = current.get("media_review", {})

        def queue(edition):
            previous = edition["items"].get(platform)
            if previous and (previous.get("package") or previous.get("edit") or not previous.get("editorial")):
                edition.setdefault("previous_items", {}).setdefault(platform, []).append(previous)
            edition["items"][platform] = episode

        return _edit_edition(path, queue)["items"][platform]


def _prepare_editorial(path, output_dir, platform, directory, episode_id):
    from core.copywriting import retrieve_cases
    from core.editorial import _hash, plan_chapters, write_batches
    from core.media_review import usable_media

    with ExitStack() as claims:
        for step in ("media", "import_media", "ai", "preview", "render"):
            claims.enter_context(_lock(directory / f"{step}.sqlite3"))
        item = read_edition(path)["items"].get(platform, {})
        if not item.get("editorial") or item.get("id") != episode_id:
            raise RuntimeError("长片选题已更换，请重新打开。")
        work = directory / "production" / item.get("script_id", episode_id) / "script"
        work.mkdir(parents=True, exist_ok=True)
        try:
            if item.get("edit"):
                _update_item(path, platform, editorial_progress="渲染开场试剪")
                preview = _render_preview(path, platform, item, directory)
                _update_item(path, platform, editorial_progress="开场试剪已就绪")
                return preview
            _update_item(path, platform, editorial_progress="整理章节与报道")
            key = _hash([item["editorial"], platform, "editorial-outline-v1"])
            chapters, plan = plan_chapters(item["editorial"], platform, work / f"outline-{key}.json",
                                           previous=item.get("previous_chapters", ()),
                                           previous_plan=item.get("previous_plan"))
            item.update(chapters=chapters, editorial_plan=plan)
            _update_item(path, platform, chapters=chapters, editorial_plan=plan)
            if not item.get("media_checked"):
                _update_item(path, platform, editorial_progress="准备章节素材")
                item["media"] = _prepare_media(path, platform, item, directory)
            refs = retrieve_cases(output_dir, platform, item["story"]["title"])

            def progress(done, total, title):
                _update_item(path, platform, editorial_progress=f"已复核 {done}/{total} 章 · {title}")

            def opening(draft):
                revision = _hash(draft)
                if (item.get("preview_revision") == revision
                        and Path((item.get("preview") or {}).get("video", "")).is_file()):
                    return
                _update_item(path, platform, opening_revision=revision, editorial_progress="渲染已复核的开场")
                _render_preview(path, platform, item, directory, draft=draft, revision=revision)

            suggestion = write_batches({**item, "media": usable_media(item)}, platform, work,
                                       [ref["notes"] for ref in refs], progress, opening=opening)
            revision = _hash(suggestion)
            _update_item(path, platform, suggestion=suggestion, suggestion_revision=revision,
                         editorial_progress="渲染开场试剪")
            item.update(suggestion=suggestion, suggestion_revision=revision)
            preview = _render_preview(path, platform, item, directory)
            _update_item(path, platform, editorial_progress="开场试剪已就绪")
            return preview
        except Exception as error:
            _update_item(path, platform, editorial_progress=str(error))
            raise


def run_stage(stage, day, output_dir=OUTPUT_DIR, platform=None, payload=None, *, now=None):
    if stage == "intake":
        from core.intake import refresh_intake
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        with _lock(output_dir / "intake.lock.sqlite3"):
            from core.intake import supplement_hotspot_reports, read_intake
            stamp = now or dt.datetime.now().astimezone()
            refresh_intake(output_dir, stamp)
            supplement_hotspot_reports(output_dir, stamp)
            return read_intake(output_dir, stamp)
    if stage == "supplement_hotspots":
        from core.intake import supplement_hotspot_reports
        return supplement_hotspot_reports(output_dir, now or dt.datetime.now().astimezone())
    if stage == "copywriting":
        from core.copywriting import generate_batch
        return generate_batch(output_dir, **payload)
    if stage == "analyze_case":
        from core.copywriting import analyze_case
        return analyze_case(platform, payload)
    if stage == "editorial_sources":
        from core.editorial import source_options
        from core.intake import read_intake
        stamp = now or dt.datetime.now().astimezone()
        previous = read_edition(edition_path(output_dir, day))["items"].get(platform, {}) if platform in PROFILES else {}
        return source_options(read_intake(output_dir, stamp), payload, stamp, saved=previous.get("editorial"))
    if stage == "login":
        from core.accounts import finish_login
        return finish_login(platform, **payload)
    if stage == "videos":
        from core.news_release import list_videos
        if isinstance(payload, dict):
            return list_videos(platform, **payload)
        return list_videos(platform, payload or 0)
    path = edition_path(output_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    if stage == "collect":
        return _collect(path, now or dt.datetime.now().astimezone())
    if stage == "queue_episodes":
        return _queue_episodes(path, output_dir, now or dt.datetime.now().astimezone())
    if stage == "queue_hotspots":
        return _queue_hotspots(path, output_dir, now or dt.datetime.now().astimezone())
    if platform not in PROFILES:
        raise ValueError("请选择有效的平台。")
    if stage == "editorial_create":
        if not isinstance(payload, dict):
            raise ValueError("请确认选题和报道。")
        return _queue_editorial(path, output_dir, platform, payload, now or dt.datetime.now().astimezone())
    if stage == "select_episode":
        stamp = now or dt.datetime.now().astimezone()
        if path.parent.name != stamp.date().isoformat():
            raise ValueError("多热点合辑使用今天的榜单。")
        return _queue_selected_episode(path, output_dir, platform, payload, stamp)
    if stage == "create":
        return _create_story(path, platform, payload)
    item = read_edition(path)["items"].get(platform)
    if not item:
        raise RuntimeError("该平台还没有选题，请先采集资讯或新建自拟选题。")
    if stage == "preview_asset":
        from core.media_review import media_state
        from core.news_media import preview_video_asset

        if not isinstance(payload, dict):
            raise ValueError("请选择要审看的视频素材。")
        asset = next((entry for entry in item.get("media", ()) if entry["page_url"] == payload.get("page_url")), None)
        if item.get("id") != payload.get("id") or not asset or media_state(item, asset) == "removed":
            raise RuntimeError("素材已移出或选题已更换。")
        return preview_video_asset(asset)
    if stage == "review_media":
        if not isinstance(payload, dict) or payload.get("state") not in {"approved", "removed"}:
            raise ValueError("请选择要审核的素材。")
        url, state = payload.get("page_url"), payload["state"]

        def review(edition):
            current = edition["items"][platform]
            if (current.get("id") != payload.get("id")
                    or not any(asset["page_url"] == url for asset in current.get("media", []))):
                raise RuntimeError("素材已更换，请刷新素材库。")
            current.setdefault("media_review", {})[url] = state
            if state == "removed":
                if url in current.get("preview_media", ()):
                    current["preview_revision"] = None
                for name, revision in (("edit", "edit_revision"), ("suggestion", "suggestion_revision")):
                    shots = (current.get(name) or {}).get("scenes", [])
                    using = [shot for shot in shots if shot.get("media") == url]
                    for shot in using:
                        shot.update(media="", zoom=False)
                    if using:
                        current[revision] = uuid.uuid4().hex

        directory = path.parent / platform
        directory.mkdir(exist_ok=True)
        with ExitStack() as claims:
            claims.enter_context(_lock(path.parent / f"{platform}-selection.sqlite3"))
            for step in ("media", "import_media", "ai", "preview", "render"):
                claims.enter_context(_lock(directory / f"{step}.sqlite3"))
            _edit_edition(path, review)
        return state
    if item.get("editorial") and stage in {"editorial_preview", "editorial_export", "render"}:
        directory = path.parent / platform
        with _lock(path.parent / f"{platform}-selection.sqlite3"):
            item = read_edition(path)["items"][platform]
            if stage == "editorial_preview":
                return _prepare_editorial(path, output_dir, platform, directory, payload)
            if (stage != "editorial_export" or not isinstance(payload, dict)
                    or payload.get("id") != item.get("id") or not item.get("edit")
                    or payload.get("revision") != item.get("edit_revision")):
                raise RuntimeError("请先审看、保存并确认当前分镜后导出。")
            with _lock(directory / "render.sqlite3"):
                return _render(path, platform, item, directory)
    if stage == "draft":
        from core.editing import default_cut
        if item.get("editorial"):
            draft = item.get("edit") or item.get("suggestion")
            if not draft:
                raise RuntimeError("请先生成这期长片的分镜。")
            return draft
        from core.media_review import usable_media
        return item.get("edit") or default_cut({**item, "media": usable_media(item)}, platform)
    if stage in {"save", "apply_ai"}:
        from core.editing import validate_cut
        from core.media_review import usable_media

        def apply_cut(edition):
            current = edition["items"][platform]
            if payload.get("episode_id") and payload["episode_id"] != current.get("id"):
                raise RuntimeError("正在编辑的合辑已更换，请重新打开分镜。")
            if stage == "apply_ai" and current.get("edit"):
                return
            clean = validate_cut(payload, usable_media(current), episode=bool(current.get("chapters")))
            markers = payload.get("chapter_starts")
            if current.get("selected_hotspots") or current.get("editorial"):
                if not current.get("chapters"):
                    raise RuntimeError("请先生成章节规划。")
                chapters = {entry["story"]["title"] for entry in current["chapters"]}
                if (not isinstance(markers, list) or len(markers) != len(chapters)
                        or any(not isinstance(mark, dict) or type(mark.get("scene")) is not int
                               or not isinstance(mark.get("title"), str) for mark in markers)):
                    raise ValueError("合辑需要保留每条报道的章节起点。")
                starts = [mark["scene"] for mark in markers]
                if (starts[0] != 0 or starts != sorted(set(starts)) or starts[-1] >= len(clean["scenes"])
                        or {mark["title"] for mark in markers} != chapters):
                    raise ValueError("分镜的章节顺序无效，请重新打开草稿。")
            if current.get("chapters") and isinstance(markers, list):
                if current.get("editorial"):
                    citations = {mark["title"]: mark.get("source_ids", [])
                                 for mark in (current.get("suggestion") or {}).get("chapter_starts", [])}
                    markers = [{"title": mark["title"], "scene": mark["scene"],
                                "source_ids": citations.get(mark["title"], [])} for mark in markers]
                clean["chapter_starts"] = markers
            if (current.get("selected_hotspots") or current.get("editorial")) and isinstance(payload.get("editorial_plan"), dict):
                clean["editorial_plan"] = payload["editorial_plan"]
            unchanged = current.get("editorial") and clean == current.get("suggestion")
            current.update(edit=clean, edit_revision=current.get("suggestion_revision") if unchanged else uuid.uuid4().hex)

        if item.get("editorial"):
            with _lock(path.parent / f"{platform}-selection.sqlite3"):
                return _edit_edition(path, apply_cut)["items"][platform]["edit"]
        return _edit_edition(path, apply_cut)["items"][platform]["edit"]
    if stage == "hold":
        if not isinstance(payload, dict) or not isinstance(payload.get("hold"), bool):
            raise ValueError("请选择要暂缓或恢复的成片。")
        def change(edition):
            current = edition["items"][platform]
            if not payload.get("video") or (current.get("package") or {}).get("video") != payload["video"]:
                raise RuntimeError("成片已更换，请重新检查。")
            current["held_video"] = payload["video"] if payload["hold"] else None
        _edit_edition(path, change)
        return payload["hold"]
    if stage == "publish":
        from core.news_release import submit_bilibili, submit_douyin
        submit = {"douyin": submit_douyin, "bilibili": submit_bilibili}.get(platform)
        package = item.get("package")
        if not submit or not package:
            raise RuntimeError("当前平台或成片不支持软件内投稿。")
        if item.get("edit_revision") != item.get("package_revision"):
            raise RuntimeError("草稿已更新，请先按当前草稿重新剪辑。")
        if not isinstance(payload, dict) or not isinstance(payload.get("account"), str) or not payload["account"]:
            raise RuntimeError("请在桌面端确认投稿账号和成片。")
        if payload.get("video") != package["video"]:
            raise RuntimeError("成片已更换，请播放当前版本后重新确认投稿。")
        if item.get("held_video") == package["video"]:
            raise RuntimeError("这版成片已暂缓，请先恢复发布资格。")
        release_dir = release_directory(path, platform, item)
        release_dir.mkdir(parents=True, exist_ok=True)
        return submit(package, release_dir, expected_account=payload["account"])
    if stage not in {"media", "import_media", "ai", "preview", "render"}:
        raise ValueError("无效的工作环节。")
    directory = path.parent / platform
    directory.mkdir(exist_ok=True)
    with _lock(directory / f"{stage}.sqlite3"):
        # Re-read after claiming this stage. Other stages only hold a short manifest lock.
        item = read_edition(path)["items"][platform]
        if item.get("editorial") and stage in {"render", "ai", "preview"}:
            raise RuntimeError("请使用长片工作台的试剪或确认导出入口。")
        if stage == "import_media":
            from core.news_media import import_local_asset
            media_dir = directory / "media" / uuid.uuid4().hex
            return _merge_media(path, platform, [import_local_asset(payload, media_dir)])
        if stage == "media":
            return _prepare_media(path, platform, item, directory)
        if stage == "ai":
            from core.editing import suggest_cut
            if item.get("selected_hotspots"):
                from core.copywriting import retrieve_cases
                reports = item["chapters"]
                topic = " ".join(ch["story"]["title"] for ch in reports)
                facts = " ".join(ch["story"]["summary"][:900] for ch in reports)
                references = retrieve_cases(output_dir, platform, topic, facts)
                suggestion = suggest_cut(item, platform, work=directory / "production" / item["id"] / "script",
                                         references=[{"title": case["title"], "notes": case["notes"], "url": case["url"]}
                                                     for case in references])
            else:
                suggestion = suggest_cut(item, platform)
            _update_item(path, platform, suggestion=suggestion)
            return suggestion
        if stage == "preview":
            return _render_preview(path, platform, item, directory)
        return _render(path, platform, item, directory)


def _prepare_media(path, platform, item, directory):
    from core.news_media import download_story_media

    media_dir = directory / "media" / uuid.uuid4().hex
    media_dir.mkdir(parents=True)
    if item.get("editorial"):
        assets, warnings = [], []
        reports = {report["id"]: report for chapter in item["chapters"] for report in chapter["reports"]}
        for key, report in reports.items():
            indexes = [i for i, ch in enumerate(item["chapters"]) if any(r["id"] == key for r in ch["reports"])]
            known = [a for a in item.get("media", []) if a.get("source_id") == key]
            if not known:
                destination = media_dir / key
                destination.mkdir()
                known, errors = download_story_media(report, destination, episode=True)
                warnings.extend(errors)
            assets.extend({**asset, "source_id": key, "chapters": indexes} for asset in known)
        for asset in item.get("media", []):
            if asset.get("local"):
                assets.append(asset)
        unique = {}
        for asset in assets:
            url = asset["page_url"]
            indexes = set(unique.get(url, {}).get("chapters", [])) | set(asset.get("chapters", []))
            unique[url] = {**asset, "chapters": sorted(indexes)}
        prepared = list(unique.values())
        _update_item(path, platform, media=prepared, warnings=warnings, media_checked=True)
        return prepared
    if item.get("chapters"):
        assets, warnings = [], []
        for index, chapter in enumerate(item["chapters"]):
            destination = media_dir / str(index)
            destination.mkdir()
            found, errors = download_story_media(chapter.get("reports", [chapter["story"]])[0], destination,
                                                hotspot=chapter.get("hotspot"), episode=True)
            assets.extend({**asset, "chapters": [index]} for asset in found)
            warnings.extend(errors)
    else:
        assets, warnings = download_story_media(item["story"], media_dir)
    return _merge_media(path, platform, assets, warnings=warnings, media_checked=True)


def _render_key(case):
    raw = json.dumps(["semantic-render-v1", case], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _render_preview(path, platform, item, directory, *, draft=None, revision=None):
    from core.audio_engine import generate_voice_and_subtitles
    from core.editing import render_case
    from core.video_engine import render_platform_video

    draft = draft or item.get("edit") or (item.get("suggestion") if item.get("editorial") else None)
    if not (item.get("selected_hotspots") or item.get("editorial")) or not draft:
        raise RuntimeError("请先保存这期合辑的分镜。")
    case = render_case({**item, "edit": draft}, platform)
    opening = case["chapter_starts"][1]["scene"] if len(case["chapter_starts"]) > 1 else len(case["scenes"])
    case["scenes"] = case["scenes"][:min(8, opening)]
    output = directory / "production" / item["id"] / "preview" / _render_key(case)
    audio = generate_voice_and_subtitles(case, platform, output)
    video = render_platform_video(case, platform, audio, output)
    preview = {"video": str(video), "cover": str(output / "slide_00.png"), "duration": audio["total_duration"]}
    _update_item(path, platform, preview=preview,
                 preview_media=sorted({scene["asset"]["page_url"] for scene in case["scenes"] if scene.get("asset")}),
                 preview_revision=revision or item.get("edit_revision") or item.get("suggestion_revision"))
    return preview


def _render(path, platform, item, directory):
    from core.editing import render_case
    from core.publisher import package_case_for_all_platforms

    fingerprint = None
    try:
        case = render_case(item, platform)
        if item.get("selected_hotspots") or item.get("editorial"):
            if not item.get("edit"):
                raise RuntimeError("请先保存这期合辑的分镜。")
            fingerprint = _render_key(case)
            package = item.get("package")
            if (package and item.get("production_render_key") == fingerprint
                    and Path(package["video"]).is_file()):
                _update_item(path, platform, package_revision=item.get("edit_revision"), error=None)
                return package
            output = directory / "versions" / item["id"] / fingerprint
        else:
            output = directory / "versions" / uuid.uuid4().hex
        result = package_case_for_all_platforms(case, (platform,), output_root=output)
        package = result["platforms"][platform]
    except Exception as error:
        _update_item(path, platform, status="ready" if item.get("package") else "failed", error=str(error))
        raise
    _update_item(
        path, platform, status="ready", package=package, package_revision=item.get("edit_revision"),
        production_render_key=fingerprint,
        release_dir=str(release_directory(path, platform, item)), error=None,
    )
    return package


def run_daily(now=None, output_dir=OUTPUT_DIR, progress=lambda message: None, invoke=run_stage):
    now = now or dt.datetime.now().astimezone()
    day = now.date()
    path = edition_path(output_dir, day)
    if not set(PROFILES) <= read_edition(path)["items"].keys():
        progress("采集 · 读取近 48 小时资讯")
        try:
            invoke("collect", day, output_dir, now=now)
        except Exception as error:
            progress(f"采集失败 · {error}")
    for platform, item in read_edition(path)["items"].items():
        if item["status"] == "ready" or item.get("editorial"):
            continue
        if not item.get("media_checked"):
            try:
                invoke("media", day, output_dir, platform)
            except Exception as error:
                progress(f"素材采集失败 · {platform} · {error}")
        progress(f"剪辑 · {PROFILES[platform]['series']}")
        try:
            invoke("render", day, output_dir, platform)
        except Exception as error:
            progress(f"剪辑失败 · {platform} · {error}")
    return read_edition(path)


def run_hot_daily(now=None, output_dir=OUTPUT_DIR, progress=lambda message: None, invoke=run_stage):
    from core.intake import REFRESH_MINUTES, read_intake

    now = now or dt.datetime.now().astimezone()
    day = now.date()
    snapshot = read_intake(output_dir, now)
    if now.timestamp() - (snapshot["checked"] or 0) >= REFRESH_MINUTES * 60:
        progress("采集部 · 正在更新资讯和平台榜单")
        try:
            invoke("intake", day, output_dir, now=now)
        except (RuntimeError, OSError) as error:
            progress(f"采集未完成 · {error}")
    edition = invoke("queue_episodes", day, output_dir, now=now)
    errors = []
    for platform, item in edition["items"].items():
        if not item.get("chapters") or item.get("package") or item.get("selected_hotspots") or item.get("editorial"):
            continue
        if not item.get("media_checked") or not any(not asset.get("local") for asset in item.get("media", [])):
            try:
                progress(f"制作部 · {platform} 采集素材")
                invoke("media", day, output_dir, platform)
            except (OSError, RuntimeError) as error:
                progress(f"素材采集未完成 · {platform} · {error}")
        try:
            if not item.get("edit"):
                progress(f"制作部 · {platform} 生成 AI 分镜")
                cut = invoke("ai", day, output_dir, platform)
                invoke("apply_ai", day, output_dir, platform, cut)
            progress(f"制作部 · {platform} 剪辑成片")
            invoke("render", day, output_dir, platform)
        except Exception as error:
            errors.append(f"{platform} · {error}")
    result = read_edition(edition_path(output_dir, day))
    missing = [platform for platform in PROFILES if not result["items"].get(platform, {}).get("chapters")]
    errors.extend(f"{platform} · 尚不足 3 条含正文的热点报道" for platform in missing)
    return result, errors
