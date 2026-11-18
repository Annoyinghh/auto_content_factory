"""Small looping vector animations for editorial diagrams."""

import hashlib
import math
import re
import subprocess

from PIL import Image, ImageDraw, ImageFont

from config import FONT_BOLD, FONT_REGULAR, get_ffmpeg
from core.video_styles import depth_metres, drilling_flow_cues


def motion_loop(directory, style):
    path = directory / f"diagram-{style}.mp4"
    if path.is_file():
        return path.name
    width, height, fps, seconds = 480, 270, 24, 4
    base = Image.new("RGB", (width, height), "#0C1821")
    draw = ImageDraw.Draw(base)
    for x in range(0, width, 24):
        draw.line((x, 0, x, height), fill="#182C35")
    for y in range(0, height, 24):
        draw.line((0, y, width, y), fill="#182C35")
    frames = bytearray()
    for frame in range(seconds * fps):
        t = frame / (seconds * fps)
        image = base.copy()
        pen = ImageDraw.Draw(image)
        if style == "path":
            points = [(32, 194), (127, 151), (230, 174), (352, 89), (448, 112)]
            pen.line(points, fill="#396674", width=2)
            progress = t * (len(points) - 1)
            index = int(progress)
            blend = progress - index
            x, y = (round(a + (b - a) * blend) for a, b in zip(points[index], points[index+1]))
            pen.line([*points[:index+1], (x, y)], fill="#75D9C9", width=4)
            for number, (px, py) in enumerate(points, 1):
                pen.ellipse((px-6, py-6, px+6, py+6), fill="#1F3540", outline="#83DACE", width=2)
                pen.text((px+10, py+7), f"{number:02d}", fill="#9AAFB2")
            pen.ellipse((x-7, y-7, x+7, y+7), fill="#F3C786")
        else:
            x, y, radius = 240, 132, 114
            angle = 360 * t - 90
            for ring in (28, 56, 84, 114):
                pen.ellipse((x-ring, y-ring, x+ring, y+ring), outline="#396674", width=2)
            for offset in range(42, 0, -3):
                pen.pieslice((x-radius, y-radius, x+radius, y+radius), angle-offset, angle-offset+3,
                             fill=(15, 40 + 2*(42-offset), 49 + 2*(42-offset)))
            rad = math.radians(angle)
            pen.line((x, y, x+radius*math.cos(rad), y+radius*math.sin(rad)), fill="#9EE3D3", width=2)
            for px, py, phase in ((215, 170, .2), (299, 104, .4), (180, 99, .6)):
                size = round(4 + 8 * ((t + phase) % 1))
                pen.ellipse((px-size, py-size, px+size, py+size), outline="#B18858")
                pen.ellipse((px-2, py-2, px+2, py+2), fill="#F3C786")
        frames.extend(image.tobytes())
    _encode_loop(path, width, height, fps, frames)
    return path.name


