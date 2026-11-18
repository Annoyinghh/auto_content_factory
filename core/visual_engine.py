"""
Visual Rendering Engine
生成高质量视频分镜画面、小红书高点击封面、B站16:9横版封面的核心渲染引擎
"""

import os
import re
from PIL import Image, ImageDraw, ImageFont, ImageOps

from config import ASSETS_DIR, FONT_REGULAR, FONT_BOLD

def get_font(size: int, bold: bool = False):
    font_path = FONT_BOLD if bold and os.path.exists(FONT_BOLD) else FONT_REGULAR
    return ImageFont.truetype(font_path, size)

def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list:
    """Wrap Chinese & English text into lines based on pixel width."""
    lines = []
    cur_line = ""
    for char in text:
        test_line = cur_line + char
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w > max_width:
            if cur_line:
                lines.append(cur_line)
                cur_line = char
            else:
                lines.append(char)
                cur_line = ""
        else:
            cur_line = test_line
    if cur_line:
        lines.append(cur_line)
    return lines


NEWS_PALETTES = {
    "xiaohongshu": ("#F8EFE4", "#2B2725", "#923E31", "#EDDCC9"),
    "douyin": ("#151617", "#F7F7F1", "#FAD745", "#282A2D"),
    "bilibili": ("#10222C", "#F0F4F4", "#79D8D0", "#213943"),
}


def news_media_box(width, height, *, cover=False):
    if width > height:
        return (int(width * 0.48), int(height * 0.18), int(width * 0.94), int(height * 0.74))
    return (int(width * 0.07), int(height * 0.15), int(width * 0.93), int(height * (0.55 if cover else 0.49)))


def opening_media_box(width, height):
    wide = width > height
    ratios = (.055, .17, .653, .773) if wide else (.065, .143, .935, .539)
    return tuple(round(size * ratio / 2) * 2 for size, ratio in zip((width, height, width, height), ratios))


def generate_opening_slide(case, scene, width, height, platform, *, video_overlay):
    wide = width > height
    unit = height / (1080 if wide else 1920)
    x0, y0, x1, y1 = opening_media_box(width, height)
    right = width - x0
    ink, muted = "#F5F6F2", "#ACBFCA"
    accent = {"bilibili": "#78DFCF", "douyin": "#F6D863", "xiaohongshu": "#F0AB9E"}[platform]
    img = Image.new("RGBA" if video_overlay else "RGB", (width, height), "#101923")
    asset, report = scene.get("asset"), scene.get("opening_report", {})
    if video_overlay:
        img.paste((0, 0, 0, 0), (x0, y0, x1, y1))
    elif asset and asset.get("preview"):
        with Image.open(asset["preview"]) as photo:
            img.paste(ImageOps.fit(photo, (x1-x0, y1-y0)), (x0, y0))
    draw = ImageDraw.Draw(img)

    def text_block(text, x, y, size, max_width, count, color=ink, bold=False):
        font = get_font(max(11, round(size * unit)), bold)
        wrapped = wrap_text(text, font, max_width, draw)
        for i, line in enumerate(wrapped[:count]):
            draw.text((x, y), line if i < count-1 or len(wrapped) <= count else line[:-1]+"…", font=font, fill=color)
            y += round(size * unit * 1.30)
        return y

    width_label = right - x0
    text_block("热采资讯  /  本期观察", x0, round(height * .040), 25 if wide else 29, width_label, 1, accent, True)
    text_block(case["title"], x0, round(height * .079), 43 if wide else 45, width_label, 1, ink, True)
    draw.line((x0, y0-12, right, y0-12), fill="#355261", width=max(1, round(2*unit)))

    if not asset:
        draw.rectangle((x0, y0, x1-1, y1-1), fill="#1B2B38")
        inset = round((x1-x0) * .075)
        block_width = x1-x0-2*inset
        y = y0 + round((y1-y0) * .19)
        text_block("报道速览" if report else "本期线索", x0+inset, y, 28 if wide else 32, block_width, 1, accent, True)
        draw.rectangle((x0+inset, y+round(48*unit), x0+inset+round(92*unit), y+round(53*unit)), fill=accent)
        text_block(report.get("title", scene["title"]), x0+inset, y+round(80*unit), 61 if wide else 55,
                   block_width, 3, ink, True)
        if report:
            text_block(report["source"], x0+inset, y1-round(90*unit), 29 if wide else 31, block_width, 1, muted)

    draw.rectangle((x0-2, y0-2, x1+1, y1+1), outline="#52717D", width=2)
    sx, sy = (round(width * .683), y0) if wide else (x0, round(height * .568))
    end = y1 if wide else round(height * .749)
    draw.rectangle((sx, sy, right, end), fill="#172633")
    pad = round(width * (.019 if wide else .035))
    content = right-sx-2*pad
    label = {"image": "资料照片", "video": "资料视频"}.get(asset.get("kind") if asset else None, "报道卡片")
    if asset and not asset.get("local"):
        label += "  /  示意画面"
    text_block(label, sx+pad, sy+round(23*unit), 24 if wide else 27, content, 1, accent, True)
    title_y = sy+round((78 if wide else 73)*unit)
    text_block(scene["title"], sx+pad, title_y, 47 if wide else 47, content, 3 if wide else 2, ink, True)
    attribution = (asset["title"] if asset.get("local") else f"{asset['author']} · {asset['license']}") if asset else ""
    if report:
        text_block(f"报道  /  {report['source']}", sx+pad, end-round(98*unit), 25, content, 1, muted)
    if attribution:
        text_block(attribution, sx+pad, end-round(54*unit), 23, content, 1, accent)

    for beat in range(5):
        start = x0 + beat*round(49*unit)
        y = y1 + round(26*unit) if wide else round(height * .772)
        draw.rectangle((start, y, start+round(37*unit), y+round(5*unit)),
                       fill=accent if beat <= scene["opening_beat"] else "#3A5360")
    return img


