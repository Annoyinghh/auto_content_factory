"""Collection, trends and release views for the Windows workbench."""

import datetime as dt
import os
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from core.accounts import account_hint
from core.daily_news import story_key
from core.intake import has_report, match_hotspots
from core.workflow import edition_path, release_directory
from studio_shell import heading, panel
from studio_theme import FONT, LINE, MUTED, PAGE


def _table(parent, columns, *, headings=True):
    panel = ttk.Frame(parent)
    panel.pack(fill="both", expand=True, pady=(10, 0))
    tree = ttk.Treeview(panel, columns=[key for key, *_ in columns], show="headings" if headings else "",
                        style="Treeview" if headings else "Feed.Treeview", selectmode="browse")
    for key, label, width, flexible in columns:
        tree.heading(key, text=label)
        tree.column(key, width=width, stretch=flexible, anchor="w")
    scrollbar = ttk.Scrollbar(panel, command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    return tree


def _replace_rows(tree, rows):
    selected = tree.selection()
    offset = tree.yview()[0] if tree.get_children() else 0
    tree.delete(*tree.get_children())
    for key, values, *tags in rows:
        tree.insert("", "end", iid=key, values=values, tags=tags)
    retained = [key for key in selected if tree.exists(key)]
    if retained:
        tree.selection_set(retained)
    tree.yview_moveto(offset)


def _stamp(timestamp):
    return dt.datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M") if timestamp else "—"


def _day_label(day):
    weekday = "一二三四五六日"[day.weekday()]
    return f"{day.month} 月 {day.day} 日  ·  星期{weekday}"


class IntakeDesk:
    def __init__(self, collection, trends, names, start_editorial):
        self.snapshot = {"articles": [], "ranks": {}, "checked": None, "errors": []}
        self.article_links = {}
        self.platform = "douyin"
        self.query = ""
        self.visible = []
        self.platform_buttons = {}
        header = heading(collection, "采集库", "查看已入库的报道和原文时间。")
        self.use_article = ttk.Button(header, text="用此选题创作", style="Accent.TButton", state="disabled",
                                      command=lambda: start_editorial({"kind": "article", "id": self.articles.selection()[0]}))
        self.use_article.pack(side="right")
        self.collection_date = ttk.Label(collection, style="Muted.TLabel")
        self.collection_date.pack(anchor="w", pady=(4, 17))
        tk.Frame(collection, height=1, background=LINE).pack(fill="x")
        self.articles = _table(collection, [("title", "", 450, True), ("source", "", 130, False),
                                            ("published", "", 68, False)], headings=False)
        self.articles.column("published", anchor="center")
        self.articles.tag_configure("older", background=PAGE, foreground=MUTED, font=(FONT, 9, "bold"))
        self.articles.tag_configure("alternate", background="#101b25")
        self.articles.bind("<Double-1>", lambda event: self.open_article())
        self.articles.bind("<Return>", lambda event: self.open_article())
        self.articles.bind("<<TreeviewSelect>>", lambda event: self.use_article.configure(
            state="normal" if self.articles.selection() and self.articles.selection()[0] in self.article_links else "disabled"))

        heading(trends, "平台热点", "选择一个热点，在 AI 创作里挑选相关报道。")
        self.trend_status = ttk.Label(trends, style="Muted.TLabel", wraplength=700)
        self.trend_status.pack(anchor="w", pady=(4, 15))
        controls = ttk.Frame(trends)
        controls.pack(fill="x")
        for key, label in names.items():
            button = ttk.Button(controls, text=label, style="Segment.TButton",
                                command=lambda selected=key: self.show_trends(selected))
            button.pack(side="left", padx=(0, 3))
            self.platform_buttons[key] = button
        self.produce = ttk.Button(controls, text="用此选题创作", style="Accent.TButton", state="disabled",
                                  command=lambda: start_editorial({"kind": "hotspot", "platform": self.platform,
                                                                   "title": self.selected_topics()[0]}))
        self.produce.pack(side="right")
        ttk.Button(controls, text="榜单来源", command=self.open_board).pack(side="right", padx=(0, 8))
        self.trends = _table(trends, [("rank", "榜单排名", 76, False), ("title", "热点", 430, True),
                                     ("heat", "热度", 86, False), ("article", "报道匹配", 122, False)])
        self.trends.bind("<<TreeviewSelect>>", lambda event: self.update_selection())
        self.trends.bind("<Double-1>", lambda event: self.open_hotspot())
        self.trends.bind("<Return>", lambda event: self.open_hotspot())

    def update(self, snapshot):
        self.snapshot = snapshot
        live_matched = match_hotspots(snapshot)
        has_live = any(row.get("article") for rows in live_matched.values() for row in rows)
        if not has_live and snapshot.get("checked"):
            checked_dt = dt.datetime.fromtimestamp(snapshot["checked"], dt.timezone.utc)
            self.matched = match_hotspots(snapshot, checked_dt)
        else:
            self.matched = live_matched
        today = dt.date.today()
        self.collection_date.configure(text=_day_label(today))
        rows = []
        day = today
        self.article_links = {}
        for i, row in enumerate(snapshot["articles"]):
            published = dt.datetime.fromisoformat(row["published"]).astimezone()
            if published.date() != day:
                day = published.date()
                rows.append((f"date:{day}", (f"  {_day_label(day)}", "", ""), "older"))
            key = story_key(row)
            self.article_links[key] = row["url"]
            tags = ("alternate",) if i % 2 else ()
            rows.append((key, (f"  {row['title']}", row["source"], published.strftime("%H:%M")), *tags))
        _replace_rows(self.articles, rows)
        self.show_trends()

    def show_trends(self, platform=None, *, query=None):
        retained = set(self.selected_topics()) if platform in (None, self.platform) and query is None else set()
        self.platform = platform or self.platform
        if query is not None:
            self.query = query.strip().casefold()
        platform = self.platform
        for key, button in self.platform_buttons.items():
            button.state(["selected" if key == platform else "!selected"])
        board = self.snapshot["ranks"].get(platform, {})
        rows = self.matched.get(platform, [])
        status = f"榜单时间 {_stamp(board.get('published'))}  ·  {board.get('source', '公开榜单')}" if rows else "暂无榜单记录"
        self.visible = [row for row in rows if self.query in row["title"].casefold()]
        matched = len({story_key(row["article"]) for row in self.visible if has_report(row["article"])})
        if rows:
            status += f"  ·  {len(self.visible)} 条热点  ·  {matched} 条有正文"
        if self.query:
            status += f"  ·  筛选「{self.query}」"
        self.trend_status.configure(text=status)
        _replace_rows(self.trends, ((str(i), (row["rank"], row["title"], row["heat"] or "—",
                                             "可用于合辑" if has_report(row["article"]) else
                                             "待补正文" if row["article"] else "暂无报道")) for i, row in enumerate(self.visible)))
        self.trends.selection_set([str(i) for i, row in enumerate(self.visible) if row["title"] in retained])
        self.update_selection()

    def selected_topics(self):
        return [self.visible[index]["title"] for index in sorted(map(int, self.trends.selection())) if index < len(self.visible)]

    def update_selection(self):
        count = len(self.trends.selection())
        self.produce.configure(state="normal" if count == 1 else "disabled")

    def open_article(self):
        selected = self.articles.selection()
        if selected and selected[0] in self.article_links:
            webbrowser.open(self.article_links[selected[0]])

    def open_hotspot(self):
        if selected := self.trends.focus():
            webbrowser.open(self.visible[int(selected)]["url"])

    def open_board(self):
        if url := self.snapshot["ranks"].get(self.platform, {}).get("url"):
            webbrowser.open(url)


class ReleaseDesk:
    def __init__(self, parent, names, publishers, account_settings, reload, hold, submit, on_day):
        self.names = names
        self.publishers = publishers
        self.rows = {}
        self.hold = hold
        self.on_day = on_day
        title = heading(parent, "发布中心", "预览成片、暂缓不满意的版本，并提交通过检查的作品。")
        self.submit = ttk.Button(title, text="一键发布", style="Accent.TButton", command=submit)
        self.submit.pack(side="right")
        frame, week = panel(parent, padding=(12, 9))
        frame.pack(fill="x", pady=(0, 12))
        ttk.Label(week, text="本周成片", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 7))
        self.calendar = ttk.Frame(week, style="Panel.TFrame")
        self.calendar.pack(fill="x")
        self.days = []
        for i in range(7):
            self.calendar.columnconfigure(i, weight=1, uniform="weekday")
            button = ttk.Button(self.calendar, style="Segment.TButton", width=8)
            button.grid(row=0, column=i, sticky="ew", padx=(0, 4 if i < 6 else 0))
            self.days.append(button)
        buttons = ttk.Frame(parent)
        buttons.pack(fill="x")
        self.play = ttk.Button(buttons, text="播放成片", command=self.play_selected)
        self.play.pack(side="left")
        self.flag = ttk.Button(buttons, text="暂缓这版", command=self.flag_selected)
        self.flag.pack(side="left", padx=8)
        ttk.Button(buttons, text="账号与作品", command=account_settings).pack(side="left")
        ttk.Button(buttons, text="刷新队列", command=reload).pack(side="left", padx=8)
        self.summary = ttk.Label(parent, style="Muted.TLabel", wraplength=760)
        self.summary.pack(anchor="w", pady=(8, 0))
        self.tree = _table(parent, [("platform", "平台", 82, False), ("title", "成片", 350, True),
                                    ("account", "投稿账号", 145, False), ("state", "发布状态", 144, False)])
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.select())
        self.tree.bind("<Double-1>", lambda event: self.play_selected())
        self.result = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.result, style="Muted.TLabel", wraplength=760).pack(anchor="w", pady=(6, 0))

    def show_week(self, editions, selected):
        selected = dt.date.fromisoformat(selected)
        monday = selected - dt.timedelta(days=selected.weekday())
        for offset, button in enumerate(self.days):
            day = monday + dt.timedelta(days=offset)
            items = editions.get(day, {}).get("items", {})
            count = sum(bool(item.get("package")) for item in items.values())
            detail = f"{count} 条成片" if count else "—"
            button.configure(text=f"{day:%m/%d}\n{detail}", command=lambda chosen=day: self.on_day(chosen.isoformat()))
            button.state(["selected" if day == selected else "!selected"])

    def update(self, edition, day, output_dir, busy):
        self.busy = busy
        self.rows = {}
        for platform, label in self.names.items():
            item = edition["items"].get(platform, {})
            package = item.get("package") or {}
            video = package.get("video")
            account, identity = account_hint(platform) if platform in self.publishers else ("—", None)
            directory = release_directory(edition_path(output_dir, day), platform, item)
            receipt = (directory / "submitted.txt").is_file()
            uncertain = (directory / "submitting.txt").exists()
            held = bool(video and item.get("held_video") == video)
            editable = bool(video and Path(video).is_file() and not receipt and not uncertain)
            if receipt:
                state = "已提交审核"
            elif uncertain:
                state = "待核对 · 不重发"
            elif not video or not Path(video).is_file():
                state = "无可用成片"
            elif item.get("edit_revision") != item.get("package_revision"):
                state = "草稿已更新"
            elif held:
                state = "已暂缓"
            elif platform not in self.publishers:
                state = "暂无直连接口"
            elif not identity:
                state = "需要开放平台授权"
            else:
                state = "可发布"
            self.rows[platform] = {"title": package.get("title", item.get("story", {}).get("title", "—")),
                                   "account": account, "identity": identity, "video": video, "state": state,
                                   "editable": editable, "held": held}
        _replace_rows(self.tree, ((key, (self.names[key], row["title"], row["account"], row["state"]))
                                  for key, row in self.rows.items()))
        count = len(self.eligible())
        self.summary.configure(text=f"{day}  ·  {count} 条可发布  ·  平台收到投稿后进入审核")
        self.submit.configure(text=f"一键发布（{count}）", state="normal" if count and not busy else "disabled")
        self.select()

    def eligible(self):
        return {key: row for key, row in self.rows.items() if row["state"] == "可发布"}

    def select(self):
        selected = self.tree.selection()
        row = self.rows.get(selected[0], {}) if selected else {}
        self.play.configure(state="normal" if row.get("video") and Path(row["video"]).is_file() else "disabled")
        self.flag.configure(text="恢复发布资格" if row.get("held") else "暂缓这版",
                            state="normal" if row.get("editable") and not self.busy else "disabled")

    def play_selected(self):
        if selected := self.tree.selection():
            video = self.rows[selected[0]]["video"]
            if video and Path(video).is_file():
                os.startfile(video)

    def flag_selected(self):
        if selected := self.tree.selection():
            key = selected[0]
            row = self.rows[key]
            if row["editable"] and not self.busy:
                self.hold(key, {"video": row["video"], "hold": not row["held"]})
