"""Local edition state and independent collection, edit, render and release steps."""

import datetime as dt
import hashlib
import json
import math
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


def _merge_media(path, platform, assets, *, expected_id=None, reselect=False, auto_review=False, **changes):
    def merge(edition):
        item = edition["items"].get(platform)
        if expected_id is not None and (not item or item.get("id") != expected_id):
            raise RuntimeError("选题已更换，请重新选择要加入的工作稿。")
        retained = {asset["page_url"]: asset for asset in item.get("media", ())}
        for asset in assets:
            url = asset["page_url"]
            previous = retained.get(url, {})
            if not asset.get("local"):
                if reselect and item.get("media_review", {}).get(url) == "removed":
                    item["media_review"][url] = "pending"
                elif not previous:
                    item.setdefault("media_review", {}).setdefault(url,
                        "approved" if auto_review and asset.get('automatic_review') else "pending")
            if previous.get("chapters") or asset.get("chapters"):
                asset = {**asset, "chapters": sorted(set(previous.get("chapters", [])) | set(asset.get("chapters", [])))}
            if previous.get("verified_report_id"):
                asset = {**asset, "verified_report_id": previous["verified_report_id"]}
            retained[url] = asset
            if item.get("editorial") and asset.get("local") and 0 in asset.get("chapters", ()):
                _invalidate_opening(item)
        item.update(media=list(retained.values()), **changes)

    return _edit_edition(path, merge)["items"][platform]["media"]


