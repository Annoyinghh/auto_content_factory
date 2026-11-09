"""
Visual Rendering Engine
生成高质量视频分镜画面、小红书高点击封面、B站16:9横版封面的核心渲染引擎
"""

import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

from config import ASSETS_DIR, FONT_REGULAR, FONT_BOLD, PLATFORM_PRESETS

def get_font(size: int, bold: bool = False):
    font_path = FONT_BOLD if bold and os.path.exists(FONT_BOLD) else FONT_REGULAR
    return ImageFont.truetype(font_path, size)

def create_gradient(width: int, height: int, start_color: tuple, end_color: tuple) -> Image.Image:
    """Generate vertical linear gradient."""
    base = Image.new("RGB", (width, height), start_color)
    top = Image.new("RGB", (width, height), end_color)
    mask = Image.new("L", (width, height))
    mask_data = []
    for y in range(height):
        mask_data.extend([int(255 * (y / height))] * width)
    mask.putdata(mask_data)
    base.paste(top, (0, 0), mask)
    return base

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


def generate_episode_slide(case, index, width, height, platform, *, cover=False, video_overlay=False):
    scene = case["scenes"][index]
    layout = "chapter" if cover else scene["layout"]
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

    if layout == "footage":
        if video_overlay:
            img.paste((0, 0, 0, 0), (0, 0, width, height))
        elif scene["asset"].get("preview"):
            with Image.open(scene["asset"]["preview"]) as photo:
                img.paste(ImageOps.fit(photo, (width, height)))
        # The lower third sits above the baked subtitle safe area.
        panel = bg = "#14181B"
        ink, accent = "#F6F5EF", "#E0EA9A"

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
    label = "热采  /  今日资讯" if cover or opening else f"今日资讯  /  {scene['chapter']:02d} · {count:02d}"
    if layout == "footage":
        draw.rectangle((0, 0, width, top + round(55 * unit)), fill=bg)
    lines(label, margin, top - round(25 * unit), 30, content, 1, accent, True)
    if layout == "chapter" or opening:
        title = case["title"] if cover or opening else scene["chapter_title"]
        y = round(height * (0.31 if wide else 0.33))
        lines("本期看点" if cover or opening else f"第 {scene['chapter']:02d} 章",
              margin, y - round(78 * unit), 38, content, 1, accent, True)
        draw.rectangle((margin, y - round(14 * unit), margin + round(width * 0.12), y - round(8 * unit)), fill=accent)
        end = lines(title, margin, y, 88 if wide else 74, content, 3, ink, True)
        direction = (case.get("editorial_plan") or {}).get("throughline")
        subtitle = direction if opening and direction else (f"{count} 条热点  /  多来源报道" if cover or opening
                                                             else f"来源  /  {scene['chapter_source']}")
        lines(subtitle, margin, min(end + round(42 * unit), round(height * 0.72)), 28, content, 2 if opening else 1, accent)
    elif layout == "closing":
        y = round(height * .31)
        lines("回看本期", margin, y - round(62 * unit), 34, content, 1, accent, True)
        end = lines(scene["title"], margin, y, 72 if wide else 66, content, 2, ink, True)
        lines(scene["text"], margin, end + round(32 * unit), 39, content, 3, ink)
        lines(f"共 {count} 章  /  章节内附报道来源", margin, round(height * .73), 27, content, 1, accent)
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
    elif layout == "footage":
        asset = scene["asset"]
        bar = round(height * (0.67 if wide else 0.59))
        draw.rectangle((0, bar, width, height), fill=panel)
        lines(scene["title"], margin, bar + round(25 * unit), 48, content, 1 if wide else 2, ink, True)
        credit = "本地导入素材" if asset.get("local") else f"示意画面  /  {asset['author']} · {asset['license']}"
        lines(credit, margin, bar + round((99 if wide else 165) * unit), 25, content, 1, accent)
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

