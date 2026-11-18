"""Platform catalogue and device-local account registrations."""

import json
import re
from uuid import uuid4

from core.douyin_profiles import douyin_profiles
from core.workflow import _lock, write_json


PLATFORMS = {
    "douyin": ("抖音", "短视频与直播"),
    "kuaishou": ("快手", "短视频与直播"),
    "wechat_channels": ("微信视频号", "短视频与直播"),
    "tiktok": ("TikTok", "短视频与直播"),
    "xiaohongshu": ("小红书", "图文与社区"),
    "wechat_official": ("微信公众号", "图文与社区"),
    "weibo": ("微博", "图文与社区"),
    "bilibili": ("B站", "中长视频"),
    "youtube": ("YouTube", "中长视频"),
}
PERSONAL_POST_PLATFORMS = ("douyin", "xiaohongshu")


def local_accounts(root):
    try:
        rows = json.loads((root / "accounts" / "matrix-local.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(rows, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", row["id"])
            or not isinstance(row.get("platform"), str) or row["platform"] not in PLATFORMS
            or row["platform"] == "douyin" or not isinstance(row.get("label"), str) or not row["label"].strip()
            for row in rows):
        raise ValueError("本机矩阵账号记录无法读取。")
    return rows


def personal_profiles(root, platform):
    if platform == "douyin":
        return douyin_profiles(root)
    if platform == "xiaohongshu":
        return {row["id"]: row["label"] for row in local_accounts(root) if row["platform"] == platform}
    raise ValueError("请选择抖音或小红书的个人账号。")


def save_local_account(root, platform, label, *, account_id=None):
    if not isinstance(platform, str) or platform not in PLATFORMS or platform == "douyin":
        raise ValueError("请选择可登记的平台。")
    label = label.strip() if isinstance(label, str) else ""
    if not 1 <= len(label) <= 60 or any(ord(char) < 32 for char in label):
        raise ValueError("账号备注请输入 1 到 60 个字符。")
    registry = root / "accounts" / "matrix-local.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    with _lock(registry.with_suffix(".sqlite3"), timeout=5):
        rows = local_accounts(root)
        selected = next((row for row in rows if row["id"] == account_id and row["platform"] == platform), None)
        if account_id is not None and selected is None:
            raise ValueError("找不到要修改的账号，请刷新清单。")
        if any(row["platform"] == platform and row["label"].casefold() == label.casefold()
               and row is not selected for row in rows):
            raise ValueError("该平台已有同名备注。")
        if selected is None:
            selected = {"id": uuid4().hex, "platform": platform, "label": label}
            rows.append(selected)
        else:
            selected["label"] = label
        write_json(registry, rows)
        return selected
