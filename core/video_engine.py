"""
Video Assembly Engine
利用 FFmpeg 自动化流水线压制高清视频、字幕烧录与环境 BGM 混音
"""

import subprocess
from pathlib import Path

from config import get_ffmpeg, ASSETS_DIR, PLATFORM_PRESETS
from core.visual_engine import generate_scene_slide, generate_news_slide, news_media_box


def _zoom_filter(motion, frames, width, height, fps, *, d=None, amount=.055):
    step = amount / max(1, frames - 1)
    zoom = f"max(1,1+{amount}-on*{step:.9f})" if motion == "pull" else f"min(1+on*{step:.9f},1+{amount})"
    return (f"scale=iw*2:ih*2,zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d={d or frames}:s={width}x{height}:fps={fps},setsar=1")


def _encode_news_scenes(case, platform, durations, output_dir, width, height, fps):
    end_time, previous_frame = 0.0, 0
    for index, (scene, duration) in enumerate(zip(case["scenes"], durations, strict=True)):
        end_time += duration
        end_frame = max(previous_frame+1, round(end_time * fps))
        frames, previous_frame = end_frame-previous_frame, end_frame
        asset = scene.get("asset")
        motion_video = asset and asset["kind"] == "video"
        episode = bool(scene.get("layout"))
        motion = scene.get("motion")
        zoom = motion in {"push", "pull"}
        motion_photo = (episode and asset and asset["kind"] == "image" and asset.get("preview")
                        and (zoom or (not motion and scene.get("zoom"))))
        slide = f"slide_{index:02d}.png"
        filename = f"cut_{index:02d}.mp4"
        target = output_dir / filename
        if case.get("resume") and target.is_file() and (output_dir / slide).is_file():
            yield filename
            continue
        generate_news_slide(case, index, width, height, platform, video_overlay=motion_video or motion_photo).save(output_dir / slide)
        fade = ",fade=t=in:d=0.22" if motion == "fade" else ""
        if case.get("resume") and index == len(durations)-1 and duration >= .8:
            fade += f",fade=t=out:st={duration-.40:.3f}:d=0.40"
        args = ["-i", slide]
        if motion_video:
            x0, y0, x1, y1 = (0, 0, width, height) if episode else news_media_box(width, height)
            crop_w, crop_h = x1 - x0, y1 - y0
            format_hint = ["-f", asset["video_format"]] if asset.get("video_format") else []
            camera = ("," + _zoom_filter(motion, frames, width, height, fps, d=1)
                      if zoom and episode else "")
            args = [
                "-stream_loop", "-1", "-protocol_whitelist", "file", *format_hint, "-i", asset["path"],
                "-i", slide, "-filter_complex",
                f"[0:v]fps={fps},scale={crop_w}:{crop_h}:force_original_aspect_ratio=increase,"
                f"crop={crop_w}:{crop_h},pad={width}:{height}:{x0}:{y0}{camera}[bg];"
                f"[bg][1:v]overlay{fade},format=yuv420p",
            ]
        elif motion_photo:
            args = ["-i", asset["preview"], "-i", slide, "-filter_complex",
                    f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                    f"{_zoom_filter(motion, frames, width, height, fps, amount=.09)}[bg];"
                    f"[bg][1:v]overlay{fade},format=yuv420p"]
        elif episode:
            movement = (_zoom_filter(motion, frames, width, height, fps, amount=.035)
                        if zoom else f"tpad=stop_mode=clone:stop={frames - 1}")
            args = ["-framerate", str(fps), *args, "-vf", f"{movement}{fade},format=yuv420p"]
        else:
            zoom = "min(1+on*0.0014,1.06)" if asset and scene.get("zoom", True) else "1"
            args += ["-vf", f"zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
                     f"d={frames}:s={width}x{height}:fps={fps},format=yuv420p"]
        partial = target.with_suffix(".partial.mp4")
        _run_ffmpeg(
            [get_ffmpeg(), "-y", "-filter_complex_threads", "1", *args, "-frames:v", str(frames),
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

    scenes = case["scenes"]
    scene_durations = audio_info["scene_durations"]

    concat_file = output_dir / "slides_playlist.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        if "news" in case:
            for filename in _encode_news_scenes(case, platform, scene_durations, output_dir, w, h, fps):
                f.write(f"file '{filename}'\n")
        else:
            for idx, duration in enumerate(scene_durations):
                slide = f"slide_{idx:02d}.png"
                generate_scene_slide(case, idx, w, h).save(output_dir / slide, "PNG")
                f.write(f"file '{slide}'\nduration {max(duration, 1.0):.2f}\n")
            # FFmpeg concat quirk: duplicate the last file so duration is respected.
            f.write(f"file '{slide}'\n")

    # 3. Check for BGM
    bgm_path = ASSETS_DIR / "bgm_ambient.wav"
    has_bgm = bgm_path.exists()

    output_video_name = f"video_{platform}.mp4"
    final_video_path = output_dir / output_video_name

    # 4. Construct FFmpeg command
    # Using relative paths within output_dir to prevent Windows libass path escaping issues
    cmd = [
        get_ffmpeg(), "-y",
        "-f", "concat", "-safe", "0", "-i", "slides_playlist.txt",
        "-i", "speech.mp3"
    ]

    if has_bgm:
        # Stream loop bgm
        cmd.extend([
            "-stream_loop", "-1", "-i", str(bgm_path.resolve())
        ])
        filter_complex = (
            "[0:v]format=yuv420p,ass=subtitles.ass[vout];"
            "[1:a]volume=1.0[speech];"
            "[2:a]volume=0.15[bgm];"
            "[speech][bgm]amix=inputs=2:duration=first[aout]"
        )
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]"
        ])
    else:
        filter_video = "format=yuv420p,ass=subtitles.ass"
        cmd.extend([
            "-vf", filter_video,
            "-map", "0:v",
            "-map", "1:a"
        ])

    cmd.extend([
        "-c:v", "libx264",
        "-preset", "faster",
        "-crf", "22",
        "-r", str(fps),
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        final_video_path.with_suffix(".partial.mp4").name if case.get("resume") else output_video_name,
    ])

    _run_ffmpeg(cmd, output_dir, platform, timeout=7200 if case.get("target_seconds") else 240)
    if case.get("resume"):
        final_video_path.with_suffix(".partial.mp4").replace(final_video_path)
    return final_video_path
