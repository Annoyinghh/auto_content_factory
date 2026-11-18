"""Media actually present in a collected article, kept with its adjacent explanation."""

import datetime as dt
import hashlib
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PIL import Image, ImageOps
from requests import RequestException
from core.news_media import _get_file


def candidates(report):
    from core.daily_news import story_key
    page = report.get("url", "")
    parts = urlsplit(page)
    xhs = (parts.hostname == 'www.xiaohongshu.com' and report.get('source_platform') == 'xiaohongshu'
           and re.fullmatch(r'/explore/[a-f0-9]{24}/?', parts.path)
           and parts.path.rstrip('/').rsplit('/',1)[1] == report.get('source_note_id'))
    news = (parts.hostname == 'www.news.cn'
            and re.fullmatch(r'/(?:[a-zA-Z0-9_-]+/)*\d{8}/[a-f0-9]{32}/c\.html', parts.path))
    tech = parts.hostname == 'www.ithome.com' and re.fullmatch(r'/\d+/\d+/\d+\.htm',parts.path)
    if (parts.scheme != "https" or not (xhs or news or tech or parts.hostname in {"www.sohu.com", "m.sohu.com"})
            or parts.username or parts.password or parts.port not in {None, 443}):
        return []
    found, seen = [], set()
    for row in report.get("media_candidates", [])[:64]:
        if not isinstance(row, dict):
            continue
        try:
            link = urlsplit(row.get("url", ""))
            # Only the publisher's observed media CDNs, never URLs supplied by article prose.
            video = row.get('kind') == 'video' and not xhs
            if xhs:
                allowed = link.hostname == 'sns-webpic-qc.xhscdn.com' and row.get('kind') == 'image'
            elif tech:
                allowed = not video and link.hostname == 'img.ithome.com' and link.path.startswith('/newsuploadfiles/')
            elif news:
                allowed = (re.fullmatch(r'vodpub[1-9]\d?\.v\.news\.cn', link.hostname or '') if video else
                           link.hostname in {'www.news.cn', 'imgs.news.cn'} or re.fullmatch(r'vodpub[1-9]\d?\.v\.news\.cn', link.hostname or ''))
            else:
                allowed = re.fullmatch(r'\d+-\d+\.vod\.tv\.itc\.cn' if video else r'p[0-9]\.itc\.cn', link.hostname or '')
            if (link.scheme not in {"http", "https"} or not allowed
                    or link.username or link.password or link.port not in {None, 80, 443}):
                continue
            suffix = Path(link.path).suffix.lower()
            if not xhs and suffix not in ({".mp4"} if video else {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}):
                continue
            url = urlunsplit(("https", link.hostname, link.path, link.query, ""))
            thumbnail = '' if video else url
            if news and video and row.get('poster'):
                poster = urlsplit(row['poster'])
                if (poster.scheme == 'https' and poster.hostname == link.hostname and not poster.username
                        and not poster.password and poster.port in {None, 443}
                        and Path(poster.path).suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}):
                    thumbnail = row['poster']
        except (ValueError, TypeError):
            continue
        identity = link.path.rsplit('/',1)[-1].split('!',1)[0] if xhs else link.path if video else url
        if xhs and not re.fullmatch(r'[A-Za-z0-9_-]{12,150}',identity):
            continue
        if identity in seen:
            continue
        seen.add(identity)
        source_identity = urlunsplit((parts.scheme,parts.hostname,parts.path,'','')) if xhs else page
        key = hashlib.sha256((source_identity + "\n" + identity).encode()).hexdigest()[:20]
        context = str(row.get("context", ""))[:350]
        title = context or str(row.get("title") or f"{report['title']} · 原文配图 {len(found)+1}")
        found.append({"origin": "article", "source_media_id": key, "source_report_id": story_key(report), "source_page": page,
                      "page_url": page.split("#", 1)[0] + "#acf-media-" + key,
                      "kind": "video" if video else "animation" if suffix == ".gif" else "image", "title": title[:300],
                      "context": context, "author": report.get("author") or "原文作者未标注",
                      "source_summary": str(report.get('summary',''))[:6000],
                      "source_title": report['title'], "source_published": report.get('published',''),
                      "license": "原文媒体 · 使用范围待核对", "license_url": page, "rights": "unverified",
                      "download_url": url, "thumbnail_url": thumbnail,
                      "width": row.get("width", 0), "height": row.get("height", 0)})
    return found


