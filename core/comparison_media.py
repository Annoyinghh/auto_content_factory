"""Compose a confirmed comparison pair as a single, credited workbench asset."""

import math
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from config import PLATFORM_PRESETS, get_ffmpeg
from core.visual_engine import get_font


DETAIL_REGIONS = {
    "center": (1, 1, "中心"),
    "top-left": (0, 0, "左上"), "top-right": (2, 0, "右上"),
    "bottom-left": (0, 2, "左下"), "bottom-right": (2, 2, "右下"),
}


def matches_comparison(asset, narration):
    comparison = asset.get("comparison")
    if not comparison:
        return False
    text = "".join(narration.casefold().split())
    condition = "".join(comparison["condition"].casefold().split())
    if condition not in text or re.search(r"(?:不是|并非|没有使用)" + re.escape(condition), text):
        return False
    spans = []
    for label in comparison["labels"]:
        label = "".join(label.casefold().split())
        pattern = (r"(?<!\d)" if label[0].isdigit() else "") + re.escape(label)
        if label[-1].isdigit():
            pattern += r"(?!\d)"
        spans.append([match.span() for match in re.finditer(pattern, text)])
    return any(a[1] <= b[0] or b[1] <= a[0] for a in spans[0] for b in spans[1])


def select_comparison(assets, narration):
    wants_detail = bool(re.search(r"放大|局部", narration))
    region = next((key for key, (_, _, label) in DETAIL_REGIONS.items() if wants_detail and label in narration), "")
    pairs = (asset for asset in assets if matches_comparison(asset, narration)
             and (not region or asset["comparison"].get("region") == region))
    return min(pairs, key=lambda asset: bool(asset["comparison"].get("region")) != wants_detail, default=None)


def _plate(platform, condition, labels, region):
    preset = PLATFORM_PRESETS[platform]
    width, height = preset["width"], preset["height"]
    wide = width > height
    image = Image.new("RGB", (width, height), "#101A21")
    pen = ImageDraw.Draw(image)
    margin, gap = round(width * .055), round(width * .036)
    first, bottom = round(height * (.23 if wide else .19)), round(height * .78)
    if wide:
        panel_width = (width - 2 * margin - gap) // 2
        boxes = [(margin + i * (panel_width + gap), first, panel_width, bottom - first) for i in range(2)]
    else:
        panel_height = (bottom - first - 104) // 2
        boxes = [(margin, first + i * (panel_height + 104), width - 2 * margin, panel_height) for i in range(2)]
    view = f"{DETAIL_REGIONS[region][2]}细节 ×3" if region else "同屏对照"
    heading = f"{view}  ·  {condition}"
    size = 48 if wide else 38
    while pen.textlength(heading, font=get_font(size, True)) > width - 2 * margin and size > 22:
        size -= 2
    pen.text((margin, round(height * .085)), heading, font=get_font(size, True), fill="#EEF6F4")
    for i, ((x, y, w, h), label) in enumerate(zip(boxes, labels, strict=True)):
        ink = ("#87E0C9", "#F7C48A")[i]
        pen.rectangle((x, y - 55, x + 53, y - 12), fill=ink)
        pen.text((x + 26, y - 34), "AB"[i], font=get_font(28, True), fill="#102025", anchor="mm")
        pen.text((x + 67, y - 34), label, font=get_font(33, True), fill="#F0F5F3", anchor="lm")
        pen.rectangle((x, y, x + w - 1, y + h - 1), fill="#0A1218")
    return image, boxes


