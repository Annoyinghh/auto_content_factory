"""
Audio Synthesis and Subtitle Alignment Engine
基于 edge-tts 的高清语音合成与多平台字幕对齐引擎
"""

import os
import asyncio
import subprocess
import wave
import hashlib
import shutil
from pathlib import Path
import edge_tts

from config import get_ffmpeg, PLATFORM_PRESETS

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EDGE_SCENE_TIMEOUT = 45
WINDOWS_SPEECH = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $voices = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN' })
    if (!$voices.Count) { throw 'No zh-CN Windows speech voice is installed.' }
    $name = @($voices | Where-Object { $_.VoiceInfo.Name -like "*$env:FACTORY_VOICE_PREFERENCE*" })[0]
    if (!$name) { $name = $voices[0] }
    $synth.SelectVoice($name.VoiceInfo.Name)
    $synth.Rate = [int]$env:FACTORY_VOICE_RATE
    $synth.SetOutputToWaveFile($env:FACTORY_VOICE_WAV)
    $synth.Speak([IO.File]::ReadAllText($env:FACTORY_VOICE_TEXT, [Text.Encoding]::UTF8))
} finally { $synth.Dispose() }
"""

def get_audio_duration(file_path: str, *, strict=False) -> float:
    """Uses ffmpeg to probe media duration in seconds."""
    cmd = [get_ffmpeg(), "-nostdin", "-i", file_path]
    res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore",
                         creationflags=NO_WINDOW)
    for line in res.stderr.split("\n"):
        if "Duration:" in line:
            # Duration: 00:00:05.81, start: ...
            parts = line.split("Duration:")[1].split(",")[0].strip().split(":")
            hours = float(parts[0])
            mins = float(parts[1])
            secs = float(parts[2])
            return hours * 3600 + mins * 60 + secs
    if strict:
        raise RuntimeError(f"无法读取媒体时长：{Path(file_path).name}")
    return 5.0 # fallback

def format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp H:MM:SS.cc"""
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{mins:02d}:{secs:05.2f}"