def _depth_frames(labels, cues, duration, fps, width, height, accent, extent):
    values = [depth_metres(label) for label in labels]
    extent = max(max(values), extent or 0)
    target = extent / 4
    order = 10 ** math.floor(math.log10(target))
    step = next(n * order for n in (1, 2, 2.5, 3, 5, 10) if n * order >= target)
    ticks = math.ceil(extent / step)
    limit = ticks * step
    top, bottom = round(height * .29), round(height * .76)
    column = round(width * .40)

    def ordinate(value):
        return round(top + value / limit * (bottom - top))

    base = Image.new("RGB", (width, height), "#121C24")
    pen = ImageDraw.Draw(base)
    small = ImageFont.truetype(FONT_REGULAR, 15)
    marker_font = ImageFont.truetype(FONT_BOLD, 20)
    left, right = round(width * .08), round(width * .91)
    pen.text((left, top - 37), "深度标尺", font=marker_font, fill="#F2F4F5")
    pen.text((right, top - 34), "纵向比例  /  米", font=small, fill="#A7BAC1", anchor="ra")
    pen.rectangle((column - 9, top, column + 9, bottom), fill="#22353E")
    for index in range(ticks + 1):
        y = ordinate(index * step)
        pen.line((left + 47, y, right, y), fill="#36505A" if index else "#799098", width=1)
        tick = f"{index * step:,.3f}".rstrip("0").rstrip(".")
        pen.text((left + 40, y), tick,
                 fill="#A7BAC1", font=small, anchor="rm")

    ordered = sorted(range(len(labels)), key=lambda i: values[i])
    positions = {}
    label_y = top - 26
    for i in ordered:
        label_y = max(label_y + 26, ordinate(values[i]))
        positions[i] = label_y

    for frame in range(math.ceil(duration * fps)):
        t = frame / fps
        progress = [min(1., max(0., (t - cue) / min(.8, duration - cue - 1 / fps))) for cue in cues]
        depth = max(value * (p * p * (3 - 2 * p)) for value, p in zip(values, progress, strict=True))
        canvas = base.copy()
        draw = ImageDraw.Draw(canvas)
        if depth:
            point = ordinate(depth)
            draw.line((column, top, column, point), fill=accent, width=4)
            draw.polygon([(column - 5, point - 4), (column + 5, point - 4), (column, point + 6)], fill=accent)
        for index, (value, label, level) in enumerate(zip(values, labels, progress, strict=True)):
            if level < .85:
                continue
            y, yy = ordinate(value), positions[index]
            draw.line([(column, y), (column + 24, yy), (column + 40, yy)], fill=accent, width=2)
            draw.ellipse((column - 4, y - 4, column + 4, y + 4), fill=accent)
            draw.text((column + 49, yy), label, font=marker_font, fill=accent, anchor="lm")
        yield canvas.tobytes()


def _flow_particles(draw, points, clock, color, *, count=5):
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    draw.line(points, fill=color, width=2, joint="curve")
    for index in range(count):
        position = (clock * .63 + index / count) % 1 * total
        for (x, y), (x1, y1), length in zip(points, points[1:], lengths):
            if position <= length:
                fraction = position / length
                px, py = x + (x1-x)*fraction, y + (y1-y)*fraction
                draw.ellipse((px-3, py-3, px+3, py+3), fill=color)
                break
            position -= length


