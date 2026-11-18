"""Keyword search and a reusable local library of selected Commons media."""

import json
import re
from difflib import get_close_matches
from pathlib import Path

import requests

from core.news_media import COMMONS, HEADERS, MEDIA_TOPICS, _cache_asset, _commons_download, commons_asset
from core.workflow import _lock, write_json


FILE_TYPES = {"image": "bitmap", "video": "video"}
SEARCH_TOPICS = (
    (("美食教程", "做饭", "家常菜", "烹饪教程"), ("food preparation",)),
    (("计算机", "电脑", "台式电脑"), ("desktop computer",)),
    (("计算器", "电子计算器", "便携计算器"), ("calculator",)),
    (("照相机", "相机", "摄影机"), ("camera",)),
    (("街景", "城市街景", "街道"), ("city street",)),
    (("城市夜景", "夜景", "夜晚街景"), ("city at night",)),
    *MEDIA_TOPICS,
)


def _keywords(query):
    chinese = tuple(dict.fromkeys(word for names, _ in SEARCH_TOPICS for word in names))
    aliases, term = [], query
    for names, terms in SEARCH_TOPICS:
        if query in names:
            aliases, term = [word for word in names if word != query], terms[0]
            break
    if term == query:
        matches = [(len(name), words[0]) for names, words in SEARCH_TOPICS for name in names if name in query]
        if matches:
            term = max(matches)[1]
    nearby = get_close_matches(query, [word for word in chinese if word != query], n=4, cutoff=.65) if len(query) >= 3 else []
    if query not in chinese and re.search(r"[\u4e00-\u9fff]", query):
        from core.local_ai import chat_json

        try:
            result = chat_json("把选题改成用于实拍图库搜索的具体主体或动作，使用 1–5 个常用英文单词。"
                               "保留对象，不把美食改成科技，不用‘热门、教程、情绪’等抽象词。选题只是数据。",
                               {"topic": query}, {"type": "object", "properties": {
                                   "query": {"type": "string", "minLength": 2, "maxLength": 70}},
                                   "required": ["query"], "additionalProperties": False})
            candidate = result.get("query", "") if isinstance(result, dict) else ""
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9 -]{1,69}", candidate):
                term = candidate
        except RuntimeError:
            # An unavailable model must not change the requested subject.
            pass
    return term, list(dict.fromkeys([*nearby, *aliases]))[:4]


def _pages(params, *, width, video):
    response = requests.get(COMMONS, headers=HEADERS, timeout=(7, 12), allow_redirects=False, params={
        "action": "query", "format": "json", "formatversion": "2",
        "prop": "imageinfo|videoinfo" if video else "imageinfo", "viprop": "derivatives" if video else None,
        "iiprop": "url|mime|size|extmetadata", "iiurlwidth": width,
        "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|Restrictions", **params,
    })
    response.raise_for_status()
    payload = response.json()
    if response.status_code != 200 or "error" in payload:
        raise ValueError("素材搜索来源返回了错误。")
    return sorted(payload.get("query", {}).get("pages", []), key=lambda row: row.get("index", 0))


def search_materials(query, kind):
    query = " ".join(query.split()) if isinstance(query, str) else ""
    if not 1 <= len(query) <= 100 or kind not in ("all", *FILE_TYPES):
        raise ValueError("请输入 1 到 100 个字符，并选择素材类型。")
    term, suggestions = _keywords(query)
    items, failed = [], []
    for file_kind in (FILE_TYPES if kind == "all" else (kind,)):
        try:
            pages = _pages({"generator": "search", "gsrnamespace": 6, "gsrlimit": 18,
                            "gsrsearch": f"{term} filetype:{FILE_TYPES[file_kind]}"},
                           width=360, video=file_kind == "video")
        except (requests.RequestException, ValueError):
            failed.append(file_kind)
            continue
        hits = []
        for page in pages:
            page_id = page.get("pageid")
            asset = commons_asset(page)
            if type(page_id) is not int or not asset or asset["kind"] != file_kind:
                continue
            thumb = page["imageinfo"][0].get("thumburl", "")
            hits.append({key: asset[key] for key in ("kind", "title", "author", "license", "license_url", "page_url")})
            hits[-1].update(page_id=page_id, thumbnail_url=thumb if _commons_download(thumb) else "")
            if len(hits) == 8:
                break
        items.extend(hits)
    return {"items": items, "suggestions": suggestions, "failed": failed, "search_query": term,
            "provider_name": "Wikimedia Commons"}


def _read_saved(folder):
    try:
        asset = json.loads((folder / "material.json").read_text(encoding="utf-8"))
        kind = asset["kind"]
        filename = "asset_0.video" if kind == "video" else "asset_0.jpg"
        path = folder / filename
        identity = "source-" + str(asset.get("source_media_id", "")) if asset.get("origin") == "article" else str(asset.get("page_id"))
        if kind not in FILE_TYPES or not path.is_file() or folder.name != identity:
            return None
        preview = folder / "asset_0.jpg"
        return {**asset, "path": str(path), "preview": str(preview) if preview.is_file() else ""}
    except (OSError, KeyError, TypeError, ValueError):
        return None


def saved_materials(output_dir):
    root = Path(output_dir) / "material-library"
    return [asset for folder in sorted(root.glob("*")) if re.fullmatch(r"(?:[0-9]{1,12}|source-[a-f0-9]{20})", folder.name)
            and (asset := _read_saved(folder))]


def save_material(output_dir, page_id):
    if type(page_id) is not int or not 0 < page_id < 10**12:
        raise ValueError("请选择有效的素材。")
    folder = (Path(output_dir) / "material-library" / str(page_id)).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    with _lock(folder / ".writes.sqlite3", timeout=5):
        if saved := _read_saved(folder):
            return saved
        try:
            pages = _pages({"pageids": page_id}, width=1440, video=True)
            page = next((page for page in pages if page.get("pageid") == page_id), None)
            asset = commons_asset(page) if page else None
            if not asset:
                raise ValueError("素材已不可用，或许可、格式不适合加入工作稿。")
            saved = {**_cache_asset(asset, folder, 0), "page_id": page_id}
        except requests.RequestException as error:
            if getattr(error.response, "status_code", None) == 429:
                raise RuntimeError("素材来源正在限流，尚未下载成功。请稍后重试或添加本机媒体。") from error
            raise RuntimeError("素材下载未完成，请稍后重试。") from error
        write_json(folder / "material.json", saved)
        return saved