def _roundup_heading(image, chapter, platform, *, on_footage):
    width, height = image.size
    wide = width > height
    draw = ImageDraw.Draw(image)
    x = round(width * .065)
    y = round(height * (.19 if on_footage else .31))
    label = f"资讯 {chapter['number']:02d} / {chapter['total']:02d}"
    accent = "#A4E7DC" if platform == "bilibili" else "#FFD48B"
    unit = height / (1080 if wide else 1920)
    small = get_font(round(28 * unit), True)
    title = get_font(round((60 if wide else 65) * unit), True)
    caption = get_font(round(27 * unit))
    edge = round(2 * unit) if on_footage else 0
    draw.line((x, y, x+round(48*unit), y), fill=accent, width=max(2, round(4*unit)))
    draw.text((x, y+round(14*unit)), label, fill=accent, font=small, stroke_width=edge, stroke_fill="#102027")
    text_y = y + round(64*unit)
    for line in wrap_text(chapter["title"], title, width-2*x, draw)[:2]:
        draw.text((x, text_y), line, fill="#F5F8F6", font=title, stroke_width=edge, stroke_fill="#102027")
        text_y += round(79*unit)
    stamp = chapter.get("published", "")
    details = f"报道 · {chapter['source']}" + (f"  /  发布 {stamp}" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp) else "")
    for line in wrap_text(details, caption, width-2*x, draw)[:2]:
        draw.text((x, text_y+round(11*unit)), line, fill="#CFDEDD", font=caption, stroke_width=edge, stroke_fill="#102027")
        text_y += round(37*unit)
    if not on_footage:
        draw.line((x, round(height*.70), width-x, round(height*.70)), fill="#31444D", width=2)
    return image


def _documentary_plate(scene, width, height, platform, *, video_overlay):
    """A full-frame image with typography only where the story needs it."""
    asset = scene.get("asset", {})
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0) if video_overlay else "#12191F")
    if not video_overlay and asset.get("preview"):
        with Image.open(asset["preview"]) as picture:
            image.paste(ImageOps.fit(picture.convert("RGBA"), (width, height)))
    wide = width > height
    scale = height / (1080 if wide else 1920)
    x, y = round(width*.065), round(height*.095)
    label = ("动画示意" if not asset else asset.get("caption_label") or "本地素材" if asset.get("local") else
             f"{asset.get('caption_label', '资料画面')}  ·  {asset['author']}")
    heading = scene.get("documentary_title")
    number = scene.get("documentary_number")
    dark = Image.new("RGBA", image.size, "#0A1117")
    gradient = Image.new("L", (1, height))
    gradient.putdata([round(max(0, 1-row/(height*.24))*118 + max(0, (row-height*.78)/(height*.22))*142)
                      for row in range(height)])
    dark.putalpha(gradient.resize(image.size))
    image.alpha_composite(dark)
    draw = ImageDraw.Draw(image)
    accent = {"xiaohongshu": "#FFD5C8", "douyin": "#FFD48B", "bilibili": "#F5D190"}[platform]
    ink = "#FFF8ED"
    credit_font = get_font(max(15, round(23*scale)))
    if asset.get("caption_label") != "":
        credit = wrap_text(label, credit_font, width-2*x, draw)[0]
        draw.text((x, y), credit, font=credit_font, fill=ink, stroke_width=2, stroke_fill="#172027")
    if scene.get("chapter_handoff"):
        return _roundup_heading(image, scene["chapter_handoff"], platform, on_footage=True)
    if heading:
        title_font = get_font(round((68 if wide else 69)*scale), True)
        for row, line in enumerate(wrap_text(heading, title_font, round(width*.72), draw)[:2]):
            draw.text((x, round(height*.30)+row*round(88*scale)), line, font=title_font, fill=ink,
                      stroke_width=max(2, round(3*scale)), stroke_fill="#192027")
    elif number:
        font = get_font(round((112 if wide else 124)*scale), True)
        draw.text((width-x, round(height*.55)), number, anchor="ra", font=font, fill=accent,
                  stroke_width=max(2, round(3*scale)), stroke_fill="#172027")
    return image


