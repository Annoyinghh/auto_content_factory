"""
Video Assembly Engine
利用 FFmpeg 自动化流水线压制高清视频、字幕烧录与环境 BGM 混音
"""

import subprocess
import math
from pathlib import Path

from config import get_ffmpeg, ASSETS_DIR, PLATFORM_PRESETS
from core.audio_engine import get_audio_duration
from core.episodes import TRANSITIONS
from core.motion_graphics import label_diagram, motion_loop
from core.visual_engine import generate_news_slide, news_media_box
from core.video_sequence import enough_footage


def _zoom_filter(motion, frames, width, height, fps, *, d=None, amount=.055):
    step = amount / max(1, frames - 1)
    zoom = f"max(1,1+{amount}-on*{step:.9f})" if motion == "pull" else f"min(1+on*{step:.9f},1+{amount})"
    return (f"scale=iw*2:ih*2,zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d={d or frames}:s={width}x{height}:fps={fps},setsar=1")


def _framing(width, height, position):
    if position == "fit":
        return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x12191f")
    horizontal = {"left": "(iw-ow)*0.20", "right": "(iw-ow)*0.80"}.get(position, "(iw-ow)/2")
    return (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:{horizontal}:(ih-oh)/2")


def _encode_news_scenes(case, platform, durations, output_dir, width, height, fps, *, source_audit=None):
    from core.news_frame import frame_box, motion_filter
    framed = case.get('news_frame') == 'source-news-v1'
    end_time, previous_frame = 0.0, 0
    footage_positions = {}
    planned_positions = {}
    bounded = {}
    for index, (scene, duration) in enumerate(zip(case["scenes"], durations, strict=True)):
        asset, visual = scene.get("asset") or {}, scene.get("visual") or {}
        if asset.get("kind") == "video" and "end" in visual:
            start, end = visual["start"], visual["end"]
            if (any(type(value) not in {int,float} or not math.isfinite(value) for value in (start,end))
                    or not 0 <= start < end <= asset.get("duration",0)):
                raise ValueError("原片片段的入点或出点无效。")
            key = (scene.get("clip_group",index),asset["path"],start,end)
            bounded[key] = bounded.get(key,0) + duration
    for key, total in bounded.items():
        if not enough_footage(key[3]-key[2],total):
            raise RuntimeError("对应动作片段时长不足，请补充连续素材或缩短这段口播；不会定格凑时长。")
    for index, (scene, duration) in enumerate(zip(case["scenes"], durations, strict=True)):
        end_time += duration
        end_frame = max(previous_frame+1, round(end_time * fps))
        frames, previous_frame = end_frame-previous_frame, end_frame
        asset = scene.get("asset")
        motion_video = asset and asset["kind"] == "video"
        episode = bool(scene.get("layout"))
        motion = scene.get("motion")
        graphic = scene.get("layout") == "graphic" and (not framed or bool(scene.get('diagram')))
        visual = scene.get("visual")
        offset = 0
        clip_seconds, speed = None, 1
        if motion_video and visual:
            group = scene.get("clip_group", index)
            if "end" in visual:
                group = (group,asset["path"],visual["start"],visual["end"])
                span = visual["end"]-visual["start"]
                speed = min(1,span/bounded[group])
                # Center on the verified action instead of only playing the lead-in.
                offset = visual["start"] + max(0,span-bounded[group])/2 + planned_positions.get(group,0) * speed
                clip_seconds = min(visual["end"]-offset,duration*speed)
            else:
                offset = visual["start"] + planned_positions.get(group, 0)
                if asset.get("duration"):
                    offset = min(offset, max(0, asset["duration"] - .25))
            planned_positions[group] = planned_positions.get(group, 0) + duration
        elif motion_video and episode:
            offset = footage_positions.get(asset["path"], 0)
            footage_positions[asset["path"]] = offset + duration
        if source_audit is not None:
            source = asset or {}
            start = offset if visual else offset % source['duration'] if source.get('duration', 0) >= 2 else 0
            consumed = clip_seconds if clip_seconds is not None else duration
            looped = bool(motion_video and not visual and start+consumed > source.get('duration', 0))
            source_audit.append({'start':end_time-duration,'end':end_time,'narration':scene['text'],
                'source_url':source.get('source_page') or source.get('page_url')
                             or next((row['url'] for row in scene.get('evidence', []) if row.get('url')), ''),
                'source_in':start if motion_video else None,
                'source_out':start+consumed if motion_video and not looped else None,
                'looped':looped,'intent':(visual or {}).get('intent',''),
                'reason':(visual or {}).get('reason',''),'evidence':scene.get('evidence',[])})
        zoom = motion in {"push", "pull"} and (not framed or asset and asset.get('kind')=='image'
                                               and asset.get('capture_kind')!='official_notice')
        motion_photo = ((episode or framed) and asset and asset["kind"] == "image" and asset.get("preview")
                        and (framed or visual or zoom or (not motion and scene.get("zoom"))))
        slide = f"slide_{index:02d}.png"
        filename = f"cut_{index:02d}.mp4"
        target = output_dir / filename
        if case.get("resume") and target.is_file() and (output_dir / slide).is_file():
            yield filename
            continue
        generate_news_slide(case, index, width, height, platform,
                            video_overlay=bool(motion_video or motion_photo or graphic)).save(output_dir / slide)
        fade = ",fade=t=in:d=0.22" if motion == "fade" else ""
        if case.get("resume") and index == len(durations)-1 and duration >= .8:
            fade += f",fade=t=out:st={duration-.40:.3f}:d=0.40"
        args = ["-i", slide]
        input_count = 1
        frame_hint = {**(asset or {}), 'diagram':bool(scene.get('diagram'))}
        media_box = frame_box(width, height, frame_hint) if framed else (0, 0, width, height)
        animated_plate = framed or episode and bool(scene.get("documentary_title") or scene.get("documentary_number")
                                                   or scene.get("chapter_handoff"))
        plate_args = ["-loop", "1", "-framerate", str(fps), "-i", slide] if animated_plate else ["-i", slide]
        plate = "[1:v]format=rgba,fade=t=in:st=0.10:d=0.42:alpha=1[ink];[bg][ink]" if animated_plate and not framed else "[bg][1:v]"
        if motion_video or graphic:
            x0, y0, x1, y1 = media_box if episode or framed else news_media_box(width, height)
            crop_w, crop_h = x1 - x0, y1 - y0
            if scene.get("diagram"):
                source = label_diagram(output_dir, scene["diagram"], scene["labels"], platform,
                                       narration=scene["text"], duration=duration, depth_extent=scene.get("depth_extent"),
                                       viewport=(crop_w,crop_h))
            else:
                source = motion_loop(output_dir, motion) if graphic else asset["path"]
            format_hint = ["-f", asset["video_format"]] if asset and asset.get("video_format") else []
            # Keep changing the excerpt on repeat appearances. Wrapping is bounded by the probe in the asset review.
            if motion_video and visual:
                seek = ["-ss", f"{offset:.3f}"] if offset else []
            else:
                seek = ["-ss", f"{offset % asset['duration']:.3f}"] if offset and asset.get("duration", 0) >= 2 else []
            camera = ("," + _zoom_filter(motion, frames, crop_w, crop_h, fps, d=1)
                      if zoom and episode else "")
            loop = [] if visual else ["-stream_loop", "-1"]
            hold = (",tpad=stop_mode=clone:stop=1" if clip_seconds is not None else
                    f",tpad=stop_mode=clone:stop_duration={duration:.3f}" if visual else "")
            interval = (f"trim=duration={clip_seconds:.6f},setpts=(PTS-STARTPTS)/{speed:.9f},"
                        if clip_seconds is not None else "")
            framing = _framing(crop_w, crop_h, 'fit' if framed else visual["framing"] if visual else "center")
            args = [*loop, "-protocol_whitelist", "file", *seek, *format_hint, "-i", source, *plate_args]
            input_count = 2
            if (not framed and episode and clip_seconds is not None and visual["framing"] == "fit" and not camera
                    and (width < height or asset.get('width',width) < asset.get('height',height))):
                # Keep the full action in view when a portrait crop would lose it.
                # Blur the same continuous frames at quarter size before upscaling the backdrop.
                low_width = max(2, width // 8 * 2)
                low_height = max(2, height // 8 * 2)
                graph = (
                    f"[0:v]{interval}fps={fps}{hold},split=2[back][front];"
                    f"[back]{_framing(low_width, low_height, 'center')},gblur=sigma=12,"
                    f"eq=brightness=-0.13:saturation=0.8,scale={width}:{height},setsar=1[wash];"
                    f"[front]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                    f"setsar=1[detail];[wash][detail]overlay=(W-w)/2:(H-h)/2:shortest=1[bg];"
                    f"{plate}overlay{fade}"
                )
            else:
                graph = (
                    f"[0:v]{interval}fps={fps},{framing}{hold}{camera},pad={width}:{height}:{x0}:{y0},setsar=1[bg];"
                    f"{plate}overlay{fade}"
                )
        elif motion_photo:
            x0, y0, x1, y1 = media_box
            crop_w, crop_h = x1-x0, y1-y0
            image_loop = ["-loop", "1", "-framerate", str(fps)] if (visual or framed) and not zoom else []
            args = [*image_loop, "-i", asset["preview"], *plate_args]
            input_count = 2
            framing = _framing(crop_w, crop_h, 'fit' if framed else visual["framing"] if visual else "center")
            camera = "," + _zoom_filter(motion, frames, crop_w, crop_h, fps, amount=.025 if framed else .09) if zoom or not visual and not framed else ""
            graph = (f"[0:v]{framing}{camera},pad={width}:{height}:{x0}:{y0}[bg];"
                     f"{plate}overlay{fade}")
        elif episode or framed:
            movement = (_zoom_filter(motion, frames, width, height, fps, amount=.035)
                        if zoom else f"tpad=stop_mode=clone:stop={frames - 1}")
            args = ["-framerate", str(fps), *args]
            graph = f"[0:v]{movement}{fade}"
        else:
            zoom = "min(1+on*0.0014,1.06)" if asset and scene.get("zoom", True) else "1"
            graph = (f"[0:v]zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
                     f"d={frames}:s={width}x{height}:fps={fps}")
        if framed:
            graph += motion_filter(width, height, fps, duration, end_time-duration, platform, frame_hint)
        if index and motion in TRANSITIONS and frames >= round(fps * .5) and not framed:
            args += ["-sseof", "-0.10", "-i", f"cut_{index-1:02d}.mp4"]
            normalize = f"fps={fps},settb=1/{fps},setpts=PTS-STARTPTS,setsar=1,format=yuv444p"
            graph += (f",{normalize}[incoming];[{input_count}:v]{normalize},"
                      f"tpad=stop_mode=clone:stop_duration=0.4[previous];"
                      f"[previous][incoming]xfade=transition={TRANSITIONS[motion]}:duration=0.27:offset=0")
        graph += ",format=yuv420p[out]"
        partial = target.with_suffix(".partial.mp4")
        _run_ffmpeg(
            [get_ffmpeg(), "-y", "-filter_complex_threads", "1", *args, "-filter_complex", graph, "-map", "[out]", "-frames:v", str(frames),
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", partial.name],
            output_dir, platform,
        )
        partial.replace(target)
        yield filename


def _run_ffmpeg(command, output_dir, platform, *, timeout=240):
    result = subprocess.run(
        command, cwd=output_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"{platform} 视频渲染失败\n{result.stderr[-1200:]}")
    return result.stdout

def render_platform_video(
    case: dict,
    platform: str,
    audio_info: dict,
    output_dir: Path
) -> Path:
    """
    Renders the final ready-to-post MP4 video for a given platform.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    preset = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["douyin"])
    w = preset["width"]
    h = preset["height"]
    fps = preset.get("fps", 30)

    scene_durations = audio_info["scene_durations"]
    from core.video_sequence import expand_sequences
    expanded,scene_durations = expand_sequences(case['scenes'],scene_durations)
    case = {**case,'scenes':expanded}
    bindings = [] if case.get('news_frame') == 'source-news-v1' else None

    concat_file = output_dir / "slides_playlist.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for filename in _encode_news_scenes(case, platform, scene_durations, output_dir, w, h, fps, source_audit=bindings):
            f.write(f"file '{filename}'\n")

    # 3. Check for BGM
    bgm_path = ASSETS_DIR / "bgm_ambient.wav"
    has_bgm = bgm_path.exists()

    output_video_name = f"video_{platform}.mp4"
    final_video_path = output_dir / output_video_name

    # 4. Construct FFmpeg command
    # Using relative paths within output_dir to prevent Windows libass path escaping issues
    cmd = [
        get_ffmpeg(), "-nostdin", "-y", "-nostats", "-progress", "pipe:1",
        "-f", "concat", "-safe", "0", "-i", "slides_playlist.txt",
        "-i", "speech.mp3"
    ]

    if has_bgm:
        # Stream loop bgm
        cmd.extend([
            "-stream_loop", "-1", "-i", str(bgm_path.resolve())
        ])
    cmd.extend(["-vf", "format=yuv420p,ass=subtitles.ass", "-map", "0:v:0"])
    if has_bgm:
        filter_complex = (
            "[1:a]aresample=24000,asetpts=N/SR/TB[speech];"
            "[2:a]aresample=24000,asetpts=N/SR/TB,volume=0.15[bgm];"
            "[speech][bgm]amix=inputs=2:duration=first[aout]"
        )
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[aout]"
        ])
    else:
        cmd.extend(["-map", "1:a"])

    cmd.extend([
        "-c:v", "libx264",
        "-preset", "faster",
        "-crf", "22",
        "-r", str(fps),
        "-c:a", "aac",
        "-b:a", "192k",
    ])
    duration = audio_info["total_duration"]
    partial = final_video_path.with_suffix(".partial.mp4")
    cmd.extend(["-t", f"{duration:.6f}", partial.name])
    try:
        progress = _run_ffmpeg(cmd, output_dir, platform, timeout=7200 if case.get("target_seconds") else 240)
        actual = get_audio_duration(str(partial), strict=True)
        if abs(actual - duration) > max(.15, 3/fps):
            raise RuntimeError(f"视频合成时长异常：实测 {actual:.2f} 秒，口播 {duration:.2f} 秒。")
        frames = next((int(line.partition("=")[2]) for line in reversed(progress.splitlines())
                       if line.startswith("frame=") and line.partition("=")[2].strip().isdigit()), 0)
        if abs(frames - round(duration * fps)) > 4:
            raise RuntimeError(f"视频画面帧数异常：实测 {frames} 帧，口播约需 {round(duration * fps)} 帧。")
        progress = _run_ffmpeg([get_ffmpeg(), "-nostdin", "-v", "error", "-i", partial.name,
                                "-map", "0:a:0", "-c:a", "copy", "-progress", "pipe:1", "-f", "null", "-"],
                               output_dir, platform)
        end_us = next((int(line.partition("=")[2]) for line in reversed(progress.splitlines())
                       if line.startswith("out_time_us=") and line.partition("=")[2].strip().isdigit()), 0)
        if abs(end_us / 1_000_000 - duration) > .15:
            raise RuntimeError(f"视频音轨时长异常：实测 {end_us / 1_000_000:.2f} 秒，口播 {duration:.2f} 秒。")
        partial.replace(final_video_path)
        if bindings is not None:
            import json
            (output_dir/'shot-sources.json').write_text(json.dumps(bindings,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        partial.unlink(missing_ok=True)
    return final_video_path
