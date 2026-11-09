"""Cache local media and reusable illustrations from Wikimedia Commons."""

import html
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps

from config import get_ffmpeg

COMMONS = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "AutoContentFactory/1.0 (Windows editorial desktop; licensed media research)"}
MEDIA_TOPICS = (
    (("赛里木湖",), ("sayram lake", "sailimu lake")),
    (("南极", "南极考察"), ("antarctica", "antarctic")),
    (("旅行", "旅游", "风景"), ("landscape", "tourism")),
    (("海鸥",), ("seagull", "gull")),
    (("足球", "中超", "蓉城"), ("football stadium", "football ground", "soccer stadium")),
    (("篮球", "湖人", "NBA", "开拓者", "杨瀚森"), ("basketball court", "basketball arena")),
    (("英雄联盟", "电竞", "游戏", "崩坏", "星穹铁道"), ("video game", "esports", "gaming")),
    (("进博会", "博览会", "展会"), ("china international import expo", "trade fair")),
    (("美食", "烹饪", "立冬吃什么"), ("chinese cuisine", "chinese food")),
    (("机器狗", "四足机器人"), ("robot dog", "robotic dog", "quadruped robot")),
    (("机器人",), ("robot", "robotics")),
    (("半导体", "芯片", "晶圆"), ("semiconductor", "microchip", "silicon wafer")),
    (("新能源汽车", "电动汽车", "电动车"), ("electric car", "electric vehicle")),
    (("手机",), ("smartphone", "mobile phone")),
    (("笔记本电脑", "笔电"), ("laptop", "notebook computer")),
    (("智能眼镜", "增强现实"), ("smart glasses", "augmented reality")),
    (("人工智能", "大模型"), ("artificial intelligence", "machine learning")),
    (("钻机", "深井", "地下万米"), ("drilling rig", "drilling platform")),
    (("超市", "便利店"), ("supermarket", "convenience store")),
    (("招聘", "求职", "面试"), ("job interview", "recruitment")),
    (("远程办公", "共享办公"), ("coworking", "home office")),
    (("物流", "仓储"), ("logistics", "warehouse")),
)
LICENSES = {
    "CC BY 2.0": "https://creativecommons.org/licenses/by/2.0/",
    "CC BY 2.5": "https://creativecommons.org/licenses/by/2.5/",
    "CC BY 3.0": "https://creativecommons.org/licenses/by/3.0/",
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "Public domain": "https://creativecommons.org/publicdomain/mark/1.0/",
}


def _host_is(url, host):
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc == host


def _commons_download(url):
    return any(_host_is(url, host) for host in ("upload.wikimedia.org", "thumb.wikimedia.org"))


def commons_asset(page):
    files = page.get("imageinfo", [])
    if not files:
        return None
    info = files[0]
    metadata = info.get("extmetadata", {})

    def field(name):
        value = html.unescape(metadata.get(name, {}).get("value", ""))
        return " ".join(re.sub(r"<[^>]+>", " ", value).split())

    license_name, author = field("LicenseShortName"), field("Artist")
    license_url = LICENSES.get(license_name)
    if not license_url or field("Restrictions") or not author:
        return None
    if license_name != "Public domain":
        supplied_license = field("LicenseUrl").replace("http://", "https://", 1).rstrip("/")
        if supplied_license != license_url.rstrip("/") and not supplied_license.startswith(license_url + "deed."):
            return None
    mime = info.get("mime")
    if mime not in {"image/jpeg", "image/png", "video/webm", "video/mp4"}:
        return None
    kind = "video" if mime.startswith("video/") else "image"
    image_url = info.get("thumburl") or info.get("url", "")
    download_url = info.get("url", "") if kind == "video" else image_url
    page_url = info.get("descriptionurl", "")
    if not _commons_download(download_url) or not _host_is(page_url, "commons.wikimedia.org"):
        return None
    if kind == "video" and info.get("size", 0) > 24_000_000:
        return None
    return {
        "kind": kind, "title": page["title"].removeprefix("File:"),
        "author": author, "license": license_name, "license_url": license_url,
        "page_url": page_url, "download_url": download_url,
        "thumbnail_url": image_url if kind == "video" and image_url != download_url and _commons_download(image_url) else "",
        "video_format": {"video/webm": "matroska", "video/mp4": "mov"}.get(mime),
    }


def _get_file(url, target, limit):
    with requests.get(url, headers=HEADERS, stream=True, timeout=(7, 15), allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f"Commons 素材 HTTP {response.status_code}")
        size = 0
        with target.open("wb") as handle:
            for block in response.iter_content(65536):
                size += len(block)
                if size > limit:
                    raise ValueError("Commons 素材超过大小限制")
                handle.write(block)


def _cache_asset(asset, directory, index):
    preview = directory / f"asset_{index}.jpg"
    if asset["kind"] == "video":
        video = directory / f"asset_{index}.video"
        _get_file(asset["download_url"], video, 24_000_000)
        # The Commons thumbnail is optional. Only the render worker decodes footage.
        try:
            if asset["thumbnail_url"]:
                _cache_thumbnail(asset["thumbnail_url"], preview)
        except (OSError, ValueError, requests.RequestException):
            preview.unlink(missing_ok=True)
        return {**asset, "path": str(video), "preview": str(preview) if preview.exists() else ""}
    _cache_thumbnail(asset["download_url"], preview)
    return {**asset, "path": str(preview), "preview": str(preview)}


def _cache_thumbnail(url, preview):
    raw = preview.with_suffix(".download")
    try:
        _get_file(url, raw, 8_000_000)
        _normalize_photo(raw, preview)
    finally:
        raw.unlink(missing_ok=True)