def generate_episode_slide(case, index, width, height, platform, *, cover=False, video_overlay=False):
    scene = case["scenes"][index]
    layout = "chapter" if cover else scene["layout"]
    if layout == "roundup_intro":
        return _roundup_heading(Image.new("RGB", (width, height), "#121E25"),
                                scene["chapter_handoff"], platform, on_footage=False)
    if layout in {"footage", "graphic"} or (layout == "opening" and scene.get("asset")):
        return _documentary_plate(scene, width, height, platform, video_overlay=video_overlay)
    if layout == "opening":
        return generate_opening_slide(case, scene, width, height, platform, video_overlay=video_overlay)
    bg, ink, accent, panel = NEWS_PALETTES[platform]
    wide = width > height
    unit = height / (1080 if wide else 1920)
    margin, top = round(width * 0.075), round(height * 0.085)
    content = width - 2 * margin
    img = Image.new("RGBA" if video_overlay else "RGB", (width, height), bg)
    opening = case.get("resume") and index == 0
    if opening:
        with Image.open(ASSETS_DIR / "screening" / "studio.png") as studio:
            backdrop = ImageOps.fit(studio.convert("RGB"), (width, height), centering=(.72, .48))
        img = Image.blend(Image.new("RGB", (width, height), "#141314"), backdrop, .47)
        ink, accent = "#F5E9DE", "#EFB275"

    draw = ImageDraw.Draw(img)

    def lines(text, x, y, size, max_width, max_lines, color, bold=False):
        font = get_font(max(11, round(size * unit)), bold)
        wrapped = wrap_text(text, font, max_width, draw)
        for i, line in enumerate(wrapped[:max_lines]):
            if i == max_lines - 1 and len(wrapped) > max_lines:
                line = line[:-1] + "…"
            draw.text((x, y), line, font=font, fill=color)
            y += round(size * unit * 1.38)
        return y

    count = len(case["chapter_starts"])
    news = case.get('news') or {}
    series = news.get('series','今日资讯')
    label = f"热采  /  {series}" if cover or opening else f"{series}  /  {scene['chapter']:02d} · {count:02d}"
    lines(label, margin, top - round(25 * unit), 30, content, 1, accent, True)
    if layout == "chapter" or opening:
        title = case["title"] if cover or opening else scene["chapter_title"]
        y = round(height * (0.31 if wide else 0.33))
        lines("本期看点" if cover or opening else f"第 {scene['chapter']:02d} 章",
              margin, y - round(78 * unit), 38, content, 1, accent, True)
        draw.rectangle((margin, y - round(14 * unit), margin + round(width * 0.12), y - round(8 * unit)), fill=accent)
        end = lines(title, margin, y, 88 if wide else 74, content, 3, ink, True)
        direction = (case.get("editorial_plan") or {}).get("throughline")
        subtitle = direction if opening and direction else ((f"{count} 章  /  所选原文" if news.get('is_reference') else
                                                            f"{count} 条热点  /  多来源报道") if cover or opening
                                                             else f"来源  /  {scene['chapter_source']}")
        lines(subtitle, margin, min(end + round(42 * unit), round(height * 0.72)), 28, content, 2 if opening else 1, accent)
    elif layout == "closing":
        y = round(height * .31)
        lines("回看本期", margin, y - round(62 * unit), 34, content, 1, accent, True)
        end = lines(scene["title"], margin, y, 72 if wide else 66, content, 2, ink, True)
        lines(scene["text"], margin, end + round(32 * unit), 39, content, 3, ink)
        lines(f"共 {count} 章  /  章节内附原文出处", margin, round(height * .73), 27, content, 1, accent)
    elif layout == "metric":
        y = round(height * .23)
        draw.rectangle((margin, y, width - margin, round(height * .69)), fill=panel)
        inset = round(width * .04)
        lines("报道数据", margin + inset, y + round(23 * unit), 26, content - 2*inset, 1, ink)
        lines(scene["metric"], margin + inset, y + round(77 * unit), 110 if wide else 96,
              content - 2*inset, 1, accent, True)
        lines(scene["title"], margin + inset, y + round(234 * unit), 48 if wide else 47,
              content - 2*inset, 2, ink, True)
        lines(f"来源  /  {scene['chapter_source']}", margin, round(height * .73), 25, content, 1, accent)
    else:
        y = round(height * (0.26 if wide else 0.25))
        if layout == "detail":
            draw.rectangle((0, round(height * 0.19), width, round(height * 0.72)), fill=panel)
            lines("报道细节", margin, y, 32, content, 1, accent, True)
            y += round(68 * unit)
        else:
            draw.line((margin, y, width - margin, y), fill=accent, width=max(2, round(3 * unit)))
            y += round(54 * unit)
        end = lines(scene["title"], margin, y, 68 if wide else 62, content, 2, ink, True)
        lines(scene["card_desc"], margin, end + round(28 * unit), 44 if wide else 42, content, 3, ink)
        lines(f"来源  /  {scene['chapter_source']}", margin, round(height * 0.73), 25, content, 1, accent)
    return img