def _drilling_frames(cues, duration, fps, width, height, narration):
    wide = width > height
    small, large = (ImageFont.truetype(font, size) for font, size in ((FONT_REGULAR, 15), (FONT_BOLD, 22)))
    axis = round(width * (.31 if wide else .39))
    left, right = axis - 30, axis + 30
    top, bottom = round(height * (.26 if wide else .30)), round(height * .75)
    joint = round(top + (bottom - top) * .49)
    margin = round(width * .07)
    edge = round(width * (.56 if wide else .91))
    down = [(axis, top + 12), (axis, bottom - 22)]
    up = [(axis, bottom - 20), (axis + 17, bottom - 11), (axis + 20, bottom - 21), (axis + 20, top + 12)]
    branch = [(axis + 20, joint), (right + 17, joint + 5), (right + 40, joint - 5), (edge - 7, joint + 6)]
    captions = {
        "circulate": ("钻井液怎样循环", "沿钻杆内向下", "经过钻头，从外侧环空返出"),
        "loss": ("井漏时，流体去了哪", "一部分流体进入地层", "返出量可能减少"),
        "plug": ("堵漏在做什么", "将堵漏材料送至漏失部位", "尝试封堵流体外逸通道"),
    }
    base = Image.new("RGB", (width, height), "#0D2027")
    pen = ImageDraw.Draw(base)
    pen.rectangle((margin, top, edge, bottom), fill="#45392F", outline="#AB8765", width=1)
    for i in range(1, 7):
        y = round(top + i*(bottom-top)/7)
        pen.line((margin, y, edge, y-4), fill="#725B47", width=2)
    pen.rectangle((left, top, right, bottom), fill="#142B37", outline="#C3AA8B", width=2)
    pen.rectangle((axis-8, top-6, axis+8, bottom-24), fill="#1E4952", outline="#7AACA9", width=2)
    pen.polygon([(axis-10, bottom-25),(axis+10,bottom-25),(axis+5,bottom-15),(axis-5,bottom-15)], fill="#E3BE8B")
    pen.line((margin, top, edge, top), fill="#D5B991", width=2)
    pen.text((margin+5, bottom-9), "井壁剖面", font=small, fill="#E5CAAB", anchor="ls")

    times = [(min(max(0, duration-1.1), duration*index/len(narration)), phase) for index, phase, _ in cues]
    for frame in range(math.ceil(duration * fps)):
        elapsed = frame / fps
        phase = next((key for at, key in reversed(times) if elapsed >= at), "circulate")
        canvas = base.copy()
        draw = ImageDraw.Draw(canvas)
        title, detail, extra = captions[phase]
        tx, ty = (round(width*.61), round(height*.33)) if wide else (margin, round(height*.16))
        draw.text((tx, ty), title, font=large, fill="#F2E9D9")
        draw.text((tx, ty+33), detail, font=small, fill="#ADE1D7")
        draw.text((tx, ty+55), extra, font=small, fill="#BDD1CE")
        _flow_particles(draw, down, elapsed, "#77E2CC" if phase != "plug" else "#F3C389")
        if phase == "circulate":
            _flow_particles(draw, up, elapsed, "#9ADBEE")
        else:
            _flow_particles(draw, up, elapsed, "#709494", count=2)
            draw.line(branch, fill="#171C20", width=8, joint="curve")
            _flow_particles(draw, branch, elapsed, "#ED9C68" if phase == "loss" else "#F3C389", count=3)
            if phase == "plug":
                x, y = branch[2]
                draw.ellipse((x-8,y-5,x+8,y+5), fill="#F3C389", outline="#8C5738")
        yield canvas.tobytes()