def generate_scene_slide(case: dict, scene_idx: int, width: int, height: int) -> Image.Image:
    """
    Renders an individual scene slide frame with modern aesthetic typography,
    category badges, metric tags, and central card layout.
    """
    scene = case["scenes"][scene_idx]
    is_horiz = width > height

    # Color palette
    bg_top = (14, 18, 28)
    bg_bottom = (24, 30, 44)
    img = create_gradient(width, height, bg_top, bg_bottom)
    draw = ImageDraw.Draw(img)

    # 1. Top Series Badge
    badge_text = f"💡 全球奇趣商业怪诞志 · 第 {case.get('id', '01')} 期"
    badge_font = get_font(28 if not is_horiz else 24, bold=True)
    badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    bw = badge_bbox[2] - badge_bbox[0] + 40
    bh = badge_bbox[3] - badge_bbox[1] + 20
    bx = (width - bw) // 2
    by = 100 if not is_horiz else 50
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=15, fill=(35, 45, 65), outline=(70, 90, 130), width=2)
    draw.text((bx + 20, by + 8), badge_text, font=badge_font, fill=(0, 242, 254))

    # 2. Hero Topic Title Banner
    title_text = case["title"]
    title_font = get_font(52 if not is_horiz else 46, bold=True)
    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    tw = title_bbox[2] - title_bbox[0]
    tx = (width - tw) // 2
    ty = by + bh + (40 if not is_horiz else 25)
    # Accent glow behind title
    draw.text((tx, ty), title_text, font=title_font, fill=(255, 230, 0))

    # 3. Key stats pill
    stats_text = f"🔥 营收: {case['revenue']}  |  利润率: {case['profit_margin']}"
    stats_font = get_font(28 if not is_horiz else 24)
    stats_bbox = draw.textbbox((0, 0), stats_text, font=stats_font)
    sw = stats_bbox[2] - stats_bbox[0]
    sx = (width - sw) // 2
    sy = ty + (70 if not is_horiz else 60)
    draw.text((sx, sy), stats_text, font=stats_font, fill=(180, 200, 220))

    # 4. Central Information Card
    card_w = int(width * (0.88 if not is_horiz else 0.72))
    card_h = int(height * (0.42 if not is_horiz else 0.52))
    card_x = (width - card_w) // 2
    card_y = sy + (60 if not is_horiz else 40)

    # Card background with border
    draw.rounded_rectangle([card_x, card_y, card_x + card_w, card_y + card_h],
                           radius=24, fill=(20, 26, 40), outline=(55, 75, 110), width=3)

    # Card Step Tag: e.g. "01 / 离谱的起源"
    stage_text = f"STEP {scene_idx + 1:02d} · {scene['title']}"
    stage_font = get_font(30 if not is_horiz else 26, bold=True)
    draw.text((card_x + 50, card_y + 45), stage_text, font=stage_font, fill=(0, 230, 118))

    # Card Focus Headline: scene["card_title"]
    headline_text = scene.get("card_title", scene["title"])
    headline_font = get_font(44 if not is_horiz else 38, bold=True)
    draw.text((card_x + 50, card_y + 105), headline_text, font=headline_font, fill=(255, 255, 255))

    # Divider line
    div_y = card_y + (180 if not is_horiz else 160)
    draw.line([(card_x + 50, div_y), (card_x + card_w - 50, div_y)], fill=(50, 65, 95), width=2)

    # Card Description / Bullets
    desc_text = scene.get("card_desc", scene["text"][:60] + "...")
    desc_font = get_font(32 if not is_horiz else 28)
    wrapped_lines = wrap_text(desc_text, desc_font, card_w - 100, draw)

    line_y = div_y + 35
    for line in wrapped_lines:
        draw.text((card_x + 50, line_y), f"• {line}", font=desc_font, fill=(220, 230, 245))
        line_y += (48 if not is_horiz else 42)

    # 5. Visual Accent Tag in bottom-right of card
    cat_tag = f"#{case.get('category', '商业奇案')}"
    tag_font = get_font(26 if not is_horiz else 22)
    t_bbox = draw.textbbox((0, 0), cat_tag, font=tag_font)
    t_w = t_bbox[2] - t_bbox[0]
    draw.text((card_x + card_w - t_w - 40, card_y + card_h - 50), cat_tag, font=tag_font, fill=(120, 145, 185))

    return img