def _invalidate_opening(item):
    item["preview_revision"] = None
    item["full_preview_revision"] = None
    for draft, revision in (("edit", "edit_revision"), ("suggestion", "suggestion_revision")):
        if item.get(draft):
            item[revision] = uuid.uuid4().hex


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
            if platform == "douyin":
                chapters = chapters[:3]
            previous = edition["items"].get(platform)
            if (previous and (previous.get("chapters") or previous.get("editorial"))) or len(chapters) < 3:
                continue
            if previous:
                edition.setdefault("previous_items", {}).setdefault(platform, []).append(previous)
            edition["items"][platform] = {"status": "pending", "chapters": chapters,
                "release_dir": str(path.parent / platform / "episode_releases" / uuid.uuid4().hex),
                "target_seconds": 120 if platform == "douyin" else EPISODE_SECONDS, "hotspot": chapters[0]["hotspot"],
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
        if platform == "douyin" and isinstance(selection, list) and len(selection) > 3:
            raise ValueError("抖音快报请选择 3 条重点热点。")
        chapters = selected_chapters(read_intake(output_dir, now), platform, now, selection)
        episode = {
            "id": uuid.uuid4().hex, "status": "pending", "chapters": chapters,
            "selected_hotspots": selection, "target_seconds": 120 if platform == "douyin" else EPISODE_SECONDS,
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
    from config import DOUYIN_BRIEF_SECONDS
    from core.editorial import confirm_sources
    from core.intake import read_intake
    from core.video_styles import select_video_style

    directory = path.parent / platform
    directory.mkdir(exist_ok=True)
    with ExitStack() as claims:
        for lock in (path.parent / f"{platform}-selection.sqlite3",
                     *(directory / f"{step}.sqlite3" for step in ("media", "import_media", "ai", "preview", "render"))):
            claims.enter_context(_lock(lock))
        current = read_edition(path)["items"].get(platform, {})
        saved = current.get("editorial")
        short_video = platform == "douyin" or (platform == "xiaohongshu" and "brief" in payload)
        seconds = payload.get("target_seconds", 120 if short_video else 720)
        allowed = DOUYIN_BRIEF_SECONDS if platform == "douyin" else (
            (*DOUYIN_BRIEF_SECONDS, 720) if platform == "xiaohongshu" else (720,))
        if type(seconds) is not int or seconds not in allowed:
            raise ValueError("请选择该平台支持的目标时长。")
        requested_style = payload.get("video_style", "")
        if not isinstance(requested_style, str):
            raise ValueError("请选择有效的视频类型。")
        if (saved and saved["anchor"] == payload.get("anchor") and current.get("target_seconds", 720) == seconds
                and current.get("video_style_request", "") == requested_style
                and "brief" not in saved and "brief" not in payload
                and [r["id"] for r in saved["reports"]] == payload.get("source_ids")):
            return current
        editorial = confirm_sources(read_intake(output_dir, now), payload, now, saved=saved)
        if (saved == editorial and current.get("target_seconds", 720) == seconds
                and current.get("video_style_request", "") == requested_style):
            return current
        video_style = select_video_style(platform, editorial["anchor"]["title"], requested_style)
        if editorial.get("mode") == "original" and not requested_style:
            from core.video_styles import original_video_style

            video_style = original_video_style()
        episode = {"id": uuid.uuid4().hex, "status": "pending", "editorial": editorial,
                   "target_seconds": seconds, "story": {"title": editorial["anchor"]["title"],
                   "category": "综合", "source": "多来源报道", "url": "", "summary": "", "published": now.isoformat()}}
        if editorial.get("mode") == "original":
            episode["story"].update(category="原创", source="用户创作说明（非新闻来源）")
        if video_style:
            episode.update(video_style=video_style, video_style_request=requested_style)
        episode["release_dir"] = str(directory / "episode_releases" / episode["id"])
        if saved and saved["anchor"] == editorial["anchor"] and "brief" not in editorial:
            episode["script_id"] = current.get("script_id", current["id"])
            if (current.get("target_seconds", 720) == seconds and current.get("video_style") == video_style
                    and current.get("chapters") and current.get("editorial_plan")):
                episode["previous_chapters"] = current["chapters"]
                episode["previous_plan"] = current["editorial_plan"]
            episode["media"] = [asset for asset in current.get("media", [])
                                if asset.get("local") or asset.get("source_id") in payload["source_ids"]]
            episode["media_review"] = current.get("media_review", {})

        def queue(edition):
            previous = edition["items"].get(platform)
            if previous:
                edition.setdefault("previous_items", {}).setdefault(platform, []).append(previous)
            edition["items"][platform] = episode

        with ExitStack() as rollback:
            if brief := editorial.get("brief"):
                episode.update(media=_import_brief_media(brief, current, directory, episode["id"], rollback))
                episode["media_review"] = {asset["page_url"]: "approved" for asset in episode["media"]}
            result = _edit_edition(path, queue)["items"][platform]
            rollback.pop_all()
            return result


def _import_brief_media(brief, previous, directory, episode_id, rollback):
    from core.news_media import import_local_asset
    from core.material_search import saved_materials

    saved = {asset["brief_source"]: asset for asset in previous.get("media", ()) if "brief_source" in asset}
    library = {str(Path(asset["path"]).resolve()): asset for asset in saved_materials(directory.parents[2])}
    selected = []
    for index, filename in enumerate(brief["asset_paths"]):
        asset = saved.get(filename) or library.get(str(Path(filename).resolve()))
        if asset and not Path(asset["path"]).is_file():
            asset = None
        if not asset:
            destination = directory / "media" / f"{episode_id}-{index}"
            asset = import_local_asset(filename, destination)
            rollback.callback(_discard_brief_copy, destination, asset)
        selected.append({**asset, "brief_source": filename, "brief_asset_index": index})
    return selected


def _discard_brief_copy(destination, asset):
    Path(asset["path"]).unlink(missing_ok=True)
    Path(asset["preview"]).unlink(missing_ok=True)
    destination.rmdir()


def _prepare_editorial(path, output_dir, platform, directory, episode_id, *, rewrite=False,
                       with_visuals=False, visual_only=False, plan_only=False):
    from core.copywriting import chapter_cases
    from core.editorial import _hash, plan_chapters, retrieve_chapter_evidence, write_batches
    from core.media_review import usable_media
    from core.video_styles import select_video_style

    with ExitStack() as claims:
        for step in ("media", "import_media", "ai", "preview", "render"):
            claims.enter_context(_lock(directory / f"{step}.sqlite3"))
        item = read_edition(path)["items"].get(platform, {})
        if not item.get("editorial") or item.get("id") != episode_id:
            raise RuntimeError("长片选题已更换，请重新打开。")
        topic = item.get("story", {}).get("title") or item["editorial"].get("anchor", {}).get("title", "")
        video_style = item.get("video_style") or select_video_style(platform, topic)
        if video_style and not video_style.get('speech') and video_style.get('id') != 'original':
            from core.spoken_style import PLATFORMS
            video_style = {**video_style, 'speech':PLATFORMS[platform]}
        if video_style != item.get("video_style"):
            item["video_style"] = video_style
            _update_item(path, platform, video_style=video_style)
        work = directory / "production" / item.get("script_id", episode_id) / "script"
        work.mkdir(parents=True, exist_ok=True)
        try:
            models = None
            if with_visuals or visual_only:
                if (not visual_only and item['editorial'].get('mode') != 'original'
                        and item['editorial'].get('reports') and not item.get('original_media_checked')
                        and not item['editorial'].get('brief', {}).get('asset_paths')):
                    _prepare_original_media(path, platform, item, output_dir)
                    item = read_edition(path)['items'][platform]
                from core.directing import direct_cut, production_models

                models = production_models()
                if not models:
                    raise RuntimeError("请先启动本机 Ollama，并安装视觉设计和看图选镜模型。")
                if video_style and video_style.get('id') == 'roundup':
                    from core.scene_index import index_video
                    indexes = []
                    for asset in usable_media(item):
                        if asset.get('kind') == 'video':
                            indexes.append(index_video(asset,Path(asset['path']).parent/'.scene-index',models['editing'],
                                lambda title:_update_item(path,platform,editorial_progress=title),with_speech=True))
                    item['source_indexes'] = indexes
                    _update_item(path,platform,source_indexes=indexes)

            def direct(draft):
                timings=(_narration_timing(item,draft,platform,directory)
                         if video_style and video_style.get('id')=='roundup' else None)
                return direct_cut(draft, usable_media(item), platform, work / "visual", models,
                                  lambda title: _update_item(path, platform,
                                                             editorial_progress=f"设计画面并看图选镜 · {title}"),
                                  chapters=item["chapters"], video_style=video_style,
                                  fixed_style=bool(item.get("video_style_request")),
                                  brief=item.get('editorial',{}).get('brief'),
                                  source_indexes=item.get('source_indexes',()), topic=topic,durations=timings)

            if visual_only:
                base = item.get("edit") or item.get("suggestion")
                if not base:
                    raise RuntimeError("请先生成或保存一版口播分镜。")
                suggestion = direct(base)
                revision = _hash(suggestion)
                _update_item(path, platform, suggestion=suggestion, suggestion_revision=revision,
                             editorial_progress="视觉分镜已就绪")
                from core.directing import audit_visuals
                audit = audit_visuals(suggestion,usable_media(item))
                _update_item(path,platform,production_audit=audit)
                if not audit['ready']:
                    raise RuntimeError(audit['message'])
                if plan_only:
                    return suggestion
                _render_preview(path, platform, item, directory, draft=suggestion, revision=revision)
                return suggestion
            if item.get("edit") and not rewrite:
                if not item.get("opening_media_checked"):
                    _update_item(path, platform, editorial_progress="查找片头素材")
                    item["media"] = _prepare_media(path, platform, item, directory, opening_only=True)
                _update_item(path, platform, editorial_progress="渲染开场试剪")
                preview = _render_preview(path, platform, item, directory)
                _update_item(path, platform, editorial_progress="开场试剪已就绪")
                return preview
            _update_item(path, platform, editorial_progress="整理章节与报道")
            from core.scene_index import writing_catalog
            available_media=(writing_catalog(usable_media(item),item.get('source_indexes',()),topic,limit=8)
                             if models and video_style and video_style.get('id')=='roundup' else [])
            if rewrite and item.get("chapters") and item.get("editorial_plan"):
                chapters, plan = item["chapters"], item["editorial_plan"]
            else:
                key = _hash([item["editorial"], platform, item.get("target_seconds", 720), video_style,
                             "editorial-outline-v8",*([available_media] if available_media else [])])
                chapters, plan = plan_chapters(item["editorial"], platform, work / f"outline-{key}.json",
                                               previous=item.get("previous_chapters", ()),
                                               previous_plan=item.get("previous_plan"),
                                               target_seconds=item.get("target_seconds", 720), video_style=video_style,
                                               **({'available_media':available_media} if available_media else {}))
            if item.get('target_seconds', 720) > 180 and item['editorial'].get('mode') != 'original':
                chapters = retrieve_chapter_evidence(item['editorial'], chapters, work,
                    lambda title: _update_item(path, platform, editorial_progress=title))
            item.update(chapters=chapters, editorial_plan=plan)
            _update_item(path, platform, chapters=chapters, editorial_plan=plan)
            if not item.get("media_checked") or not item.get("opening_media_checked"):
                _update_item(path, platform, editorial_progress="准备章节素材")
                item["media"] = _prepare_media(path, platform, item, directory,
                                              opening_only=bool(item.get("media_checked")))
                # Reload review decisions made while acquiring media. Imported selections are approved;
                # downloaded candidates need review before the model can use them.
                item["media_review"] = read_edition(path)["items"][platform].get("media_review", {})
            if with_visuals and not usable_media(item) and item['editorial'].get('mode') == 'original':
                raise RuntimeError("尚无已确认的图片或视频。请在下方媒体素材中预览并接入相关素材，再继续制作；不会用文字幻灯片代替。")
            def progress(done, total, title):
                _update_item(path, platform, editorial_progress=f"已复核 {done}/{total} 章 · {title}")

            def opening(draft):
                revision = _hash(draft)
                if (item.get("preview_revision") == revision
                        and Path((item.get("preview") or {}).get("video", "")).is_file()):
                    return
                _update_item(path, platform, opening_revision=revision, editorial_progress="渲染已复核的开场")
                _render_preview(path, platform, item, directory, draft=draft, revision=revision)

            def references(chapter):
                from core.spoken_style import resolve_reference
                reference = resolve_reference(output_dir, platform, chapter['story']['title'],
                    ' '.join(report['title'] for report in chapter['reports']),
                    explicit=item['editorial'].get('style_reference'))
                return [reference] if reference else []

            suggestion = write_batches({**item, "media": usable_media(item)}, platform, work, references,
                                       progress, opening=None if models or (rewrite and item.get("edit")) else opening,
                                       **({"writing_only": True,**({'source_indexes':item.get('source_indexes',())}
                                          if video_style and video_style.get('id')=='roundup' else {})} if models else {}))
            if models:
                if not item.get("edit"):
                    _update_item(path, platform, suggestion=suggestion, suggestion_revision=_hash(suggestion))
                suggestion = direct(suggestion)
                from core.directing import audit_visuals

                audit = audit_visuals(suggestion, usable_media(item))
                _update_item(path, platform, suggestion=suggestion, suggestion_revision=_hash(suggestion),
                             production_audit=audit)
                if not audit["ready"]:
                    raise RuntimeError(audit["message"])
            revision = _hash(suggestion)
            if plan_only:
                _update_item(path, platform, suggestion=suggestion, suggestion_revision=revision,
                             editorial_progress="全片分镜已就绪")
                return suggestion
            if rewrite and item.get("edit"):
                _update_item(path, platform, suggestion=suggestion, suggestion_revision=revision,
                             editorial_progress="案例改写稿已就绪")
                return suggestion
            _update_item(path, platform, suggestion=suggestion, suggestion_revision=revision,
                         editorial_progress="渲染开场试剪")
            item.update(suggestion=suggestion, suggestion_revision=revision)
            preview = _render_preview(path, platform, item, directory)
            _update_item(path, platform, editorial_progress="开场试剪已就绪")
            return preview
        except Exception as error:
            _update_item(path, platform, editorial_progress=str(error))
            raise


def _require_cut_version(item, request):
    if (not item or any(request.get(key) != item.get(field) for key, field in (
            ("id", "id"), ("revision", "edit_revision"), ("suggestion_revision", "suggestion_revision")))):
        raise RuntimeError("分镜草稿已更新，请重新打开后再操作。")


def _narration_timing(item,draft,platform,directory):
    from core.audio_engine import generate_voice_and_subtitles
    from core.editing import render_case

    # Measuring a new selection must not validate the old, possibly edited mapping.
    voice_shots=[{**shot,'visual':{key:value for key,value in shot['visual'].items() if key != 'clips'}}
                 if shot.get('visual') else shot for shot in draft['scenes']]
    case=render_case({**item,'edit':{**draft,'scenes':voice_shots}},platform)
    output=directory/'production'/(item.get('id') or 'draft')/'full-preview'/('narration-'+_render_key(case))
    audio=generate_voice_and_subtitles(case,platform,output)
    timings={index:0. for index in range(len(draft['scenes']))}
    segments={str(index):[] for index in timings}
    for scene,seconds in zip(case['scenes'],audio['scene_durations'],strict=True):
        group=scene['narration_group']
        if group not in timings or type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
            raise ValueError('配音分段与原始口播不一致。')
        timings[group]+=seconds
        segments[str(group)].append(scene['text'])
    if any(seconds<=0 for seconds in timings.values()):
        raise ValueError('配音没有覆盖全部原始口播。')
    draft['editorial_plan']={**(draft.get('editorial_plan') or {}),'narration_segments':segments}
    return timings


def _direct_scenes(item, draft, platform, directory, media, indexes, *, required_media=None, progress=None):
    from core.directing import direct_cut, production_models

    models = production_models()
    if not models:
        raise RuntimeError("请先启动本机 Ollama，并安装视觉设计和看图选镜模型。")
    if (item.get('video_style') or {}).get('id') == 'roundup':
        from core.scene_index import index_video
        item['source_indexes'] = [index_video(asset,Path(asset['path']).parent/'.scene-index',models['editing'],
                                              progress or (lambda _title:None),with_speech=True)
                                  for asset in media if asset.get('kind') == 'video']
        timings=_narration_timing(item,draft,platform,directory.parent)
    else:
        timings=None
    return direct_cut(draft, media, platform, directory, models, progress or (lambda _title: None),
                      chapters=item.get("chapters", ()), video_style=item.get("video_style"),
                      fixed_style=bool(item.get("video_style_request")),
                      brief=(item.get("editorial") or {}).get("brief"), only=indexes, required_media=required_media,
                      source_indexes=item.get('source_indexes',()),
                      topic=(item.get('editorial') or {}).get('anchor',{}).get('title',''),durations=timings)


def _direct_scene(path, platform, item, directory, request):
    from core.editing import default_cut, render_case, validate_cut
    from core.media_review import usable_media

    media = usable_media(item)
    base = item.get("suggestion" if request.get("source") == "suggestion" else "edit")
    if not base and not item.get("editorial") and request.get("source") == "edit":
        base = default_cut({**item, "media": media}, platform)
    index = request.get("index")
    if not base or type(index) is not int or not 0 <= index < len(base["scenes"]):
        raise ValueError("请选择当前草稿中的一个分镜。")
    shots = list(base["scenes"])
    shots[index] = request.get("scene")
    cut = validate_cut({"scenes": shots}, media, episode=bool(item.get("chapters")))
    picked = cut["scenes"][index]["media"]
    edited = _direct_scenes(item, {**base, **cut}, platform, directory / "visual", media, {index},
                            required_media=picked or None)["scenes"][index]
    if picked and edited["media"] != picked:
        raise RuntimeError("AI 未在选定素材中找到与口播匹配的画面，请调整口播或改选素材。")
    case = render_case({**item, "chapters": [], "edit": {"scenes": [edited]}}, platform)
    preview = _render_trial(case, platform, directory / "shot-preview" / _render_key(case))
    _require_cut_version(read_edition(path)["items"].get(platform), request)
    return {"scene": edited, "preview": preview}


def _auto_cut(path, output_dir, platform, item, directory, request):
    from core.directing import audit_visuals
    from core.editing import suggest_cut, validate_cut
    from core.media_review import usable_media

    edit = item.get("edit")
    base = edit or item.get("suggestion")
    prepared = False
    if not edit and item.get("editorial"):
        markers = (base or {}).get('chapter_starts', [])
        chapters = item.get('chapters', [])
        from core.spoken_style import VERSION
        complete = bool((base or {}).get('editorial_plan',{}).get('spoken_style_version') == VERSION
            and chapters and len(markers) == len(chapters)
            and [mark.get('title') for mark in markers] == [chapter['story']['title'] for chapter in chapters]
            and all(type(mark.get('scene')) is int and 0 <= mark['scene'] < len(base.get('scenes',[])) for mark in markers))
        base = _prepare_editorial(path, output_dir, platform, directory, item["id"],
                                  with_visuals=True, visual_only=complete, plan_only=True)
        item = read_edition(path)["items"][platform]
        request = {**request, "suggestion_revision": item.get("suggestion_revision")}
        prepared = True
    with _lock(directory / "ai.sqlite3"), _lock(directory / "render.sqlite3"):
        item = read_edition(path)["items"][platform]
        _require_cut_version(item, request)
        if not base:
            _update_item(path, platform, editorial_progress="正在编排全片口播")
            base = suggest_cut(item, platform)
        media = usable_media(item)
        indexes = set(range(len(base["scenes"])))
        confirmed = set(base.get("confirmed_scenes", indexes if edit else ()))
        pending = indexes - confirmed
        if request.get('only_missing') is True:
            if not edit:
                raise ValueError('请先生成一版整片，再补齐缺失画面。')
            available={asset['page_url'] for asset in media}
            pending={index for index in pending if base['scenes'][index].get('media') not in available}
        draft = base
        audit = None
        if pending and not prepared:
            def progress(title):
                _update_item(path, platform, editorial_progress=f"正在选镜 · {title}")

            directed = _direct_scenes(item, base, platform, directory / "visual", media, pending, progress=progress)
            draft = directed
            audit = audit_visuals(draft, media)
        export = request.get("export") is True
        blocked = audit is not None and not audit['ready']
        if pending or not edit:
            draft = {**draft, **validate_cut({"scenes": draft["scenes"],
                     "confirmed_scenes": sorted(indexes if export and not blocked else confirmed)}, media,
                     episode=bool(item.get("chapters")))}

        def commit(edition):
            current = edition["items"].get(platform)
            _require_cut_version(current, request)
            if draft != edit or not current.get("edit_revision"):
                current.update(edit=draft, edit_revision=uuid.uuid4().hex)
            if 'source_indexes' in item:
                current['source_indexes']=item['source_indexes']
            if audit is not None:
                current['production_audit']=audit
            current["editorial_progress"] = audit['message'] if blocked else (
                "正在导出成片" if export else "正在拼接全片试剪")

        saved = _edit_edition(path, commit)["items"][platform]
        if blocked:
            _notify_quality_failure(path, platform, saved, None,
                                    {"findings": [audit["message"]], "message": audit["message"]})
            raise RuntimeError(audit['message'])
        result = _render(path, platform, saved, directory) if export else _render_preview(
            path, platform, saved, directory, whole=True)
        _update_item(path, platform, editorial_progress="成片已导出" if export else "全片试剪已就绪")
        return result


def run_stage(stage, day, output_dir=OUTPUT_DIR, platform=None, payload=None, *, now=None, open_post_panel=None):
    if stage in {'topic_prepare','topic_produce'}:
        from core.topic_production import topic_stage
        return topic_stage(stage,day,output_dir,platform,payload,now=now)
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
    if stage == "xiaohongshu_notes":
        from core.copywriting import generate_batch

        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        with _lock(root / "note-writing.sqlite3"):
            return generate_batch(root, payload["topic"], payload["facts"], ("xiaohongshu",), payload["count"],
                                  None if payload.get("keyword_topic") is True else "xiaohongshu",
                                  illustrated=True, text_only=payload.get("text_only", False),
                                  photo_first=payload.get("photo_first", False), now=now,
                                  asset_paths=payload.get("asset_paths", ()), source_ids=payload.get('source_ids', ()))
    if stage == "analyze_case":
        from core.copywriting import analyze_case
        return analyze_case(platform, payload)
    if stage == "collect_writing_cases":
        from core.copywriting import collect_topic_cases
        return collect_topic_cases(output_dir, platform, payload)
    if stage == "refresh_writing_library":
        from core.copywriting import refresh_hotspot_library
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        with _lock(root / "writing-refresh.sqlite3"):
            return refresh_hotspot_library(root, now or dt.datetime.now().astimezone())
    if stage == "editorial_sources":
        from core.editorial import source_options
        from core.intake import read_intake
        stamp = now or dt.datetime.now().astimezone()
        previous = read_edition(edition_path(output_dir, day))["items"].get(platform, {}) if platform in PROFILES else {}
        return source_options(read_intake(output_dir, stamp), payload, stamp, recommend=False, local_rank=True,
                              saved=previous.get("editorial"))
    if stage == "editorial_collect":
        if platform == 'xiaohongshu':
            from core.copywriting import collect_topic_cases
            from core.editorial import source_options
            from core.intake import read_intake
            stamp=now or dt.datetime.now().astimezone()
            anchor=payload['anchor']
            chosen=source_options(read_intake(output_dir,stamp),anchor,stamp,recommend=False)['anchor']
            query=payload.get('query','').strip() or chosen['title']
            found=collect_topic_cases(output_dir,'xiaohongshu',{'topic':query},limit=3,photo_notes=True)
            result=source_options(read_intake(output_dir,stamp),anchor,stamp,recommend=False,local_rank=True)
            result.update(collected=len(found.get('source_ids',[])),collection_query=query,
                          searched_ids=found.get('source_ids',[]),warning=found.get('error',''))
            return result
        from core.intake import collect_topic_reports
        return collect_topic_reports(output_dir, payload["anchor"], now or dt.datetime.now().astimezone(),
                                     query=payload.get("query", ""))
    if stage == "editorial_link":
        from core.editorial import read_editorial_link

        return read_editorial_link(payload["url"], now or dt.datetime.now().astimezone(), output_dir=output_dir)
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
    if stage == "attach_search_media":
        from core.material_search import save_material

        if not isinstance(payload, dict) or not payload.get("id") or item.get("id") != payload.get("id"):
            raise RuntimeError("选题已更换，请重新选择要加入的工作稿。")
        if payload.get("source_media_id"):
            from core.source_media import save_source_media
            asset = save_source_media(output_dir, payload.get("source_report_id"), payload["source_media_id"])
        else:
            asset = save_material(output_dir, payload.get("page_id"))
        if item.get("editorial"):
            asset = {**asset, "chapters": [0]}
        return _merge_media(path, platform, [asset], expected_id=item["id"], reselect=True)
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
        from core.media_review import media_state

        if not isinstance(payload, dict) or payload.get("state") not in {"approved", "removed"}:
            raise ValueError("请选择要审核的素材。")
        url, state = payload.get("page_url"), payload["state"]

        def review(edition):
            current = edition["items"][platform]
            asset = next((entry for entry in current.get("media", []) if entry["page_url"] == url), None)
            if current.get("id") != payload.get("id") or not asset:
                raise RuntimeError("素材已更换，请刷新素材库。")
            previous = media_state(current, asset)
            if "report_id" in payload:
                report_id = payload["report_id"]
                reports = {report["id"] for report in current.get("editorial", {}).get("reports", [])}
                if (state != "approved" or asset.get("kind") != "image" or asset.get("comparison")
                        or not isinstance(report_id, str) or report_id and report_id not in reports):
                    raise ValueError("请选择这期稿件中的报道，并核对照片拍的是该事件。")
                if report_id != asset.get("verified_report_id", ""):
                    if report_id:
                        asset["verified_report_id"] = report_id
                    else:
                        asset.pop("verified_report_id", None)
                    _invalidate_opening(current)
            current.setdefault("media_review", {})[url] = state
            if (current.get("editorial") and 0 in asset.get("chapters", ())
                    and (state == "approved") != (previous == "approved")):
                _invalidate_opening(current)
            if state == "removed":
                removed = {url, *(row["page_url"] for row in current.get("media", [])
                                 if any(source["page_url"] == url for source in
                                        row.get("comparison", {}).get("sources", ())))}
                current["media_review"].update(dict.fromkeys(removed, "removed"))
                if removed.intersection(current.get("preview_media", ())):
                    current["preview_revision"] = None
                if removed.intersection(current.get("full_preview_media", ())):
                    current["full_preview_revision"] = None
                for name, revision in (("edit", "edit_revision"), ("suggestion", "suggestion_revision")):
                    shots = (current.get(name) or {}).get("scenes", [])
                    using = [shot for shot in shots if shot.get("media") in removed]
                    for shot in using:
                        shot.update(media="", zoom=False)
                        shot.pop("visual", None)
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
    if stage in {"direct_scene", "auto_cut"}:
        if not isinstance(payload, dict):
            raise ValueError("请重新打开当前草稿。")
        directory = path.parent / platform
        directory.mkdir(exist_ok=True)
        with _lock(path.parent / f"{platform}-selection.sqlite3"):
            current = read_edition(path)["items"].get(platform)
            _require_cut_version(current, payload)
            if stage == "direct_scene":
                with _lock(directory / "ai.sqlite3"), _lock(directory / "render.sqlite3"):
                    return _direct_scene(path, platform, current, directory, payload)
            try:
                return _auto_cut(path, output_dir, platform, current, directory, payload)
            except Exception as error:
                _update_item(path, platform, editorial_progress=str(error))
                raise
    if stage == "full_preview":
        if not isinstance(payload, dict) or not payload.get("revision"):
            raise RuntimeError("请先保存当前分镜后再生成全片试剪。")
        directory = path.parent / platform
        directory.mkdir(exist_ok=True)
        with _lock(path.parent / f"{platform}-selection.sqlite3"), _lock(directory / "render.sqlite3"):
            item = read_edition(path)["items"][platform]
            if (payload.get("id") != item.get("id") or payload["revision"] != item.get("edit_revision")
                    or not item.get("edit")):
                raise RuntimeError("分镜草稿已更新，请重新打开后再试剪。")
            return _render_preview(path, platform, item, directory, whole=True)
    if item.get("editorial") and stage in {"editorial_preview", "editorial_export", "visual_direct", "render"}:
        directory = path.parent / platform
        with _lock(path.parent / f"{platform}-selection.sqlite3"):
            item = read_edition(path)["items"][platform]
            if stage in {"editorial_preview", "visual_direct"}:
                options = payload if isinstance(payload, dict) else {"id": payload}
                return _prepare_editorial(path, output_dir, platform, directory, options.get("id"),
                                          rewrite=options.get("rewrite") is True,
                                          with_visuals=options.get("direct") is True, visual_only=stage == "visual_direct")
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
        else:
            from core.media_review import usable_media
            draft = item.get("edit") or default_cut({**item, "media": usable_media(item)}, platform)
        if isinstance(payload, dict) and payload.get("with_revision") is True:
            return {"draft": draft, "episode_id": item.get("id"), "edit_revision": item.get("edit_revision"),
                    "suggestion_revision": item.get("suggestion_revision")}
        return draft
    if stage in {"save", "apply_ai"}:
        from core.editing import validate_cut
        from core.media_review import usable_media

        def apply_cut(edition):
            current = edition["items"][platform]
            if payload.get("episode_id") and payload["episode_id"] != current.get("id"):
                raise RuntimeError("正在编辑的合辑已更换，请重新打开分镜。")
            if stage == "save" and (
                ("expected_revision" in payload and payload["expected_revision"] != current.get("edit_revision"))
                or ("expected_suggestion_revision" in payload
                    and payload["expected_suggestion_revision"] != current.get("suggestion_revision"))
            ):
                raise RuntimeError("分镜草稿已更新，请重新打开分镜后再保存。")
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
                    from_suggestion = (payload.get("draft_source") == "suggestion" or stage == "apply_ai"
                                       or not current.get("edit"))
                    origin = current.get("suggestion" if from_suggestion else "edit") or {}
                    citations = {mark["title"]: mark for mark in origin.get("chapter_starts", [])}
                    markers = [{**citations.get(mark["title"], {"source_ids": []}),
                                "title": mark["title"], "scene": mark["scene"]} for mark in markers]
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
    if stage in {"publish", "browser_post"}:
        package = item.get("package")
        if not package:
            raise RuntimeError("当前平台或成片不支持软件内投稿。")
        if item.get("edit_revision") != item.get("package_revision"):
            raise RuntimeError("草稿已更新，请先按当前草稿重新剪辑。")
        if stage == "publish" and (not isinstance(payload, dict) or not isinstance(payload.get("account"), str)
                                   or not payload["account"]):
            raise RuntimeError("请在桌面端确认投稿账号和成片。")
        if not isinstance(payload, dict) or payload.get("video") != package["video"]:
            raise RuntimeError("成片已更换，请播放当前版本后重新确认投稿。")
        if item.get("held_video") == package["video"]:
            raise RuntimeError("这版成片已暂缓，请先恢复发布资格。")
        release_dir = release_directory(path, platform, item)
        if stage == "browser_post":
            from core.browser_post import BROWSER_POST_PLATFORMS, prepare_browser_post

            if platform not in BROWSER_POST_PLATFORMS:
                raise RuntimeError("当前平台请在创作者后台手动投稿。")
            release_dir.mkdir(parents=True, exist_ok=True)
            with _lock(release_dir / "publish.sqlite3"):
                if (release_dir / "submitted.txt").is_file():
                    raise RuntimeError("这版成片已提交，请到创作者后台核对。")
                if (release_dir / "submitting.txt").exists():
                    raise RuntimeError("这版成片的提交结果待核对，请先查看创作者后台。")
                if open_post_panel is not None:
                    return prepare_browser_post(platform, package, timeout=660, open_panel=open_post_panel)
                return prepare_browser_post(platform, package)

        from core.news_release import submit_bilibili, submit_douyin

        submit = {"douyin": submit_douyin, "bilibili": submit_bilibili}.get(platform)
        if not submit:
            raise RuntimeError("当前平台或成片不支持软件内投稿。")
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
            if isinstance(payload, dict) and "comparison" in payload:
                from core.comparison_media import build_comparison
                from core.media_review import usable_media

                if item.get("id") != payload.get("id") or not isinstance(payload["comparison"], dict):
                    raise RuntimeError("选题已更换，请重新打开素材库。")
                asset = build_comparison(payload["comparison"], usable_media(item), media_dir, platform)
            elif isinstance(payload, dict):
                if not item.get("editorial") or item.get("id") != payload.get("id"):
                    raise RuntimeError("长片选题已更换，请重新打开片头。")
                asset = {**import_local_asset(payload.get("filename"), media_dir), "chapters": [0]}
            else:
                asset = import_local_asset(payload, media_dir)
            return _merge_media(path, platform, [asset])
        if stage == "media":
            return _prepare_media(path, platform, item, directory, opening_only=bool(item.get("editorial")))
        if stage == "ai":
            from core.editing import suggest_cut
            if platform == "douyin" and item.get("chapters") and item.get("target_seconds", 720) <= 180:
                from core.copywriting import retrieve_cases

                references = {ch["story"]["title"]: retrieve_cases(output_dir, platform, ch["story"]["title"],
                               ch["story"]["summary"][:900], actual_works=True) for ch in item["chapters"]}
                suggestion = suggest_cut(item, platform, references=references)
            elif item.get("selected_hotspots"):
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
            video_style = suggestion.get("editorial_plan", {}).get("video_style")
            if video_style and item.get("chapters"):
                from core.directing import direct_cut, production_models
                from core.media_review import usable_media

                if models := production_models():
                    suggestion = direct_cut(suggestion, usable_media(item), platform,
                                            release_directory(path, platform, item) / "visual", models,
                                            chapters=item["chapters"], video_style=video_style,
                                            brief=item.get('editorial',{}).get('brief'))
                _update_item(path, platform, video_style=video_style)
            _update_item(path, platform, suggestion=suggestion)
            return suggestion
        if stage == "preview":
            return _render_preview(path, platform, item, directory)
        return _render(path, platform, item, directory)


def _prepare_original_media(path, platform, item, output_dir):
    """Use the selected publisher's originals before planning, including legacy drafts."""
    from core.intake import _read_report
    from core.source_media import candidates
    from core.topic_production import collect_original_assets

    reports = []
    for report in item['editorial']['reports']:
        _update_item(path, platform, editorial_progress='读取报道原始素材 · ' + report['title'])
        original = report if candidates(report) else _read_report({**report, 'content_origin': 'feed'}, allow_older=True)
        reports.append({**original, 'id': report['id']})
    assets, warnings = collect_original_assets(reports, output_dir,
        lambda message: _update_item(path, platform, editorial_progress=message))
    if len(assets) < 3:
        import re
        from core.intake import _search_reports, has_report
        from core.daily_news import story_key
        title = item.get('story', {}).get('title') or reports[0]['title']
        names = re.findall(r'\b[A-Za-z][A-Za-z0-9.-]{2,}\b', title)
        query = ' '.join(names[:2]) if names else title[:72]
        _update_item(path, platform, editorial_progress='补找同题原始图片和视频 · ' + query)
        related = _search_reports({'title': title, 'search_query': query}, dt.datetime.now().astimezone(),
                                  reference=True, output_dir=output_dir)
        known = {report['url'] for report in reports}
        for extra in related[:4]:
            if extra['url'] in known or not has_report(extra) or not candidates(extra):
                continue
            extra = {**extra, 'id': story_key(extra), 'source_type': 'reference'}
            more, errors = collect_original_assets([extra], output_dir)
            warnings.extend(errors)
            if more:
                reports.append(extra); assets.extend(more); known.add(extra['url'])
            if len(assets) >= 6:
                break
    if not assets:
        warnings.append('已读取所选原文，未取得可用图片或视频；数据与过程将尝试生成有依据的解释动画。')
    _merge_media(path, platform, assets, expected_id=item['id'], auto_review=True,
        editorial={**item['editorial'], 'reports': reports}, original_media_checked=True,
        media_checked=True, opening_media_checked=True, warnings=warnings,
        editorial_progress='原始素材已核对，开始编写分镜')


def _prepare_media(path, platform, item, directory, *, opening_only=False):
    from core.news_media import download_story_media
    from core.video_styles import chapter_video_style, select_video_style

    editorial = item.get("editorial", {})
    selected_paths = set(editorial.get("brief", {}).get("asset_paths", ()))
    if selected_paths:
        chapters = item.get("chapters", [])
        assets = []
        for asset in item.get("media", []):
            if asset.get("brief_source") not in selected_paths:
                continue
            indexes = [i for i, chapter in enumerate(chapters) if any(
                report.get("url") == asset.get("source_page") for report in chapter.get("reports", ())) ]
            assets.append({**asset, "chapters": indexes or list(range(len(chapters)))})
        return _merge_media(path, platform, assets, opening_media_checked=True, media_checked=True)
    if editorial.get('reports') and editorial.get('mode') != 'original':
        _prepare_original_media(path, platform, item, path.parent.parent.parent)
        return read_edition(path)['items'][platform]['media']
    media_dir = directory / "media" / uuid.uuid4().hex
    media_dir.mkdir(parents=True)
    if item.get("editorial"):
        chapters = item.get("chapters")
        if not chapters:
            raise RuntimeError("请先生成长片章节，再查找片头素材。")
        assets, warnings = [], []
        reports = {report["id"]: report for chapter in (chapters[:1] if opening_only else chapters)
                   for report in chapter["reports"]}
        anchor = item["editorial"]["anchor"]
        for key, report in reports.items():
            indexes = [i for i, ch in enumerate(chapters) if any(r["id"] == key for r in ch["reports"])]
            style = chapter_video_style(platform, item.get("video_style"), chapters[indexes[0]],
                                        fixed=bool(item.get("video_style_request")))
            known = [a for a in item.get("media", []) if a.get("source_id") == key]
            if not known or opening_only or (0 in indexes and not item.get("opening_media_checked")):
                destination = media_dir / key
                destination.mkdir()
                found, errors = download_story_media(report, destination, hotspot=anchor if 0 in indexes else None,
                                                    episode=True, exclude={a["page_url"] for a in known},
                                                    **({"video_style": style} if style else {}))
                known.extend(found)
                warnings.extend(errors)
            assets.extend({**asset, "source_id": key, "chapters": indexes} for asset in known)
        return _merge_media(path, platform, assets, warnings=warnings, opening_media_checked=True,
                            media_checked=bool(item.get("media_checked") or not opening_only))
    if item.get("chapters"):
        assets, warnings = [], []
        for index, chapter in enumerate(item["chapters"]):
            destination = media_dir / str(index)
            destination.mkdir()
            style = select_video_style(platform, chapter["story"]["title"])
            found, errors = download_story_media(chapter.get("reports", [chapter["story"]])[0], destination,
                                                hotspot=chapter.get("hotspot"), episode=True,
                                                **({"video_style": style} if style else {}))
            assets.extend({**asset, "chapters": [index]} for asset in found)
            warnings.extend(errors)
    else:
        assets, warnings = download_story_media(item["story"], media_dir)
    return _merge_media(path, platform, assets, warnings=warnings, media_checked=True)


def _render_key(case):
    from studio_preferences import render_profile_key

    raw = json.dumps(["semantic-render-v17-spoken-sentences", case, render_profile_key()], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _render_trial(case, platform, output):
    from core.audio_engine import generate_voice_and_subtitles
    from core.reading import build_pages,capture_frame
    from core.video_engine import render_platform_video

    audio = generate_voice_and_subtitles(case, platform, output)
    video = render_platform_video(case, platform, audio, output)
    cover = output / "slide_00.png"
    poster = output / "preview-cover.jpg"
    if case["scenes"][0].get("layout") in {"opening", "footage", "graphic"} and (poster.is_file() or capture_frame(video, poster, .25)):
        cover = poster
    return {"video": str(video), "cover": str(cover), "duration": audio["total_duration"],
            'reading_pages':build_pages(case,audio,video)}


def _render_preview(path, platform, item, directory, *, draft=None, revision=None, whole=False):
    from core.editing import render_case

    draft = draft or item.get("edit") or (item.get("suggestion") if item.get("editorial") else None)
    if not draft or not (whole or item.get("selected_hotspots") or item.get("editorial")):
        raise RuntimeError("请先保存这期合辑的分镜。")
    case = render_case({**item, "edit": draft}, platform)
    folder = "full-preview" if whole else "preview"
    if not whole:
        opening = case["chapter_starts"][1]["scene"] if len(case["chapter_starts"]) > 1 else len(case["scenes"])
        case["scenes"] = case["scenes"][:min(8, opening)]
        case['chapter_starts']=[mark for mark in case['chapter_starts'] if mark['scene'] < len(case['scenes'])]
    output = directory / "production" / (item.get("id") or "draft") / folder / _render_key(case)
    previous = item.get("full_preview") or {}
    if (whole and item.get("full_preview_revision") == item["edit_revision"]
            and Path(previous.get("video", "")).is_file() and Path(previous["video"]).parent == output):
        if previous.get('reading_pages'):
            return previous
        from core.audio_engine import generate_voice_and_subtitles
        from core.reading import build_pages
        audio=generate_voice_and_subtitles(case,platform,output)
        preview={**previous,'reading_pages':build_pages(case,audio,Path(previous['video']))}
    else:
        preview = _render_trial(case, platform, output)
    used_media = sorted({asset['page_url'] for scene in case['scenes']
                         for asset in ([scene['asset']] if scene.get('asset') else [])
                         + [clip['asset'] for clip in (scene.get('visual') or {}).get('clips',[])]})
    if whole:
        def record(edition):
            current = edition["items"].get(platform) or {}
            if current.get("id") != item.get("id") or current.get("edit_revision") != item["edit_revision"]:
                raise RuntimeError("试剪期间草稿已更新，请重新生成全片试剪。")
            current.update(full_preview=preview, full_preview_media=used_media,
                           full_preview_revision=item["edit_revision"])

        _edit_edition(path, record)
    else:
        _update_item(path, platform, preview=preview, preview_media=used_media,
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
        # The version path already contains the platform and item ID.
        result = package_case_for_all_platforms(case, (platform,), output_root=output, flat=True)
        package = result["platforms"][platform]
    except Exception as error:
        _update_item(path, platform, status="ready" if item.get("package") else "failed", error=str(error))
        _notify_quality_failure(path, platform, item, None,
                                {"findings": [str(error)], "message": str(error)}, fingerprint=fingerprint)
        raise
    from core.quality import inspect_package, resolve_notices

    inspection = inspect_package(item, platform, package)
    _update_item(
        path, platform, status="ready", package=package, package_revision=item.get("edit_revision"),
        production_render_key=fingerprint, production_audit=inspection, quality_review=None,
        release_dir=str(release_directory(path, platform, item)), error=None,
    )
    if not inspection["ready"]:
        _update_item(path, platform, held_video=package["video"], error=inspection["message"])
        _notify_quality_failure(path, platform, item, package, inspection, fingerprint=fingerprint)
    else:
        try:
            resolve_notices(Path(path).resolve().parents[2], day=Path(path).parent.name, platform=platform)
        except Exception:
            pass
    return package


def _notify_quality_failure(path, platform, item, package, inspection, *, fingerprint=None):
    """Best-effort notice across local inbox, group webhook, operations, and team review."""
    if (item.get('topic_job') or {}).get('publish') is False:
        return
    fingerprint = fingerprint or item.get("production_render_key")
    try:
        from core.quality import dispatch_quality_notice

        output_dir = Path(path).resolve().parents[2]
        day = Path(path).parent.name
        title = ((package or {}).get("title") or (item.get("story") or {}).get("title") or platform)
        receipt = dispatch_quality_notice(
            output_dir, day, platform, title, inspection.get("findings") or [inspection.get("message", "")],
            source="auto", package=package, fingerprint=fingerprint,
        )
        _update_item(path, platform, quality_notice=receipt)
    except Exception as error:
        _update_item(path, platform, quality_notice={"render_key": fingerprint, "error": str(error)})


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
