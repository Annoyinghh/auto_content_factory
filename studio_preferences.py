"""Apply this computer's video size once, at worker startup."""

import json
from pathlib import Path


def read_video_resolution(data_dir):
    path = Path(data_dir) / "studio-settings.json"
    try:
        if path.stat().st_size > 65536:
            return 1080
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        resolution = value.get("VideoResolution") if isinstance(value, dict) else None
        return resolution if type(resolution) is int and resolution in (720, 1080, 1440) else 1080
    except (OSError, ValueError):
        return 1080


def apply_export_preferences():
    from config import DATA_DIR, PLATFORM_PRESETS

    resolution = read_video_resolution(DATA_DIR)
    for preset in PLATFORM_PRESETS.values():
        factor = resolution / min(preset["width"], preset["height"])
        for key in ("width", "height"):
            preset[key] = 2 * round(preset[key] * factor / 2)
        for key in ("sub_fontsize", "sub_bottom_margin"):
            preset[key] = round(preset[key] * factor)


def render_profile_key():
    from config import PLATFORM_PRESETS

    return [(name, preset["width"], preset["height"], preset["fps"])
            for name, preset in sorted(PLATFORM_PRESETS.items())]
