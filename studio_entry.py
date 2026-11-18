"""Dispatch Windows worker and reader modes without importing the workbench."""

import sys


def run_command():
    if any(mode in sys.argv for mode in ("--worker", "--daily")):
        from studio_preferences import apply_export_preferences

        apply_export_preferences()
    if len(sys.argv) == 3 and sys.argv[1] == "--reader":
        from studio_reader import reader_main

        return reader_main(sys.argv[2])
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        from core.desktop_jobs import worker_main

        return worker_main(*sys.argv[2:])
    if "--intake" not in sys.argv and "--daily" not in sys.argv:
        return None

    import datetime as dt
    import logging

    from config import DATA_DIR, OUTPUT_DIR, PROFILES
    from core.desktop_jobs import run_worker
    from core.workflow import _lock, run_hot_daily, run_stage

    if "--intake" in sys.argv:
        logging.basicConfig(filename=DATA_DIR / "collection.log", encoding="utf-8", level=logging.INFO)
        try:
            run_stage("intake", dt.date.today())
        except Exception:
            logging.exception("后台采集未完成")
            return 1
        return 0

    logging.basicConfig(filename=DATA_DIR / "daily.log", encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        with _lock(OUTPUT_DIR / "daily-compilation.sqlite3"):
            edition, errors = run_hot_daily(progress=logging.info, invoke=run_worker)
    except Exception:
        logging.exception("今日制作启动失败")
        return 1
    ready = sum(item["status"] == "ready" for item in edition["items"].values())
    for error in [*edition["feed_errors"], *errors]:
        logging.warning("%s", error)
    for platform, item in edition["items"].items():
        if item["status"] == "failed":
            logging.warning("%s · %s", platform, item["error"])
    logging.info("成片 %s / %s", ready, len(PROFILES))
    return 0 if ready and not errors else 1


def main():
    result = run_command()
    if result is not None:
        return result

    from desktop import main as show_workbench

    return show_workbench()


if __name__ == "__main__":
    sys.exit(main())
