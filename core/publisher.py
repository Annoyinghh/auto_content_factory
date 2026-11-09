"""
Multi-Platform Publisher & Package Assembler
将各平台定制视频、爆款封面、文案标签打包并提供自动化发布支持
"""

import os
import json
from pathlib import Path
from config import OUTPUT_DIR, PLATFORM_PRESETS
from core.audio_engine import generate_voice_and_subtitles
from core.visual_engine import generate_xiaohongshu_cover, generate_bilibili_cover, generate_news_slide
from core.video_engine import render_platform_video
from core.reading import build_pages, draw_waveform

def package_case_for_all_platforms(case: dict, platforms=None, *, output_root=OUTPUT_DIR) -> dict:
    """
    Builds the full multi-platform release bundle for a specific case:
    - 抖音 (Douyin)
    - 小红书 (Xiaohongshu)
    - 哔哩哔哩 (Bilibili)
    - 微信视频号 (WeChat Channels)
    """
    case_id = case["id"]
    case_output_root = output_root / case_id
    case_output_root.mkdir(parents=True, exist_ok=True)

    results = {
        "case_id": case_id,
        "title": case["title"],
        "platforms": {}
    }

    # Iterate through all 4 target platforms
    for platform_key in platforms or PLATFORM_PRESETS:
        platform_dir = case_output_root / platform_key
        platform_dir.mkdir(parents=True, exist_ok=True)
        preset = PLATFORM_PRESETS[platform_key]

        # 1. Synthesize audio & synchronized subtitles
        audio_info = generate_voice_and_subtitles(case, platform_key, platform_dir)
        if case.get("target_seconds") and not 600 <= audio_info["total_duration"] <= 900:
            raise RuntimeError(f"本次口播实测 {audio_info['total_duration'] / 60:.1f} 分钟，需调整到 10–15 分钟后重新剪辑。")

        # 2. Render platform-specific video
        video_path = render_platform_video(case, platform_key, audio_info, platform_dir)

        # 3. Platform-specific covers & copy files
        cover_path = None
        copy_text = ""
        meta = case["platform_metadata"][platform_key]

        if "news" in case:
            if platform_key in ("xiaohongshu", "bilibili"):
                cover_path = platform_dir / f"cover_{platform_key}.png"
                generate_news_slide(case, 0, preset["cover_width"], preset["cover_height"], platform_key, cover=True).save(cover_path)
            description = meta.get("post_content") or meta.get("desc") or meta["call_to_action"]
            tags = " ".join(f"#{tag}" for tag in meta["tags"])
            news = case["news"]
            attribution = "\n".join(
                f"示意素材（已裁切或节选） · {asset['title']} / {asset['author']} / {asset['license']}\n"
                f"{asset['page_url']}\n{asset['license_url']}"
                for asset in news.get("media", ())
                if not asset.get("local")
            )
            source_credit = f"资讯来源 · {news['source']}\n{news['source_url']}\n" if news["source_url"] else ""
            if news.get("sources"):
                source_credit = "\n".join(f"资讯来源 · {entry['source']} · {entry['title']}\n{entry['url']}" for entry in news["sources"]) + "\n"
                starts = case.get("chapter_starts", [])
                timestamps = []
                for chapter in starts:
                    seconds = int(sum(audio_info["scene_durations"][:chapter["scene"]]))
                    timestamps.append(f"{seconds // 60:02d}:{seconds % 60:02d} {chapter['title']}")
                if timestamps:
                    description += "\n\n章节\n" + "\n".join(timestamps)
            credit_caption = f"{source_credit}AI 合成配音\n\n{attribution}".strip()
            copy_text = (
                f"{meta['title']}\n\n{description}\n\n{tags}\n\n"
                f"{credit_caption}"
            )
        elif platform_key == "xiaohongshu":
            cover_path = platform_dir / "cover_xiaohongshu.png"
            generate_xiaohongshu_cover(case, str(cover_path))
            
            tags_str = " ".join([f"#{t}" for t in meta["tags"]])
            copy_text = f"""【小红书发布文案包】
--------------------------------------------------
📌 推荐标题：{meta['title']}

📝 笔记正文：
{meta['post_content']}

🏷️ 标签话题：
{tags_str}

💡 封面文件：{cover_path.name} (已按3:4高转化标准生成)
🎥 视频文件：{video_path.name}
--------------------------------------------------
"""

        elif platform_key == "bilibili":
            cover_path = platform_dir / "cover_bilibili.png"
            generate_bilibili_cover(case, str(cover_path))
            
            tags_str = ",".join(meta["tags"])
            copy_text = f"""【B站发布文案包】
--------------------------------------------------
📌 推荐标题：{meta['title']}

📂 推荐分区：知识 > 商业·财经 或 搞笑·奇闻
🏷️ 视频标签：{tags_str}

📝 简介内容：
{meta['desc']}

💡 封面文件：{cover_path.name} (16:9超清横屏封面)
🎥 视频文件：{video_path.name} (16:9横屏超清视频)
--------------------------------------------------
"""

        elif platform_key == "douyin":
            tags_str = " ".join([f"#{t}" for t in meta["tags"]])
            copy_text = f"""【抖音发布文案包】
--------------------------------------------------
📌 推荐标题：{meta['title']}

🏷️ 热门标签：{tags_str}

💬 互动置顶引导：
{meta.get('call_to_action', '你看懂这个商业闭环了吗？欢迎在评论区讨论！')}

⏰ 最佳发布时间建议：中午 12:00-13:00 或 晚上 19:30-21:30
🎥 视频文件：{video_path.name} (9:16高完播竖屏，带双色粗描边安全区字幕)
--------------------------------------------------
"""

        elif platform_key == "wechat_channels":
            tags_str = " ".join([f"#{t}" for t in meta["tags"]])
            copy_text = f"""【微信视频号发布文案包】
--------------------------------------------------
📌 推荐动态描述：
{meta['title']}

{meta.get('summary', '')}

🏷️ 话题标签：{tags_str}

👥 朋友圈转发推荐导语：
“商业的本质不是卖物理产品，而是洞察人性。这个海外商业案例非常值得创业者思考。”

🎥 视频文件：{video_path.name}
--------------------------------------------------
"""

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
        }
        if "news" in case:
            results["platforms"][platform_key].update(
                caption=copy_text, credit_caption=credit_caption, title=meta["title"], tags=meta["tags"],
            )

    # Save manifest.json
    manifest_path = case_output_root / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    if "news" in case:
        return results

    # Save a comprehensive Markdown guide
    guide_path = case_output_root / "README_PUBLISH.md"
    guide_md = f"""# {case['title']} - 全平台发布一览

本项目已全自动生成面向四大主流平台的独立成片与配套物料：

## 1. 抖音 (Douyin)
* 视频文件: `douyin/video_douyin.mp4` (9:16 竖屏，高节奏，快问快答)
* 文案与标签: 见 `douyin/publish_info.txt`

## 2. 小红书 (Xiaohongshu)
* 视频文件: `xiaohongshu/video_xiaohongshu.mp4`
* 爆款封面: `xiaohongshu/cover_xiaohongshu.png` (3:4 高吸睛视觉)
* 笔记正文: 见 `xiaohongshu/publish_info.txt` (含丰富 Emoji 排版与破冰引导)

## 3. 哔哩哔哩 (Bilibili)
* 视频文件: `bilibili/video_bilibili.mp4` (16:9 横屏，故事与商业逻辑拆解)
* 横屏封面: `bilibili/cover_bilibili.png` (16:9 高清缩略图)
* 稿件简介: 见 `bilibili/publish_info.txt` (带时间戳索引)

## 4. 微信视频号 (WeChat Channels)
* 视频文件: `wechat_channels/video_wechat_channels.mp4` (9:16 竖屏)
* 转发导语: 见 `wechat_channels/publish_info.txt` (适合朋友圈与社群高社交货币分发)
"""
    with open(guide_path, "w", encoding="utf-8") as f:
        f.write(guide_md)

    return results
