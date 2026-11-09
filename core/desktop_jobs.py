"""Run each desktop operation in an isolated, short-lived process."""

import datetime as dt
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from core.workflow import run_stage, write_json


DEADLINES = {"collect": 150, "intake": 420, "supplement_hotspots": 300, "select_episode": 600,
             "editorial_sources": 200, "editorial_create": 30, "editorial_preview": 4200, "editorial_export": 7200,
             "media": 1200, "ai": 2100, "preview": 1800, "render": 10800, "publish": 900, "login": 90, "videos": 60,
             "copywriting": 1800, "analyze_case": 220, "import_media": 90, "preview_asset": 100}


def desktop_command():
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).resolve().parents[1] / "desktop.py"))
    return command


def run_worker(stage, day, output_dir, platform=None, payload=None, *, now=None):
    jobs = Path(output_dir).resolve() / ".jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job = jobs / uuid.uuid4().hex
    job.mkdir()
    request, result = job / "request.json", job / "result.json"
    try:
        if stage == "login":
            from core.accounts import protect_json
            payload = protect_json(payload)
        write_json(request, {
            "stage": stage, "day": str(day), "platform": platform, "payload": payload,
            "output_dir": str(jobs.parent), "now": now.isoformat() if now else None,
        })
        command = [*desktop_command(), "--worker", str(request), str(result)]
        timeout = DEADLINES.get(stage, 30)
        env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        try:
            if os.name == "nt":
                from core.windows_worker import run_in_job
                returncode = run_in_job(command, timeout, env)
            else:
                returncode = subprocess.run(command, timeout=timeout, env=env).returncode
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("此环节运行超时，其他环节可继续使用。") from error
        if not result.is_file():
            raise RuntimeError(f"此环节意外退出（{returncode}），其他环节可继续使用。")
        response = json.loads(result.read_text(encoding="utf-8"))
        if returncode:
            raise RuntimeError(response["error"])
        return response["value"]
    finally:
        request.unlink(missing_ok=True)
        result.unlink(missing_ok=True)
        result.with_suffix(".tmp").unlink(missing_ok=True)
        request.with_suffix(".tmp").unlink(missing_ok=True)
        job.rmdir()


def worker_main(request, result):
    command = json.loads(Path(request).read_text(encoding="utf-8"))
    stamp = command.pop("now")
    now = dt.datetime.fromisoformat(stamp) if stamp else None
    try:
        if command["stage"] == "login":
            from core.accounts import unprotect_json
            command["payload"] = unprotect_json(command["payload"])
        value = run_stage(**command, now=now)
    except Exception as error:
        write_json(Path(result), {"error": str(error) or type(error).__name__})
        return 1
    write_json(Path(result), {"value": value})
    return 0
