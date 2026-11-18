"""Names and separate WebView2 profile locations for personal Douyin accounts."""

import json
import re
from uuid import uuid4

from core.workflow import _lock, write_json


def douyin_profiles(root):
    try:
        records = json.loads((root / "browser" / "douyin-accounts.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"default": "抖音主账号"}
    if (not isinstance(records, dict) or "default" not in records or
            any(not re.fullmatch(r"default|[0-9a-f]{32}", key) or not isinstance(name, str) or not name.strip()
                for key, name in records.items()) or
            len({name.casefold() for name in records.values()}) != len(records)):
        raise ValueError("抖音账号列表无法读取。")
    return records


def profile_directory(root, profile_id):
    if profile_id not in douyin_profiles(root):
        raise ValueError("请重新选择有效的抖音账号。")
    browser = root / "browser"
    return browser / "douyin" if profile_id == "default" else browser / "douyin-accounts" / profile_id


def save_profile(root, label, *, profile_id=None):
    label = label.strip()
    if not 1 <= len(label) <= 30 or any(ord(char) < 32 for char in label):
        raise ValueError("账号备注请输入 1 到 30 个字符。")
    registry = root / "browser" / "douyin-accounts.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    with _lock(registry.with_suffix(".sqlite3"), timeout=5):
        records = douyin_profiles(root)
        if profile_id is not None and profile_id not in records:
            raise ValueError("请重新选择要修改的抖音账号。")
        if any(name.casefold() == label.casefold() and key != profile_id for key, name in records.items()):
            raise ValueError("这个账号备注已经存在。")
        key = profile_id or uuid4().hex
        records[key] = label
        write_json(registry, records)
    return key