def format_srt_time(seconds: float) -> str:
    """Format seconds into SRT timestamp HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis = 0
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

async def _synthesize_text(text: str, voice: str, rate: str, output_path: str, max_retries: int = 2):
    """Synthesizes speech to an mp3 file with automatic retry on network glitches."""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate, connect_timeout=10, receive_timeout=30)
            await asyncio.wait_for(communicate.save(output_path), timeout=EDGE_SCENE_TIMEOUT)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                return
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                await asyncio.sleep(1.0 * attempt)
    raise RuntimeError(f"配音失败（{type(last_err).__name__ if last_err else '音频为空'}）") from last_err


def _synthesize_windows(text, voice, rate, output):
    text_file, wav = output.with_suffix(".voice.txt"), output.with_suffix(".wav")
    text_file.write_text(text, encoding="utf-8")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_SPEECH],
            env={**os.environ, "FACTORY_VOICE_TEXT": str(text_file), "FACTORY_VOICE_WAV": str(wav),
                 "FACTORY_VOICE_PREFERENCE": "Huihui" if "Xiaoxiao" in voice else "Kangkang",
                 "FACTORY_VOICE_RATE": str(round(int(rate.rstrip("%")) / 10))},
            timeout=45, capture_output=True, creationflags=NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError("Windows 中文语音合成失败，请检查系统是否已安装中文语音包。")
        subprocess.run(
            [get_ffmpeg(), "-y", "-i", str(wav), "-ar", "24000", "-ac", "1", "-b:a", "48k", str(output)],
            timeout=25, capture_output=True, check=True, creationflags=NO_WINDOW,
        )
    finally:
        text_file.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)


def _assemble_speech(scene_files, master):
    # Decode each clip independently so MP3 encoder padding never becomes narration time.
    sample_rate = 24_000
    pcm_file = master.with_suffix(".partial.wav")
    encoded = master.with_suffix(".partial.mp3")
    durations = []
    try:
        with wave.open(str(pcm_file), "wb") as track:
            track.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
            for source in scene_files:
                decoded = subprocess.run(
                    [get_ffmpeg(), "-nostdin", "-loglevel", "error", "-i", str(source), "-map", "0:a:0",
                     "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1"],
                    capture_output=True, check=True, timeout=30, creationflags=NO_WINDOW,
                ).stdout
                if not decoded or len(decoded) % 2:
                    raise RuntimeError(f"配音片段无法解码：{source.name}")
                track.writeframesraw(decoded)
                durations.append(len(decoded) / (2 * sample_rate))
        subprocess.run(
            [get_ffmpeg(), "-nostdin", "-loglevel", "error", "-y", "-i", str(pcm_file),
             "-c:a", "libmp3lame", "-b:a", "96k", str(encoded)],
            capture_output=True, check=True, timeout=150, creationflags=NO_WINDOW,
        )
        encoded.replace(master)
        return durations
    finally:
        pcm_file.unlink(missing_ok=True)
        encoded.unlink(missing_ok=True)


def generate_voice_and_subtitles(case: dict, platform: str, output_dir: Path) -> dict:
    """
    Synthesizes speech for all scenes in the case, concatenates them,
    and produces synchronized ASS and SRT subtitles tailored to the platform.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    preset = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["douyin"])
    voice = preset["voice"]
    rate = preset["rate"]
    
    scenes = case["scenes"]
    scene_audios = []
    offline_voice = False
    speech_cache=output_dir.parent/'.speech-cache-v1' if case.get('resume') else None
    if speech_cache:
        speech_cache.mkdir(exist_ok=True)
    
    # 1. Synthesize each scene audio
    for idx, sc in enumerate(scenes):
        scene_mp3 = output_dir / f"scene_{idx:02d}.mp3"
        cached=None
        valid_cache=False
        if speech_cache:
            key=hashlib.sha256('\0'.join((voice,rate,sc['text'])).encode('utf-8')).hexdigest()
            cached=speech_cache/(key+'.mp3')
            if cached.is_file():
                try:
                    valid_cache=get_audio_duration(cached,strict=True)>0
                except (OSError,RuntimeError):
                    pass
            if valid_cache and not scene_mp3.is_file():
                shutil.copyfile(cached,scene_mp3)
        if not (case.get("resume") and scene_mp3.is_file()):
            partial = scene_mp3.with_suffix(".partial.mp3")
            if not offline_voice:
                try:
                    asyncio.run(_synthesize_text(sc["text"], voice, rate, str(partial)))
                except RuntimeError:
                    if os.name != "nt":
                        raise
                    offline_voice = True
            if offline_voice:
                _synthesize_windows(sc["text"], voice, rate, partial)
            partial.replace(scene_mp3)
        if cached and not valid_cache:
            pending=cached.with_suffix('.partial.mp3')
            shutil.copyfile(scene_mp3,pending)
            pending.replace(cached)
        scene_audios.append(scene_mp3)

    master_audio = output_dir / "speech.mp3"
    scene_durations = _assemble_speech(scene_audios, master_audio)
    for scene, duration in zip(scenes, scene_durations, strict=True):
        scene["duration"] = duration
    total_duration = sum(scene_durations)

    # 3. Generate Subtitles (ASS & SRT)
    # Split each scene text into short, punchy clauses for mobile reading
    sub_entries = []
    current_time = 0.0
    
    for idx, sc in enumerate(scenes):
        dur = scene_durations[idx]
        text = sc["text"]
        # Split by punctuation
        raw_parts = []
        cur_buf = ""
        for char in text:
            cur_buf += char
            if char in "，。！？；!?,;":
                if len(cur_buf.strip()) > 0:
                    raw_parts.append(cur_buf.strip())
                cur_buf = ""
        if cur_buf.strip():
            raw_parts.append(cur_buf.strip())
            
        # Distribute time proportionally by character length
        total_chars = sum(len(p) for p in raw_parts) or 1
        part_start = current_time
        for p in raw_parts:
            part_dur = (len(p) / total_chars) * dur
            part_end = part_start + part_dur
            sub_entries.append({
                "start": part_start,
                "end": part_end,
                "text": p
            })
            part_start = part_end
            
        current_time += dur

    # Build ASS content (Advanced SubStation Alpha with custom styling)
    sub_fontsize = preset.get("sub_fontsize", 48)
    sub_margin_v = preset.get("sub_bottom_margin", 320)
    if case.get('news_frame') == 'source-news-v1':
        sub_margin_v = round(preset['height'] * (.065 if preset['width'] > preset['height'] else .115))
    w = preset["width"]
    h = preset["height"]

    ass_path = output_dir / "subtitles.ass"
    srt_path = output_dir / "subtitles.srt"

    # ASS template
    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{sub_fontsize},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4.5,1.5,2,40,40,{sub_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    for entry in sub_entries:
        s_ass = format_ass_time(entry["start"])
        e_ass = format_ass_time(entry["end"])
        t = entry["text"]
        # Highlight punctuation removal for cleaner look
        t_clean = t.rstrip("，。！？；!?,;").translate(str.maketrans({"{": "｛", "}": "｝", "\\": "＼"}))
        ass_content += f"Dialogue: 0,{s_ass},{e_ass},Default,,0,0,0,,{t_clean}\n"

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    # Build SRT content
    srt_lines = []
    for i, entry in enumerate(sub_entries, 1):
        s_srt = format_srt_time(entry["start"])
        e_srt = format_srt_time(entry["end"])
        t_clean = entry["text"].rstrip("，。！？；!?,;")
        srt_lines.append(f"{i}\n{s_srt} --> {e_srt}\n{t_clean}\n")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))

    return {
        "master_audio": master_audio,
        "total_duration": total_duration,
        "scene_durations": scene_durations,
        "ass_file": ass_path,
        "srt_file": srt_path
    }
