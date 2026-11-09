"""Build the chapter index from the exact version passed to the renderer."""

import math
import subprocess
from itertools import accumulate
from pathlib import Path

from config import get_ffmpeg


def draw_waveform(video):
    speech = Path(video).with_name("speech.mp3")
    waveform = speech.with_name("speech-waveform.png")
    if not speech.is_file():
        return None
    try:
        subprocess.run([get_ffmpeg(), "-loglevel", "error", "-nostdin", "-y", "-i", str(speech),
                        "-filter_complex", "showwavespic=s=880x48:split_channels=0:colors=0x76d1c4",
                        "-frames:v", "1", str(waveform)], check=True, capture_output=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        waveform.unlink(missing_ok=True)
    return waveform.name if waveform.is_file() else None


def build_pages(case, audio_info, video):
    scenes = case["scenes"]
    durations = audio_info["scene_durations"]
    if len(scenes) != len(durations) or any(not math.isfinite(d) or d <= 0 for d in durations):
        return []
    offsets = list(accumulate(durations, initial=0.0))
    marks = case.get("chapter_starts") or [{"scene": i, "title": shot.get("title") or f"镜头 {i+1}"}
                                           for i, shot in enumerate(scenes)]
    starts = [entry["scene"] for entry in marks]
    if starts != sorted(set(starts)) or any(not 0 <= i < len(scenes) for i in starts):
        return []
    news = case.get("news", {})
    sources = {source["title"]: source for source in news.get("sources", ())}
    pages = []
    video = Path(video)
    for index, (mark, end) in enumerate(zip(marks, [*starts[1:], len(scenes)], strict=True), 1):
        start, title = mark["scene"], mark["title"]
        seconds = offsets[start]
        if seconds >= audio_info["total_duration"]:
            break
        narration = " ".join(str(shot.get("text", "")).strip() for shot in scenes[start:end]).strip()
        poster = video.with_name(f"reading-{index:02d}.jpg")
        if video.is_file():
            try:
                subprocess.run([get_ffmpeg(), "-loglevel", "error", "-nostdin", "-y", "-ss", f"{seconds:.3f}",
                                "-i", str(video), "-frames:v", "1", "-vf", "scale=640:640:force_original_aspect_ratio=decrease",
                                "-q:v", "3", str(poster)], check=True, capture_output=True, timeout=12,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                poster.unlink(missing_ok=True)
        related = news.get("chapter_sources", {}).get(title, [])
        source = related[0] if related else sources.get(title, {})
        pages.append({"title": title, "start_ms": round(seconds * 1000),
                      "excerpt": narration[:320] + ("…" if len(narration) > 320 else ""),
                      "source": source.get("source", news.get("source", "")),
                      "source_url": source.get("url", news.get("source_url", "")),
                      "frame": poster.name if poster.is_file() else None,
                      **({"sources": related} if related else {})})
    return pages
