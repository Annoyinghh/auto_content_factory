"""
Multi-Platform Publisher & Package Assembler
将各平台定制视频、爆款封面、文案标签打包并提供自动化发布支持
"""

import json

from config import DOUYIN_BRIEF_SECONDS, OUTPUT_DIR, PLATFORM_PRESETS
from core.audio_engine import generate_voice_and_subtitles
from core.reading import build_pages, draw_waveform
from core.video_engine import render_platform_video
from core.visual_engine import generate_news_slide


def package_case_for_all_platforms(case: dict, platforms, *, output_root=OUTPUT_DIR, flat=False) -> dict:
    """
    Builds the full multi-platform release bundle for a specific case:
    - 抖音 (Douyin)
    - 小红书 (Xiaohongshu)
    - 哔哩哔哩 (Bilibili)
    """
    case_id = case["id"]
    platforms = tuple(platforms)
    if flat and len(platforms) != 1:
        raise ValueError("单平台导出目录只能保存一个平台的成片。")
    case_output_root = output_root if flat else output_root / case_id
    case_output_root.mkdir(parents=True, exist_ok=True)

    results = {
        "case_id": case_id,
        "title": case["title"],
        "platforms": {}
    }

    for platform_key in platforms:
        platform_dir = case_output_root if flat else case_output_root / platform_key
        platform_dir.mkdir(parents=True, exist_ok=True)
        preset = PLATFORM_PRESETS[platform_key]

        # 1. Synthesize audio & synchronized subtitles
        audio_info = generate_voice_and_subtitles(case, platform_key, platform_dir)
        target = case.get("target_seconds")
        duration = audio_info["total_duration"]
        if platform_key in {"douyin", "xiaohongshu"} and target in DOUYIN_BRIEF_SECONDS:
            from core.quality import duration_bounds
            low, high = duration_bounds(platform_key, target, source_limited=case.get('target_is_ceiling', False))
            if not low <= duration <= high:
                raise RuntimeError(f"本次口播实测 {duration:.0f} 秒，请将分镜调整到 {low}–{high} 秒。")
        elif target and not 600 <= duration <= 900:
            raise RuntimeError(f"本次口播实测 {duration / 60:.1f} 分钟，需调整到 10–15 分钟后重新剪辑。")

        # 2. Render platform-specific video
        video_path = render_platform_video(case, platform_key, audio_info, platform_dir)

        # 3. Platform-specific covers & copy files
        cover_path = None
        meta = case["platform_metadata"][platform_key]
        if platform_key in ("xiaohongshu", "bilibili"):
            cover_path = platform_dir / f"cover_{platform_key}.png"
            generate_news_slide(case, 0, preset["cover_width"], preset["cover_height"], platform_key, cover=True).save(cover_path)
        description = meta.get("post_content") or meta.get("desc") or meta["call_to_action"]
        tags = " ".join(f"#{tag}" for tag in meta["tags"])
        news = case["news"]
        credited = {source["page_url"]: source for asset in news.get("media", ())
                    for source in asset.get("comparison", {}).get("sources", [asset]) if not source.get("local")}
        attribution = "\n".join(
            f"示意素材（已裁切或节选） · {asset['title']} / {asset['author']} / {asset['license']}\n"
            f"{asset['page_url']}\n{asset['license_url']}"
            for asset in credited.values()
        )
        source_label='资料出处' if news.get('is_reference') else '资讯来源'
        source_credit = f"{source_label} · {news['source']}\n{news['source_url']}\n" if news["source_url"] else ""
        if news.get("sources"):
            source_credit = "\n".join(f"{source_label} · {entry['source']} · {entry['title']}"
                + (f" · 原发布 {entry['published']}" if news.get('is_reference') and entry.get('published') else '')
                + f"\n{entry['url']}" for entry in news["sources"]) + "\n"
            starts = case.get("chapter_starts", [])
            timestamps = []
            for chapter in starts:
                seconds = int(sum(audio_info["scene_durations"][:chapter["scene"]]))
                timestamps.append(f"{seconds // 60:02d}:{seconds % 60:02d} {chapter['title']}")
            if timestamps:
                description += "\n\n章节\n" + "\n".join(timestamps)
        if any(scene.get("diagram") == "drilling_flow" for scene in case["scenes"]):
            from core.video_styles import DRILLING_FLOW_SOURCES

            source_credit += "".join(f"原理示意参考 · {name}\n{url}\n" for name, url in DRILLING_FLOW_SOURCES)
        credit_caption = f"{source_credit}AI 合成配音\n\n{attribution}".strip()
        copy_text = (
            f"{meta['title']}\n\n{description}\n\n{tags}\n\n"
            f"{credit_caption}"
        )

        # Save copy text file
        copy_file = platform_dir / "publish_info.txt"
        with open(copy_file, "w", encoding="utf-8") as f:
            f.write(copy_text)

        results["platforms"][platform_key] = {
            "name": preset["name"],
            "video": str(video_path),
            "cover": str(cover_path) if cover_path else None,
            "copy_file": str(copy_file),
            "duration": audio_info["total_duration"],
            "reading_pages": build_pages(case, audio_info, video_path),
            "speech_waveform": draw_waveform(video_path),
            "caption": copy_text,
            "credit_caption": credit_caption,
            "title": meta["title"],
            "tags": meta["tags"],
        }
        references = [{"chapter": mark["title"], **mark["style_reference"]}
                      for mark in case.get("chapter_starts", []) if mark.get("style_reference")]
        if references:
            results["platforms"][platform_key]["style_references"] = references
        video_references = [{"chapter": mark["title"], "type": mark["video_style"]["name"],
                             **mark["video_style"]["reference"]}
                            for mark in case.get("chapter_starts", []) if mark.get("video_style")]
        if video_references:
            results["platforms"][platform_key]["video_references"] = video_references
        models = (case.get("editorial_plan") or {}).get("models")
        if models:
            choices = []
            for scene in case["scenes"]:
                if not scene.get("visual") or (choices and choices[-1]["group"] == scene.get("clip_group")
                                               and scene.get("clip_group") is not None):
                    continue
                choices.append({**scene["visual"], "group": scene.get("clip_group"),
                                "chapter": scene.get("chapter_title", ""), "title": scene["title"],
                                "media": scene.get("asset", {}).get("page_url", "")})
            results["platforms"][platform_key]["visual_decisions"] = {"models": models, "shots": choices}

    manifest_path = case_output_root / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    return results