def _combine_clips(assets, starts, duration, boxes, plate, output):
    args, filters = [], []
    for i, (asset, start, (_, _, width, height)) in enumerate(zip(assets, starts, boxes, strict=True)):
        width, height = width - width % 2, height - height % 2
        args += ["-ss", str(start), "-protocol_whitelist", "file", "-f", asset["video_format"], "-i", asset["path"]]
        filters.append(f"[{i}:v]fps=24,scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x0a1218,setsar=1[p{i}]")
    for i, (x, y, _, _) in enumerate(boxes):
        base = "2:v" if i == 0 else "first"
        target = "first" if i == 0 else "final"
        filters.append(f"[{base}][p{i}]overlay={x}:{y}:shortest=1[{target}]")
    subprocess.run([get_ffmpeg(), "-nostdin", "-loglevel", "error", "-y", "-filter_complex_threads", "1", *args,
                    "-loop", "1", "-framerate", "24", "-i", str(plate), "-filter_complex", ";".join(filters),
                    "-map", "[final]", "-t", str(duration), "-an", "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", str(output)], check=True, capture_output=True, timeout=95,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def build_comparison(payload, approved, directory, platform):
    ids, labels, condition = (payload.get(key) for key in ("sources", "labels", "condition"))
    if (not isinstance(ids, list) or len(ids) != 2 or not all(isinstance(key, str) for key in ids)
            or ids[0] == ids[1] or not isinstance(labels, list) or len(labels) != 2
            or not all(isinstance(label, str) and 1 <= len(label.strip()) <= 18 for label in labels)
            or not isinstance(condition, str) or not 1 <= len(condition.strip()) <= 32):
        raise ValueError("请选择两份素材，填写两个样本名与共同的取景或测试条件。")
    labels = [" ".join(label.split()) for label in labels]
    condition = " ".join(condition.split())
    if labels[0].casefold() == labels[1].casefold():
        raise ValueError("两边的样本名需要不同。")
    allowed = {asset["page_url"]: asset for asset in approved if not asset.get("comparison")}
    assets = [allowed.get(key) for key in ids]
    if not all(assets) or assets[0]["kind"] != assets[1]["kind"] or assets[0]["kind"] not in {"image", "video"}:
        raise ValueError("请选择两张已审核照片，或两段已审核视频。")
    video = assets[0]["kind"] == "video"
    region = payload.get("region", "")
    if not isinstance(region, str) or (region and (region not in DETAIL_REGIONS or video)):
        raise ValueError("放大区域只适用于两张照片，请选择支持的区域。")
    starts = payload.get("starts", [0, 0]) if video else [0, 0]
    if (not isinstance(starts, list) or len(starts) != 2
            or any(type(value) not in {float, int} or not math.isfinite(value) or value < 0 for value in starts)):
        raise ValueError("素材起点需要是有效的秒数。")
    duration = min(20, *(asset.get("duration", 0) - start for asset, start in zip(assets, starts, strict=True))) if video else 0
    if video and (duration < 1 or any(asset.get("video_format") not in {"mov", "matroska"} for asset in assets)):
        raise ValueError("从两个视频的起点算起，都需要至少一秒画面。")

    directory.mkdir(parents=True)
    plate = directory / "layout.png"
    preview = directory / "comparison.jpg"
    output = directory / "comparison.mp4" if video else preview
    try:
        image, boxes = _plate(platform, condition, labels, region)
        if video:
            from core.reading import capture_frame

            image.save(plate)
            _combine_clips(assets, starts, duration, boxes, plate, output)
            if not capture_frame(output, preview, .1):
                raise ValueError("无法读取合成后的对照视频。")
        else:
            for asset, (x, y, w, h) in zip(assets, boxes, strict=True):
                with Image.open(asset["preview"], formats=("JPEG", "PNG")) as source:
                    photo = ImageOps.exif_transpose(source).convert("RGB")
                    if region:
                        col, row, _ = DETAIL_REGIONS[region]
                        crop_w, crop_h = max(1, photo.width // 3), max(1, photo.height // 3)
                        left, top = (photo.width - crop_w) * col // 2, (photo.height - crop_h) * row // 2
                        photo = photo.crop((left, top, left + crop_w, top + crop_h))
                    photo = ImageOps.contain(photo, (w, h))
                    image.paste(photo, (x + (w-photo.width)//2, y + (h-photo.height)//2))
            image.save(preview, quality=95, subsampling=0)
    except (OSError, ValueError, Image.DecompressionBombError, subprocess.SubprocessError) as error:
        for path in {output, preview, plate}:
            path.unlink(missing_ok=True)
        directory.rmdir()
        raise ValueError("无法合成这组对照素材，请核对原文件和视频起点。") from error
    finally:
        plate.unlink(missing_ok=True)
    sources = [{key: asset[key] for key in ("page_url", "title", "author", "license", "license_url", "local") if key in asset}
               for asset in assets]
    detail = f" · {DETAIL_REGIONS[region][2]}细节" if region else ""
    return {"title": f"同屏对照 · {labels[0]} / {labels[1]}{detail}", "local": True, "page_url": f"local:{directory.name}",
            "kind": "video" if video else "image", "path": str(output), "preview": str(preview), "caption_label": "",
            "comparison": {"labels": labels, "condition": condition, "sources": sources, "starts": starts,
                           **({"region": region} if region else {})},
            **({"duration": duration, "video_format": "mov"} if video else {})}