def _with_thumbnails(assets, output_dir):
    from concurrent.futures import ThreadPoolExecutor
    from core.workflow import _lock
    folder = Path(output_dir).resolve() / '.source-thumbnails'
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = dt.datetime.now().timestamp() - 7*86400
    with _lock(folder/'.writes.sqlite3', timeout=120):
        for old in folder.glob('*.jpg'):
            if re.fullmatch(r'[a-f0-9]{20}\.jpg',old.name) and not old.is_symlink() and old.stat().st_mtime < cutoff:
                old.unlink()
        def preview(asset):
            if asset.get('kind') == 'video' and not asset.get('thumbnail_url'):
                return asset
            target = folder/(asset['source_media_id']+'.jpg')
            if target.is_file():
                return {**asset,'preview':str(target)}
            raw, temporary = target.with_suffix('.download'), target.with_suffix('.tmp')
            try:
                _get_file(asset.get('thumbnail_url') or asset['download_url'],raw,32_000_000)
                with Image.open(raw, formats=('JPEG','PNG','WEBP','AVIF','GIF')) as image:
                    if image.width*image.height > 40_000_000 or min(image.size)<160:
                        return asset
                    image.draft('RGB',(640,640))
                    photo=ImageOps.exif_transpose(image).convert('RGB')
                    photo.thumbnail((320,320))
                    photo.save(temporary,format='JPEG',quality=88)
                temporary.replace(target)
                return {**asset,'preview':str(target)}
            except (OSError,ValueError,RequestException,Image.DecompressionBombError):
                return asset
            finally:
                raw.unlink(missing_ok=True)
                temporary.unlink(missing_ok=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(preview,assets))