def preview_video_asset(asset):
    source = Path(asset["path"])
    video_format = asset.get("video_format")
    if not source.is_file() or asset.get("kind") != "video" or video_format not in {"mov", "matroska"}:
        raise ValueError("找不到可播放的视频素材。")
    if asset.get("local"):
        return str(source)
    output = source.with_suffix(".review.mp4")
    if output.is_file() and output.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return str(output)
    temp = output.with_suffix(f".{uuid.uuid4().hex}.mp4")
    try:
        result = subprocess.run(
            [get_ffmpeg(), "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file", "-f", video_format,
             "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-t", "30", "-vf", "scale='min(960,iw)':-2",
             "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(temp)],
            capture_output=True, timeout=65, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode or not temp.is_file():
            raise RuntimeError("这条素材无法生成审看预览。")
        temp.replace(output)
        return str(output)
    finally:
        temp.unlink(missing_ok=True)


def _normalize_photo(source, preview):
    with Image.open(source, formats=("JPEG", "PNG")) as image:
        if image.width * image.height > 24_000_000:
            raise ValueError("图片像素数超限")
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        normalized.thumbnail((1600, 1600))
        normalized.save(preview, quality=90)


def import_local_asset(filename, directory):
    source = Path(filename)
    extension = source.suffix.lower()
    video_format = {".mp4": "mov", ".mov": "mov", ".webm": "matroska"}.get(extension)
    if not video_format and extension not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("请选择 JPG、PNG、MP4、MOV 或 WebM 文件。")
    if not source.is_file() or not 0 < source.stat().st_size <= 250_000_000:
        raise ValueError("文件不存在、为空或超过 250 MB。")

    directory.mkdir(parents=True)
    preview = directory / "preview.jpg"
    target = directory / f"clip{extension}" if video_format else preview
    try:
        if video_format:
            shutil.copyfile(source, target)
            subprocess.run(
                [get_ffmpeg(), "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file",
                 "-f", video_format, "-i", str(target), "-map", "0:v:0", "-frames:v", "1",
                 "-vf", "scale=960:960:force_original_aspect_ratio=decrease", str(preview)],
                check=True, capture_output=True, timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if not preview.is_file():
                raise ValueError("视频没有可用画面。")
        else:
            _normalize_photo(source, preview)
    except (OSError, ValueError, Image.DecompressionBombError, subprocess.SubprocessError) as error:
        target.unlink(missing_ok=True)
        preview.unlink(missing_ok=True)
        directory.rmdir()
        raise ValueError("无法读取素材，请检查图片尺寸或确认视频可播放。") from error
    return {
        "local": True, "title": source.name, "page_url": f"local:{directory.name}",
        "kind": "video" if video_format else "image", "video_format": video_format,
        "path": str(target), "preview": str(preview),
    }


def download_story_media(story, directory, *, hotspot=None, episode=False):
    """Search for named subjects first and only use licensed, relevant results."""
    headline = re.split(r"\s+-\s+|_", story["title"], maxsplit=1)[0]
    context = f"{headline} {(hotspot or {}).get('title', '')}"
    aliases = next((names for keywords, names in MEDIA_TOPICS if any(word in context for word in keywords)), ())
    named = re.findall(r"《([^》]{3,30})》|([A-Za-z][A-Za-z0-9-]+(?: [A-Za-z0-9-]+){1,3})", context)
    place = re.search(r"(?:拍到(?:了)?|走进|探访|来到)([\u4e00-\u9fff]{2,6}?(?:湖|湾|山|岛|海|河|镇|谷))(?=的|[，。！!？?]|$)", context)
    exact = list(dict.fromkeys((work or name).casefold() for work, name in named))
    if place:
        exact.append(place[1])
    if episode and not exact and 4 <= len(headline) <= 30 and not aliases:
        exact.append(headline.casefold())
    groups = [(term, tuple(exact), True) for term in exact[:1]]
    groups.extend((term, aliases, False) for term in aliases[:2])
    assets, warnings = [], []
    seen = set()
    for kind, filetype, limit in (("image", "bitmap", 2 if episode else 1), ("video", "video", 1)):
        count = 0
        for search_term, terms, named_subject in groups:
            query = '"' + search_term.replace('"', '') + '" filetype:' + filetype
            try:
                response = requests.get(
                    COMMONS, headers=HEADERS, timeout=(7, 15), params={
                        "action": "query", "format": "json", "formatversion": "2",
                        "generator": "search", "gsrnamespace": "6", "gsrsearch": query,
                        "gsrlimit": "18", "prop": "imageinfo", "iiprop": "url|mime|size|extmetadata",
                        "iiurlwidth": "1440",
                        "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|Restrictions|ImageDescription",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if "error" in payload:
                    raise ValueError("Commons 返回了查询错误")
                pages = sorted(payload.get("query", {}).get("pages", []), key=lambda page: page.get("index", 0))
            except (ValueError, requests.RequestException) as error:
                warnings.append(f"Commons 采集失败 · {type(error).__name__}")
                continue
            for page in pages:
                asset = commons_asset(page)
                if not asset or asset["kind"] != kind or asset["page_url"] in seen:
                    continue
                metadata = page["imageinfo"][0]["extmetadata"]
                description = metadata.get("ImageDescription", {}).get("value", "")
                caption = re.sub(r"<[^>]+>", " ", html.unescape(description))[:150] if named_subject else ""
                searchable = f"{asset['title']} {caption}".casefold()
                if not any(term in searchable for term in terms):
                    continue
                try:
                    assets.append(_cache_asset(asset, Path(directory), len(assets)))
                except (OSError, ValueError, requests.RequestException) as error:
                    warnings.append(f"素材读取失败 · {type(error).__name__}")
                    continue
                seen.add(asset["page_url"])
                count += 1
                if count == limit:
                    break
            if count == limit:
                break
    return assets, list(dict.fromkeys(warnings))
