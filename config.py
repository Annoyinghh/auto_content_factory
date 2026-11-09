import os
import sys
from pathlib import Path
from functools import cache

BASE_DIR = Path(__file__).resolve().parent
CORE_DIR = BASE_DIR / "core"
ASSETS_DIR = BASE_DIR / "assets"
APP_ICON = ASSETS_DIR / "screening" / "app.ico"
DATA_DIR = (
    Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AutoContentFactory"
    if getattr(sys, "frozen", False)
    else BASE_DIR
)
OUTPUT_DIR = DATA_DIR / "output"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

@cache
def get_ffmpeg():
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()

# Windows Fonts
FONT_REGULAR = r"C:\Windows\Fonts\msyh.ttc"      # Microsoft YaHei
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"       # Microsoft YaHei Bold
if not os.path.exists(FONT_BOLD):
    FONT_BOLD = FONT_REGULAR
FONT_HEI = r"C:\Windows\Fonts\simhei.ttf"        # SimHei

# Platform Preset Dimensions
PLATFORM_PRESETS = {
    "douyin": {
        "name": "抖音 (Douyin)",
        "width": 1080,
        "height": 1920,
        "aspect_ratio": "9:16",
        "fps": 30,
        "sub_fontsize": 48,
        "sub_bottom_margin": 320,
        "voice": "zh-CN-YunxiNeural",
        "rate": "+15%",
        "tone": "快节奏、抓眼球、反常识、反转强"
    },
    "xiaohongshu": {
        "name": "小红书 (Xiaohongshu)",
        "width": 1080,
        "height": 1920, # or 1080x1440 (3:4)
        "cover_width": 1080,
        "cover_height": 1440,
        "aspect_ratio": "9:16",
        "fps": 30,
        "sub_fontsize": 46,
        "sub_bottom_margin": 300,
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+10%",
        "tone": "高颜值、干货提炼、情绪共鸣、搞钱思维"
    },
    "bilibili": {
        "name": "哔哩哔哩 (Bilibili)",
        "width": 1920,
        "height": 1080,
        "cover_width": 1920,
        "cover_height": 1080,
        "aspect_ratio": "16:9",
        "fps": 30,
        "sub_fontsize": 44,
        "sub_bottom_margin": 100,
        "voice": "zh-CN-YunjianNeural",
        "rate": "+8%",
        "tone": "深度叙事、纪录片质感、商业本质拆解、有梗"
    },
    "wechat_channels": {
        "name": "微信视频号 (Channels)",
        "width": 1080,
        "height": 1920,
        "aspect_ratio": "9:16",
        "fps": 30,
        "sub_fontsize": 48,
        "sub_bottom_margin": 340,
        "voice": "zh-CN-YunxiNeural",
        "rate": "+10%",
        "tone": "认知跃迁、商业启示、沉稳专业、社交传播"
    }
}

PROFILES = {
    "xiaohongshu": {
        "series": "生活决策笔记",
        "audience": "关注消费选择、职场和工具效率的女性读者",
        "priority": {"消费": 4, "职场": 4, "科技": 1, "商业": 1},
    },
    "douyin": {
        "series": "一分钟商业快讯",
        "audience": "希望快速知道发生了什么的泛商业观众",
        "priority": {"消费": 2, "职场": 2, "科技": 3, "商业": 4},
    },
    "bilibili": {
        "series": "商业与科技拆解",
        "audience": "关注技术背景、行业机制和信息边界的观众",
        "priority": {"消费": 1, "职场": 2, "科技": 4, "商业": 4},
    },
}

CREATOR_URLS = {
    "douyin": "https://creator.douyin.com/creator-micro/content/upload",
    "xiaohongshu": "https://creator.xiaohongshu.com/creator/post",
    "bilibili": "https://member.bilibili.com/platform/upload/video/frame",
    "wechat_channels": "https://channels.weixin.qq.com/platform/post/create"
}