def cache_candidate(report, candidate, output_dir):
    from config import get_ffmpeg
    from core.material_search import _read_saved
    from core.workflow import _lock, write_json

    # Resolve again from the report, so callers cannot inject a download URL or output path.
    asset = next((row for row in candidates(report) if row["source_media_id"] == candidate.get("source_media_id")), None)
    if not asset:
        raise ValueError("原文媒体已变化或不存在，请重新读取原文。")
    folder = Path(output_dir).resolve() / "material-library" / ("source-" + asset["source_media_id"])
    folder.mkdir(parents=True, exist_ok=True)
    with _lock(folder / ".writes.sqlite3", timeout=15):
        if saved := _read_saved(folder):
            saved.update({key:asset[key] for key in ('source_page','page_url','download_url','license_url',
                'source_title','source_summary','source_published','author','context','title')})
            write_json(folder/'material.json',saved)
            return saved
        raw, preview, video = folder / ".original.download", folder / "asset_0.jpg", folder / "asset_0.video"
        try:
            if asset["kind"] == "video":
                _get_file(asset["download_url"], raw, 250_000_000)
                result = subprocess.run([get_ffmpeg(), "-nostdin", "-hide_banner", "-y",
                    "-protocol_whitelist", "file", "-f", "mov", "-i", str(raw), "-frames:v", "1",
                    "-vf", "scale='min(1920,iw)':-2", str(preview)], capture_output=True, timeout=45,
                    encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
                dimensions = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", result.stderr)
                if result.returncode or not preview.is_file() or not duration or not dimensions:
                    raise ValueError("原文视频无法解码，可能只是播放器初始化文件，未加入素材库。")
                duration = sum(float(value)*factor for value, factor in zip(duration.groups(), (3600,60,1)))
                width, height = map(int, dimensions.groups())
                if not 0 < duration <= 7200 or min(width,height) < 160 or width*height > 24_000_000:
                    raise ValueError("原文视频时长或尺寸异常。")
                raw.replace(video)
                asset.update(path=str(video), preview=str(preview), width=width, height=height, duration=duration,
                             origin_format="mp4", video_format="mov",
                             resolution_note="原片较小，请先审看" if min(width,height) < 720 else "原始清晰度",
                             collected_at=dt.datetime.now(dt.timezone.utc).isoformat())
                write_json(folder / "material.json", asset)
                return asset
            _get_file(asset["download_url"], raw, 32_000_000)
            with Image.open(raw, formats=("JPEG", "PNG", "GIF", "WEBP", "AVIF")) as image:
                # MPO JPEGs can contain HDR gain maps; retain their primary photograph.
                frames = 1 if image.format == 'MPO' else getattr(image, "n_frames", 1)
                if image.width * image.height > (24_000_000 if frames>1 else 40_000_000) or min(image.size) < 160:
                    raise ValueError(f"原文媒体尺寸不适合剪辑：{image.width}×{image.height}。请检查原图，不使用微型占位图。")
                width, height = image.size
                if frames > 3000:
                    raise ValueError("动图帧数过多，请选择其他片段。")
                if frames == 1:
                    image.draft('RGB',(1920,1920))
                photo = ImageOps.exif_transpose(image).convert("RGB")
                photo.thumbnail((1920, 1920))
                photo.save(preview, quality=93)
                duration = 0
                if image.format == "GIF" and frames > 1:
                    for index in range(frames):
                        image.seek(index)
                        duration += max(10, image.info.get("duration", 100)) / 1000
                    duration = min(60, duration)
            if frames > 1:
                if Path(urlsplit(asset["download_url"]).path).suffix.lower() != ".gif":
                    raise ValueError("这种动画格式尚未支持，未将它冒充静态照片。")
                result = subprocess.run([get_ffmpeg(), "-nostdin", "-loglevel", "error", "-y",
                    "-protocol_whitelist", "file", "-ignore_loop", "1", "-f", "gif", "-i", str(raw),
                    "-an", "-t", str(duration), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(video)],
                    capture_output=True, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode or not video.is_file():
                    raise ValueError("原文动图未能转换成可剪辑片段。")
                asset.update(kind="video", origin_format="gif", video_format="mov", duration=duration)
            else:
                asset.update(kind="image", video_format=None)
            asset.update(path=str(video if frames > 1 else preview), preview=str(preview), width=width, height=height,
                         resolution_note="原图较小，请先审看" if min(width, height) < 720 else "原始清晰度",
                         collected_at=dt.datetime.now(dt.timezone.utc).isoformat())
            write_json(folder / "material.json", asset)
            return asset
        except Exception:
            preview.unlink(missing_ok=True)
            video.unlink(missing_ok=True)
            raise
        finally:
            raw.unlink(missing_ok=True)


def source_options(output_dir, report_id):
    from core.intake import read_intake
    from core.daily_news import story_key
    from core.editorial_link import editorial_link_id

    report = next((row for row in read_intake(output_dir)["articles"]
                   if report_id in {story_key(row), editorial_link_id(row["url"])}), None)
    if not report:
        raise ValueError("原文已变化或不存在，请重新选择采集资料。")
    assets = candidates(report)
    if assets and (report.get('source_platform') == 'xiaohongshu' or urlsplit(report['url']).hostname == 'www.news.cn'):
        assets = _with_thumbnails(assets, output_dir)
    return report, assets


def save_source_media(output_dir, report_id, media_id):
    from core.material_search import _read_saved

    if not isinstance(media_id, str) or not re.fullmatch(r"[a-f0-9]{20}", media_id):
        raise ValueError("请选择有效的原文媒体。")
    cached = _read_saved(Path(output_dir).resolve() / "material-library" / ("source-" + media_id))
    if cached and cached.get("source_report_id") == report_id:
        return cached
    report, options = source_options(output_dir, report_id)
    candidate = next((row for row in options if row["source_media_id"] == media_id), None)
    if not candidate:
        raise ValueError("原文媒体已变化或不存在，请重新选择。")
    return cache_candidate(report, candidate, output_dir)