def generate_news_slide(case, index, width, height, platform, *, cover=False, video_overlay=False):
    if case.get('news_frame') == 'source-news-v1':
        from core.news_frame import frame_slide
        return frame_slide(case, index, width, height, platform, video_overlay=video_overlay)
    scene, news = case["scenes"][index], case["news"]
    if scene.get("layout"):
        return generate_episode_slide(case, index, width, height, platform, cover=cover, video_overlay=video_overlay)
    bg, ink, accent, panel = NEWS_PALETTES[platform]
    wide = width > height
    img = Image.new("RGBA" if video_overlay else "RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    scale = height / (1080 if wide else 1920)
    margin = int(width * 0.07)

    def font(size, bold=False):
        return get_font(max(12, round(size * scale)), bold)

    def text_block(text, x, y, size, color, max_width, lines, bold=False):
        face = font(size, bold)
        wrapped = wrap_text(text, face, max_width, draw)
        for line_index, line in enumerate(wrapped[:lines]):
            if line_index == lines - 1 and len(wrapped) > lines:
                line = line[:-1] + "…"
            draw.text((x, y), line, font=face, fill=color)
            y += round(size * scale * 1.42)
        return y

    header_y = int(height * 0.063)
    text_block(f"{news['series']}  /  {index + 1:02d}", margin, header_y, 37, accent, width - 2 * margin, 1, True)
    rule_y = int(height * 0.12)
    draw.line((margin, rule_y, width - margin, rule_y), fill=accent, width=max(2, round(scale * 3)))
    x0, y0, x1, y1 = news_media_box(width, height, cover=cover)
    asset = scene.get("asset")
    if video_overlay:
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(0, 0, 0, 0))
    elif asset and asset.get("preview"):
        with Image.open(asset["preview"]) as original:
            img.paste(ImageOps.fit(original, (x1 - x0, y1 - y0)), (x0, y0))
    else:
        draw.rectangle((x0, y0, x1, y1), fill=panel)
        text_block(case["category"], x0 + margin // 2, y0 + (y1 - y0) // 3, 138, accent, x1 - x0 - margin, 1, True)

    media_label = "报道文字 · 编辑摘要" if news.get("source_url") else "作者原稿"
    if asset and (video_overlay or asset.get("preview")):
        media_label = "本地素材" if asset.get("local") else f"示意素材 · {asset['author']} · {asset['license']}"
    text_block(media_label, x0, y1 + round(13 * scale), 24, ink, x1 - x0, 1)
    body_y = int(height * 0.24) if wide else y1 + round(69 * scale)
    body_w = x0 - margin - 38 if wide else width - 2 * margin
    heading = case["title"] if cover or index == 0 else scene["title"]
    body_y = text_block(heading, margin, body_y, 65 if wide else 73, ink, body_w, 3 if wide else 2, True)
    body_y += round(24 * scale)
    if cover:
        detail = f"{case['category']} · {news['source']}"
    else:
        detail = f"来源 · {news['source']}" if index == 0 else scene["card_desc"]
    text_block(detail, margin, body_y, 34 if wide else 39, accent if cover else ink, body_w, 3)
    footer_y = int(height * (0.82 if wide else 0.76))
    text_block("AI 合成配音", margin, footer_y, 23, accent, width - 2 * margin, 1)
    return img
