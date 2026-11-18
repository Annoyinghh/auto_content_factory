"""Read the studio's saved works without creating or changing any records."""

import datetime as dt
import hashlib
import json
import math
import os
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath

from config import PLATFORM_PRESETS


@dataclass(frozen=True)
class Work:
    platform: str
    kind: str
    title: str
    day: str
    state: str
    preview: str = ""
    package: dict | None = None
    draft: dict | None = None
    pictures: tuple = ()
    document: str = ""
    directory: str = ""
    editable: bool = False
    id: str = ""


@dataclass(frozen=True)
class Album:
    title: str
    works: tuple[Work, ...]


def cleared_work_ids(directory):
    try:
        value = json.loads((directory / "cleared.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("作品库回收站记录无法读取。")
    return set(value)


def video_work_id(output_dir, video):
    root, path = Path(output_dir).resolve(), Path(video)
    key = os.path.normcase(str(path.relative_to(root) if path.is_relative_to(root) else path))
    return hashlib.sha256(json.dumps(("video", key), ensure_ascii=False).encode("utf-8")).hexdigest()


def _read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _file_candidates(directory, stored, *fallbacks):
    """Keep relocation and saved-path identity consistent, even after a file is removed."""
    candidates = []
    if isinstance(stored, str) and stored:
        parts = PureWindowsPath(stored).parts
        if directory.name in parts:
            anchor = parts.index(directory.name)
            candidates.append(directory.joinpath(*parts[anchor+1:]))
        path = Path(stored)
        if not path.is_absolute() and not PureWindowsPath(stored).is_absolute():
            candidates.append(directory.joinpath(*parts))
        candidates.extend(fallbacks)
        candidates.append(path)
    else:
        candidates.extend(fallbacks)
    return candidates


def _file(directory, stored, *fallbacks):
    """Prefer the current output tree when a saved record was moved with its files."""
    for path in _file_candidates(directory, stored, *fallbacks):
        if path.is_file():
            return str(path.resolve())
    return ""


def _date(value, path):
    try:
        return dt.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        try:
            return dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
        except (OSError, OverflowError):
            return ""


def _video(platform, kind, topic, day, package, directory, state, *, editable=False, platform_dir=None):
    if not isinstance(package, dict) or not package.get("video"):
        return None
    candidates = (platform_dir / f"video_{platform}.mp4",) if platform_dir else ()
    video = _file(directory, package["video"], *candidates)
    if not video:
        return None
    parent = Path(video).parent
    cover = _file(directory, package.get("cover"), parent / "cover.png", parent / "slide_00.png")
    return Work(platform, kind, package.get("title") or topic, day, state, cover,
                package={**package, "video": video, "cover": cover}, directory=str(parent), editable=editable)


def _state(item, row):
    state = row.get("state")
    if state in {"已提交审核", "待核对 · 不重发", "草稿已更新", "已暂缓"}:
        return state
    if item.get("edit_revision") != item.get("package_revision"):
        return "草稿已更新"
    if item.get("held_video") and item["held_video"] == (item.get("package") or {}).get("video"):
        return "已暂缓"
    return "待审片"


def _draft_pictures(directory, draft, number):
    pictures = []
    for picture in draft.get("pictures") or ():
        if isinstance(picture, dict) and (file := _file(directory, picture.get("file"))):
            pictures.append({**picture, "file": file})
    if not pictures and draft.get("pages"):
        # The older note exporter stored rendered pages next to batch.json without a picture index.
        legacy = directory / f"{number:02d}-配图"
        pictures = [{"file": str(path)} for path in sorted(legacy.glob("*"))
                    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and path.is_file()]
    return tuple(pictures)


def _topic_key(title):
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def _day_work(path, edition, release_rows, active_day):
    day = _date(edition.get("date") or path.parent.name, path)
    current = edition.get("items") or {}
    previous = edition.get("previous_items") or {}
    for platform in PLATFORM_PRESETS:
        historical = [(item, True) for item in reversed(previous.get(platform, ()))]
        for item, old in [(current.get(platform, {}), False), *historical]:
            topic = (item.get("story", {}).get("title") or "").strip()
            if not topic:
                continue
            editable = not old and day == active_day
            row = release_rows.get(platform, {}) if editable else {}
            preview_added = False
            full_preview_added = False
            full_state = ('历史全片试剪' if old else '全片试剪' if
                          item.get('full_preview_revision') == item.get('edit_revision') else '全片试剪 · 草稿已更新')
            for kind, name, state in (
                    ("video", "package", "历史版本" if old else _state(item, row)),
                    ('preview','full_preview',full_state),
                    ("preview", "preview", "历史试剪" if old else "试剪")):
                if kind == 'preview' and preview_added:
                    continue
                work = _video(platform, kind, topic, day, item.get(name), path.parent, state, editable=editable)
                if work:
                    yield topic, work
                    preview_added = preview_added or kind == 'preview'
                    full_preview_added = full_preview_added or name == 'full_preview'
            if editable and not full_preview_added and (item.get("edit") or item.get("suggestion") or item.get("chapters")):
                # Only the live draft can be opened in the editor.
                if not item.get("package") or _state(item, row) == "草稿已更新":
                    yield topic, Work(platform, "production", topic, day, "草稿", editable=True)


def load_works(output_dir, *, edition=None, day=None, release_rows=None):
    """Join platform outputs by their saved topic; include archived and relocated files."""
    root = Path(output_dir).resolve()
    active_day = str(day) if day else None
    albums = defaultdict(list)
    titles = {}
    seen_videos = set()

    def path_key(path):
        path = Path(path)
        return os.path.normcase(str(path.relative_to(root) if path.is_relative_to(root) else path))

    def add(topic, work, *source):
        if work.package:
            video = path_key(work.package["video"])
            if video in seen_videos:
                return
            seen_videos.add(video)
            source = ("video", video)
        key = _topic_key(topic)
        identifier = (video_work_id(root, work.package["video"]) if work.package else
                      hashlib.sha256(json.dumps(source, ensure_ascii=False).encode("utf-8")).hexdigest())
        titles.setdefault(key, topic)
        albums[key].append(replace(work, id=identifier))

    if edition is not None and active_day:
        path = root / "daily" / active_day / "edition.json"
        for topic, work in _day_work(path, edition, release_rows or {}, active_day):
            add(topic, work, "production", path_key(path), work.platform, _topic_key(topic))
    for path in sorted(root.glob("daily/*/edition.json"), reverse=True):
        if edition is None or path.parent.name != active_day:
            for topic, work in _day_work(path, _read(path), release_rows or {}, active_day):
                add(topic, work, "production", path_key(path), work.platform, _topic_key(topic))

    for pattern in ("*/manifest.json", "daily/*/*/manifest.json",
                    "daily/*/*/versions/*/manifest.json",
                    "daily/*/*/versions/*/*/manifest.json", "daily/*/*/versions/*/*/*/manifest.json"):
        for path in root.glob(pattern):
            manifest = _read(path)
            topic = (manifest.get("title") or "").strip()
            if not topic:
                continue
            parts = path.relative_to(root).parts
            made = _date(parts[1] if parts[0] == "daily" else None, path)
            for platform, package in (manifest.get("platforms") or {}).items():
                if platform in PLATFORM_PRESETS:
                    work = _video(platform, "video", topic, made, package, path.parent, "已导出",
                                  platform_dir=path.parent / platform)
                    if work:
                        add(topic, work)

    for path in sorted(root.glob("copywriting/*/batch.json"), reverse=True):
        batch = _read(path)
        topic = (batch.get("topic") or "").strip()
        if not topic:
            continue
        stamp = path.parent.name[:8]
        made = _date(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}", path)
        for platform, result in (batch.get("results") or {}).items():
            if platform not in PLATFORM_PRESETS:
                continue
            document = _file(path.parent, result.get("file"), path.parent / f"{platform}.md")
            for number, draft in enumerate(result.get("drafts") or (), 1):
                if not draft.get("title") or not (draft.get("script") or draft.get("caption")):
                    continue
                pictures = () if batch.get("text_only") else _draft_pictures(path.parent, draft, number)
                kind = "image" if pictures else "text" if platform == "xiaohongshu" else "copy"
                add(topic, Work(platform, kind, draft["title"], made, "草稿",
                                pictures[0]["file"] if pictures else "", draft=draft, pictures=pictures,
                                document=document, directory=str(path.parent)),
                    "draft", path_key(path), platform, number)

    result = [Album(titles[key], tuple(sorted(works, key=lambda work: work.day, reverse=True)))
              for key, works in albums.items()]
    return tuple(sorted(result, key=lambda album: max(work.day for work in album.works), reverse=True))


def duration_label(package):
    try:
        seconds = float(package.get("duration"))
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(seconds) or seconds <= 0:
        return ""
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes:02d}:{seconds:02d}"
