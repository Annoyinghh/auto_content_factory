"""Import today's account metrics from a local, user supplied CSV."""

import csv
import datetime as dt
import io
import json
import re
import sqlite3
from pathlib import Path

from core.workflow import _lock, write_json


COLUMNS = ("平台", "账号", "日期", "发布", "播放或阅读", "点赞", "评论", "新增关注")
MAX_BYTES = 256 * 1024


def write_template(path):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as output:
        csv.writer(output).writerow(COLUMNS)


def _metrics_path(data_dir):
    return Path(data_dir) / "accounts" / "daily-metrics.json"


def read_metrics(data_dir, *, day=None):
    day = day or dt.date.today()
    try:
        payload = json.loads(_metrics_path(data_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict) or "date" not in payload:
        raise ValueError("本机账号数据文件无法读取。")
    if payload["date"] != day.isoformat():
        return {}
    rows = payload.get("rows")
    if not isinstance(rows, dict) or any(not isinstance(row, list) or len(row) != 5
                                        or any(value is not None and type(value) is not int for value in row)
                                        for row in rows.values()):
        raise ValueError("本机账号数据文件无法读取。")
    return {key: tuple(row) for key, row in rows.items()}


def _read_csv(source, accounts, day):
    with Path(source).open("rb") as csv_file:
        data = csv_file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("账号 CSV 不应超过 256 KB。")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("请用 CSV UTF-8 格式保存文件。") from error
    reader = csv.reader(io.StringIO(content, newline=""), strict=True)
    records = {}
    try:
        if tuple(next(reader, ())) != COLUMNS:
            raise ValueError("CSV 表头不匹配，请使用工作台提供的模板。")
        for row in reader:
            if not row:
                continue
            location = f"第 {reader.line_num} 行"
            if len(row) != len(COLUMNS):
                raise ValueError(f"{location}：需要 {len(COLUMNS)} 列。")
            account = accounts.get(tuple(row[:2]))
            if not account:
                raise ValueError(f"{location}：平台或账号与本机列表不匹配，或存在重名账号。")
            if account in records:
                raise ValueError(f"{location}：同一账号在文件中只能出现一次。")
            if row[2] != day.isoformat():
                raise ValueError(f"{location}：日期应为本机今天（{day}）。")
            values = []
            for column, raw in zip(COLUMNS[3:], row[3:]):
                value = raw.strip()
                pattern = r"[+-]?[0-9]{1,15}" if column == "新增关注" else r"[0-9]{1,15}"
                if value and not re.fullmatch(pattern, value):
                    raise ValueError(f"{location}：{column}应为整数或留空；只有净增关注可为负数。")
                values.append(int(value) if value else None)
            records[account] = values
    except csv.Error as error:
        raise ValueError(f"第 {reader.line_num} 行：CSV 格式无法读取。") from error
    if not records:
        raise ValueError("请至少填写一个本机账号的今日数据。")
    return records


def import_metrics(data_dir, source, accounts, *, day=None):
    day = day or dt.date.today()
    records = _read_csv(source, accounts, day)
    path = _metrics_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _lock(path.with_suffix(".sqlite3"), timeout=5):
            rows = read_metrics(data_dir, day=day)
            rows.update(records)
            write_json(path, {"date": day.isoformat(), "rows": rows})
    except sqlite3.Error as error:
        raise RuntimeError("无法锁定本机账号数据，请稍后重试。") from error
    return len(records)
