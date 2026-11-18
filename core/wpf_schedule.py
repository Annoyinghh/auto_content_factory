"""Windows daily-compilation operations for the native workbench."""

import locale
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from core.desktop_jobs import run_worker
from core.workflow import _lock, run_hot_daily


TASK_NAME = "AutoContentFactoryDaily"
START_TIME = "08:30"
LAUNCHER = "AutoContentFactory-Studio.exe"


def _task_command(*args):
    return subprocess.run(
        ["schtasks.exe", *args, "/TN", TASK_NAME],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=20,
    )


def _task_status():
    result = _task_command("/Query", "/XML")
    if result.returncode:
        return {"exists": False, "enabled": False}
    try:
        try:
            task = ElementTree.fromstring(result.stdout)
        except ElementTree.ParseError:
            # Redirected schtasks output can be multibyte despite its UTF-16 declaration.
            try:
                text = result.stdout.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = result.stdout.decode(locale.getencoding())
            task = ElementTree.fromstring(text)
    except (ElementTree.ParseError, UnicodeDecodeError) as error:
        raise RuntimeError("无法读取 Windows 每日制作任务。") from error
    enabled = task.findtext("{*}Settings/{*}Enabled", "true").strip().lower() not in {"false", "0"}
    return {"exists": True, "enabled": enabled}


def _launcher_action():
    studio_root = os.getenv("ACF_STUDIO_ROOT")
    if not studio_root:
        raise RuntimeError("请从桌面工作台开启每日制作。")
    launcher = Path(studio_root).absolute() / LAUNCHER
    if not launcher.is_file():
        raise RuntimeError("找不到每日制作使用的桌面启动程序。")
    return subprocess.list2cmdline([str(launcher), "--daily"])


def _set_task(enabled):
    before = _task_status()
    if (enabled and before["enabled"]) or (not enabled and not before["exists"]):
        return before
    if not enabled:
        result = _task_command("/Delete", "/F")
    elif before["exists"]:
        result = _task_command("/Change", "/ENABLE")
    else:
        result = _task_command("/Create", "/SC", "DAILY", "/ST", START_TIME, "/IT", "/TR", _launcher_action())
    if result.returncode:
        message = (result.stderr or result.stdout).decode(locale.getpreferredencoding(False), errors="replace").strip()
        raise RuntimeError(message or "Windows 每日制作任务未能更新。")
    return _task_status()


def schedule_stage(stage, day, output_dir, platform=None, payload=None, *, now=None):
    if stage == "schedule_status":
        return _task_status()
    root = Path(output_dir)
    if stage == "schedule_set":
        if not isinstance(payload, dict) or type(payload.get("enabled")) is not bool:
            raise ValueError("请选择开启或关闭每日制作。")
        root.mkdir(parents=True, exist_ok=True)
        with _lock(root / "daily-schedule.sqlite3"):
            return _set_task(payload["enabled"])
    if stage == "schedule_run_today":
        root.mkdir(parents=True, exist_ok=True)
        with _lock(root / "daily-compilation.sqlite3"):
            edition, errors = run_hot_daily(now=now, output_dir=root, invoke=run_worker)
        items = [item for item in edition["items"].values() if item.get("chapters")]
        return {"date": edition["date"], "ready": sum(bool(item.get("package")) for item in items),
                "total": len(items), "errors": errors}
    raise ValueError("未知的自动合辑操作。")
