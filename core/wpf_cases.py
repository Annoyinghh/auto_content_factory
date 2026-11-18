"""Case-library operations for the native workbench."""

import datetime as dt
import os
from pathlib import Path

from core.copywriting import (
    STYLES,
    _learn_pending,
    _library,
    add_case,
    analyze_case,
    collect_topic_cases,
    last_hotspot_refresh,
    list_cases,
    queue_case_excerpt,
)
from core.local_ai import installed_models, model_for, start_portable_ai
from core.workflow import _lock


def _local_case_model(*, prefer_design=False):
    models = installed_models(local_only=True)
    if not models and os.getenv("ACF_STUDIO_ROOT"):
        start_portable_ai(wait=True)
        models = installed_models(local_only=True)
    roles = ("design", "writing") if prefer_design else ("writing", "design")
    for role in roles:
        name = model_for(role)
        if name in models:
            return name
    raise RuntimeError("没有可用的本机模型，请启动 Ollama 并安装本机写作模型。")


def _case_values(payload, *fields):
    values = [payload.get(field, "") for field in fields]
    if any(not isinstance(value, str) for value in values):
        raise ValueError("请填写有效的案例资料。")
    return values


def cases_stage(stage, day, output_dir, platform=None, payload=None, *, now=None):
    if stage == "cases_video":
        from core.video_styles import note_styles, video_styles

        return {target: video_styles(target) for target in ("douyin", "bilibili")} | {"xiaohongshu_notes": note_styles()}
    if stage == "cases_learn":
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        empty = {"saved": [], "error": ""}
        with _lock(root / "writing-refresh.sqlite3"):
            with _library(root) as db:
                if db.execute("SELECT 1 FROM pending_cases LIMIT 1").fetchone() is None:
                    return empty
            stamp = (now or dt.datetime.now().astimezone()).timestamp()
            return _learn_pending(root, stamp, _local_case_model(prefer_design=True)) or empty
    if platform not in STYLES:
        raise ValueError("请选择小红书、抖音或 B站。")
    if stage == "cases_list":
        return {"cases": list_cases(output_dir, platform), "last_refresh": last_hotspot_refresh(output_dir, platform)}
    if not isinstance(payload, dict):
        raise ValueError("请填写有效的案例资料。")
    if stage == "cases_add":
        add_case(output_dir, platform, *_case_values(payload, "title", "url", "evidence", "keywords", "notes"))
        return True
    if stage == "cases_queue":
        queue_case_excerpt(output_dir, platform, *_case_values(payload, "title", "url", "evidence", "keywords", "excerpt"))
        return True
    if stage == "cases_analyze":
        excerpt, = _case_values(payload, "excerpt")
        if not 20 <= len(excerpt.strip()) <= 6000:
            raise ValueError("请粘贴 20–6000 字的参考片段。")
        return analyze_case(platform, excerpt, model=_local_case_model())
    if stage == "cases_collect":
        topic, url = _case_values(payload, "topic", "url")
        return collect_topic_cases(output_dir, platform,
            {"topic": topic, "url": url, "interactive": payload.get('interactive') is True},
            model=_local_case_model(), photo_notes=platform == 'xiaohongshu')
    raise ValueError("未知案例库操作。")