def label_diagram(directory, style, labels, platform, *, narration="", duration=3, depth_extent=None, viewport=None):
    from core.news_diagrams import KINDS, frames as news_frames
    if style in KINDS:
        width, height = viewport or ((1280, 720) if platform == 'bilibili' else (720, 1280))
        digest = hashlib.sha256(repr(('news-diagram-v5-spoken-cues',style,labels,narration,duration,platform,width,height)).encode()).hexdigest()[:16]
        path = directory / f'news-diagram-{digest}.mp4'
        if not path.is_file():
            _encode_loop(path,width,height,24,news_frames(style,labels,narration,duration,24,width,height))
        return path.name
    wide = platform == "bilibili"
    width, height = (640, 360) if wide else (360, 640)
    fps = 12
    duration = min(36. if style == "drilling_flow" else 18., max(1., duration))
    cues = [min(duration - .5, duration * narration.find(label) / len(narration))
            if narration and label in narration else i * .45 for i, label in enumerate(labels)]
    cache_key = ("v2", style, labels, platform, duration, cues)
    if style == "depth":
        cache_key += (depth_extent,)
    elif style == "drilling_flow":
        drill_cues = drilling_flow_cues(narration)
        cache_key += (len(narration), drill_cues)
    digest = hashlib.sha256(repr(cache_key).encode()).hexdigest()[:16]
    path = directory / f"labels-{digest}.mp4"
    if path.is_file():
        return path.name
    accent = "#93E4D4" if wide else "#FFD481"
    if style == "depth":
        _encode_loop(path, width, height, fps, _depth_frames(labels, cues, duration, fps, width, height, accent, depth_extent))
        return path.name
    if style == "drilling_flow":
        _encode_loop(path, width, height, fps, _drilling_frames(drill_cues, duration, fps, width, height, narration))
        return path.name
    ink, muted, bg = "#F3F4F5", "#90A2AF", "#101920"
    heading = {"steps": "过程示意", "timeline": "报道时间线", "comparison": "文字对照"}[style]
    small = ImageFont.truetype(FONT_REGULAR, 16)
    short_font = ImageFont.truetype(FONT_BOLD, 20)
    base = Image.new("RGB", (width, height), bg)
    pen = ImageDraw.Draw(base)
    left = round(width * .085)
    right = width - left
    top = round(height * (.23 if wide else .22))
    pen.text((left, top), heading, fill=accent, font=small)
    pen.line((left, top + 29, right, top + 29), fill="#38515F", width=1)
    bottom = round(height * .71)
    horizontal = wide and style in {"steps", "comparison"}
    row_width = (right - left) // len(labels) if horizontal else right - left
    row_height = (bottom - top - 54) // (1 if horizontal else len(labels))
    for i in range(len(labels)):
        x = left + i * row_width if horizontal else left
        y = top + 55 if horizontal else top + 48 + i * row_height
        if style == "comparison":
            pen.rounded_rectangle((x, y, x + row_width - 10, y + row_height - 10), radius=8, fill="#1D303C")
        elif horizontal:
            pen.line((x+10, y+18, min(right, x+row_width+10), y+18), fill="#324A58", width=2)
        elif i < len(labels) - 1:
            pen.line((x+10, y+18, x+10, y+row_height+18), fill="#324A58", width=2)

    def frame_bytes():
        for frame in range(math.ceil(duration * fps)):
            canvas = base.copy()
            pen = ImageDraw.Draw(canvas)
            for i, label in enumerate(labels):
                level = max(0., min(1., (frame / fps - cues[i]) / .32))
                if not level:
                    continue
                x = left + i * row_width if horizontal else left
                y = top + 55 if horizontal else top + 48 + i * row_height
                tint = ink if level >= .8 else muted
                if style == "timeline":
                    date = re.match(r"[\d年月日号:]+", label)
                    stamp = date[0] if date else ""
                    pen.ellipse((x+5, y+13, x+15, y+23), fill=accent)
                    pen.text((x+31, y+3), stamp or label, fill=accent, font=short_font)
                    if stamp:
                        pen.text((x+31, y+28), label[len(stamp):], fill=tint, font=short_font)
                else:
                    pen.ellipse((x+2, y+9, x+19, y+26), fill=accent)
                    pen.text((x+10, y+17), str(i+1), fill=bg, anchor="mm")
                    x_text = x + (4 if horizontal and style == "steps" else 29)
                    y_text = y + (42 if horizontal and style == "steps" else 5) + round(5*(1-level))
                    wrap = (row_width - 36) // 20
                    for offset in range(0, len(label), wrap):
                        pen.text((x_text, y_text + 25*(offset//wrap)), label[offset:offset+wrap],
                                 fill=tint, font=short_font)
                    if style == "steps" and i < len(labels)-1:
                        end = (x+row_width+10, y+18) if horizontal else (x+10, y+row_height+18)
                        pen.line((x+10, y+18, x+10+(end[0]-x-10)*level, y+18+(end[1]-y-18)*level),
                                 fill=accent, width=2)
            yield canvas.tobytes()

    _encode_loop(path, width, height, fps, frame_bytes())
    return path.name


def _encode_loop(path, width, height, fps, frames):
    temp = path.with_suffix(".partial.mp4")
    try:
        cmd = [get_ffmpeg(), "-loglevel", "error", "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
               "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0",
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(temp)]
        if isinstance(frames, bytearray):
            subprocess.run(cmd, input=frames, capture_output=True, check=True, timeout=45,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            with subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
                try:
                    for frame in frames:
                        process.stdin.write(frame)
                    process.stdin.close()
                    if process.wait(timeout=45):
                        raise RuntimeError("无法生成动态图解。")
                finally:
                    if process.poll() is None:
                        process.kill()
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