def generate_xiaohongshu_cover(case: dict, output_path: str, width: int = 1080, height: int = 1440):
    """
    Renders high-CTR Xiaohongshu cover (3:4 ratio) with bold contrasting color blocks,
    curiosity gap headline, and visual sticker badges.
    """
    img = create_gradient(width, height, (18, 20, 30), (30, 35, 52))
    draw = ImageDraw.Draw(img)

    meta = case["platform_metadata"]["xiaohongshu"]

    # Top sticker pill
    badge = meta.get("cover_badge", "海外奇葩搞钱 #01")
    b_font = get_font(34, bold=True)
    draw.rounded_rectangle([80, 100, 480, 170], radius=18, fill=(255, 36, 66))
    draw.text((110, 115), f"🔥 {badge}", font=b_font, fill=(255, 255, 255))

    # Main Headline Banner (Big, Yellow / White)
    main_title = meta.get("cover_main", case["title"])
    m_font = get_font(72, bold=True)
    lines = wrap_text(main_title, m_font, width - 160, draw)
    ty = 240
    for line in lines:
        # Yellow background highlight box for key text
        bbox = draw.textbbox((0, 0), line, font=m_font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        draw.rounded_rectangle([75, ty - 10, 85 + lw + 20, ty + lh + 15], radius=14, fill=(255, 230, 0))
        draw.text((85, ty), line, font=m_font, fill=(15, 15, 20))
        ty += lh + 45

    # Subtitle Pill
    sub_title = meta.get("cover_sub", f"{case['revenue']} · {case['profit_margin']}暴利")
    s_font = get_font(38, bold=True)
    draw.text((85, ty + 20), sub_title, font=s_font, fill=(0, 242, 254))

    # Middle Feature Box
    box_y = ty + 100
    box_h = 420
    draw.rounded_rectangle([70, box_y, width - 70, box_y + box_h], radius=24, fill=(25, 32, 48), outline=(65, 85, 125), width=3)

    # Inside feature bullets
    f_font = get_font(34, bold=False)
    bullets = [
        f"🎯 创始人: {case['founder']} ({case['year']})",
        f"💰 真实营收: {case['revenue']}",
        f"📦 成本极低: {case['cost']}",
        f"💡 底层商业模型: {case['category']}"
    ]
    by = box_y + 45
    for b in bullets:
        draw.text((110, by), b, font=f_font, fill=(230, 240, 255))
        by += 80

    # Bottom Callout Sticker
    bot_text = "👇 点击查看完整商业闭环与搞钱逻辑"
    bot_font = get_font(34, bold=True)
    draw.rounded_rectangle([70, height - 160, width - 70, height - 80], radius=20, fill=(0, 230, 118))
    draw.text((160, height - 140), bot_text, font=bot_font, fill=(10, 20, 15))

    img.save(output_path, quality=95)
    return output_path

def generate_bilibili_cover(case: dict, output_path: str, width: int = 1920, height: int = 1080):
    """
    Renders high-CTR Bilibili 16:9 widescreen thumbnail.
    """
    img = create_gradient(width, height, (12, 16, 26), (25, 32, 48))
    draw = ImageDraw.Draw(img)

    # Left Side: Giant Hook Text
    badge_font = get_font(36, bold=True)
    draw.rounded_rectangle([100, 100, 480, 170], radius=18, fill=(0, 161, 214)) # Bilibili cyan
    draw.text((130, 115), "【商业奇案观察】", font=badge_font, fill=(255, 255, 255))

    title_font = get_font(76, bold=True)
    title = case["title"]
    lines = wrap_text(title, title_font, 1000, draw)
    ty = 230
    for line in lines:
        draw.text((100, ty), line, font=title_font, fill=(255, 230, 0))
        ty += 110

    sub_font = get_font(42)
    draw.text((100, ty + 20), f"⚡ 创始人: {case['founder']} | 利润率高达 {case['profit_margin']}", font=sub_font, fill=(0, 242, 254))

    # Right Side: Giant Stats Card
    rx = 1200
    ry = 180
    rw = 620
    rh = 720
    draw.rounded_rectangle([rx, ry, rx + rw, ry + rh], radius=32, fill=(20, 26, 40), outline=(60, 80, 120), width=4)

    draw.text((rx + 60, ry + 60), "REVENUE 战报", font=get_font(32, bold=True), fill=(0, 230, 118))
    draw.text((rx + 60, ry + 120), case["revenue"], font=get_font(52, bold=True), fill=(255, 255, 255))

    draw.line([(rx + 60, ry + 220), (rx + rw - 60, ry + 220)], fill=(50, 65, 95), width=2)

    draw.text((rx + 60, ry + 260), "• 商业类别:", font=get_font(34, bold=True), fill=(180, 200, 220))
    draw.text((rx + 60, ry + 310), f"  {case['category']}", font=get_font(34), fill=(255, 230, 0))

    draw.text((rx + 60, ry + 390), "• 成本分析:", font=get_font(34, bold=True), fill=(180, 200, 220))
    draw.text((rx + 60, ry + 440), f"  {case['cost']}", font=get_font(32), fill=(255, 255, 255))

    draw.text((rx + 60, ry + 520), "• 核心武器:", font=get_font(34, bold=True), fill=(180, 200, 220))
    draw.text((rx + 60, ry + 570), f"  {case['scenes'][2]['card_title']}", font=get_font(32), fill=(0, 242, 254))

    img.save(output_path, quality=95)
    return output_path
