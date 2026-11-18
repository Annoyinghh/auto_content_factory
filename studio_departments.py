"""Collection, trends and release views for the Windows workbench."""

import datetime as dt
import os
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from core.accounts import account_hint
from core.browser_post import BROWSER_POST_PLATFORMS
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


def prepare_intake_view(snapshot):
    matched = match_hotspots(snapshot)
    if snapshot.get("checked") and not any(row.get("article") for rows in matched.values() for row in rows):
        checked = dt.datetime.fromtimestamp(snapshot["checked"], dt.timezone.utc)
        matched = match_hotspots(snapshot, checked)
    return snapshot, matched


class IntakeDesk:
    def __init__(self, collection, trends, names, start_editorial, open_writing):
        self.snapshot = {"articles": [], "ranks": {}, "checked": None, "errors": []}
        self.matched = {}
        self.article_links = {}
        self.platform = "douyin"
        self.query = ""
        self.visible = []
        self.platform_buttons = {}
        header = heading(collection, "采集库", "查看已入库的报道和原文时间。")
        self.use_article = ttk.Button(header, text="用此选题创作", style="Accent.TButton", state="disabled",
                                      command=lambda: start_editorial({"kind": "article", "id": self.articles.selection()[0]}))
        self.use_article.pack(side="right")
        self.collection_date = ttk.Label(collection, text="正在读取本地采集库…", style="Muted.TLabel")
        self.collection_date.pack(anchor="w", pady=(4, 17))
        tk.Frame(collection, height=1, background=LINE).pack(fill="x")
        self.articles = _table(collection, [("title", "", 450, True), ("source", "", 130, False),
                                            ("published", "", 68, False)], headings=False)
        self.articles.column("published", anchor="center")
        self.articles.tag_configure("older", background=PAGE, foreground=MUTED, font=(FONT, 9, "bold"))
        self.articles.tag_configure("alternate", background="#eeeff6")
        self.articles.bind("<Double-1>", lambda event: self.open_article())
        self.articles.bind("<Return>", lambda event: self.open_article())
        self.articles.bind("<<TreeviewSelect>>", lambda event: self.use_article.configure(
            state="normal" if self.articles.selection() and self.articles.selection()[0] in self.article_links else "disabled"))

        heading(trends, "平台热点", "选一个热点，再展开相关报道、补充素材和指定讲述重点。")
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
        self.note_count = ttk.Combobox(controls, values=("2 版", "3 版"), state="readonly", width=5)
        self.note_count.current(0)
        self.write = ttk.Button(controls, text="用热点写文案", state="disabled",
                                command=lambda: open_writing(self.platform, self.selected_topics()[0],
                                    int(self.note_count.get()[0]) if self.platform == "xiaohongshu" else None))
        self.write.pack(side="right", padx=(0, 8))
        ttk.Button(controls, text="榜单来源", command=self.open_board).pack(side="right", padx=(0, 8))
        self.trends = _table(trends, [("rank", "榜单排名", 76, False), ("title", "热点", 430, True),
                                     ("heat", "热度", 86, False), ("article", "报道匹配", 122, False)])
        self.trends.bind("<<TreeviewSelect>>", lambda event: self.update_selection())
        self.trends.bind("<Double-1>", lambda event: self.open_hotspot())
        self.trends.bind("<Return>", lambda event: self.open_hotspot())

    def update(self, snapshot, matched):
        self.snapshot = snapshot
        self.matched = matched
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
        self.write.configure(state="normal" if count == 1 else "disabled",
                             text="写小红书文案" if self.platform == "xiaohongshu" else "用热点写文案")
        if self.platform == "xiaohongshu":
            if not self.note_count.winfo_manager():
                self.note_count.pack(side="right", padx=(0, 6), before=self.write)
        else:
            self.note_count.pack_forget()

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
    def __init__(self, parent, names, publishers, account_settings, reload, hold, submit, browser_post, on_day,
                 share_review):
        self.names = names
        self.publishers = publishers
        self.rows = {}
        self.previous_review = None
        self.douyin_profiles = {}
        self.hold = hold
        self.on_day = on_day
        title = heading(parent, "发布中心", "选中已审成片，打开投稿页并上传。请在投稿页核对账号、成片和文案后发布。")
        self.fill = ttk.Button(title, text="填入投稿页", style="Accent.TButton", command=browser_post, state="disabled")
        self.fill.pack(side="right")
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
        self.summary = ttk.Label(buttons, style="Muted.TLabel")
        self.summary.pack(side="left")
        self.submit = ttk.Button(buttons, text="开放平台投稿", command=submit)
        self.submit.pack(side="right")
        ttk.Button(buttons, text="账号与作品", command=account_settings).pack(side="right", padx=(0, 6))
        ttk.Button(buttons, text="刷新队列", command=reload).pack(side="right", padx=(0, 6))
        selection = ttk.Frame(parent)
        selection.pack(fill="x", pady=(8, 0))
        self.play = ttk.Button(selection, text="播放成片", command=self.play_selected)
        self.play.pack(side="left")
        self.flag = ttk.Button(selection, text="暂缓这版", command=self.flag_selected)
        self.flag.pack(side="left", padx=6)
        self.share = ttk.Button(selection, text="送团队审片", command=share_review, state="disabled")
        self.share.pack(side="left")
        self.douyin_account = ttk.Combobox(selection, width=15, state="readonly")
        self.douyin_account.pack(side="right")
        ttk.Label(selection, text="抖音投稿账号", style="Muted.TLabel").pack(side="right", padx=(6, 8))
        self.douyin_account.bind("<<ComboboxSelected>>", self.choose_douyin_account)
        self.review_link = ttk.Frame(parent)
        self.review_hint = ttk.Label(self.review_link, style="Muted.TLabel", wraplength=440)
        self.review_hint.pack(side="left", fill="x", expand=True)
        ttk.Button(self.review_link, text="取消关联", command=lambda: self.set_review_base(None)).pack(side="right")
        self.operations_link = ttk.Frame(parent)
        self.operations_hint = ttk.Label(self.operations_link, style="Muted.TLabel", wraplength=460)
        self.operations_hint.pack(side="left", fill="x", expand=True)
        self.unlink_operations = ttk.Button(self.operations_link, text="解除投稿任务")
        self.unlink_operations.pack(side="right")
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
            available = bool(video and Path(video).is_file())
            editable = available and not receipt and not uncertain
            current = item.get("edit_revision") == item.get("package_revision")
            browser_ready = editable and current and not held and platform in BROWSER_POST_PLATFORMS
            if receipt:
                state = "已提交审核"
            elif uncertain:
                state = "待核对 · 不重发"
            elif not available:
                state = "无可用成片"
            elif not current:
                state = "草稿已更新"
            elif held:
                state = "已暂缓"
            elif platform not in self.publishers:
                state = "创作者后台投稿"
            elif not identity:
                state = "可填稿"
            else:
                state = "可发布"
            self.rows[platform] = {"title": package.get("title", item.get("story", {}).get("title", "—")),
                                   "account": account if identity else ("工作台内登录" if platform == "douyin" else "浏览器登录账号"),
                                   "identity": identity, "video": video, "state": state,
                                   "editable": editable, "held": held, "browser_ready": browser_ready,
                                   "review_ready": available and current and not held}
        douyin_label = self.douyin_account.get() or "请选择抖音账号"
        _replace_rows(self.tree, ((key, (self.names[key], row["title"],
                                       douyin_label if key == "douyin" else row["account"], row["state"]))
                                  for key, row in self.rows.items()))
        count = len(self.eligible())
        fillable = sum(row["browser_ready"] for row in self.rows.values())
        self.summary.configure(text=f"{day}  ·  {fillable} 条可填稿")
        self.submit.configure(text=f"开放平台投稿（{count}）", state="normal" if count and not busy else "disabled")
        self.select()

    def eligible(self):
        return {key: row for key, row in self.rows.items() if row["state"] == "可发布"}

    def set_douyin_profiles(self, profiles):
        selected = self.selected_douyin_profile() if len(self.douyin_profiles) > 1 else None
        self.douyin_profiles = {label: key for key, label in profiles.items()}
        self.douyin_account.configure(values=list(self.douyin_profiles))
        self.douyin_account.set(profiles.get(selected, next(iter(profiles.values())) if len(profiles) == 1 else ""))

    def selected_douyin_profile(self):
        return self.douyin_profiles.get(self.douyin_account.get())

    def choose_douyin_account(self, _event=None):
        if self.tree.exists("douyin"):
            self.tree.set("douyin", "account", self.douyin_account.get())
        self.select()

    def selected_browser_post(self):
        selected = self.tree.selection()
        if not self.busy and selected:
            key = selected[0]
            row = self.rows.get(key, {})
            if row.get("browser_ready") and (key != "douyin" or self.selected_douyin_profile()):
                return key, row["video"]
        return None

    def selected_team_review(self):
        if self.busy:
            return None
        selected = next(iter(self.tree.selection()), None)
        if self.previous_review and selected != self.previous_review["platform"]:
            return None
        row = self.rows.get(selected, {})
        return (selected, row["video"]) if row.get("review_ready") else None

    def set_review_base(self, review):
        self.previous_review = review
        if review:
            platform = self.names.get(review["platform"], review["platform"])
            title = review["title"]
            title = title if len(title) <= 28 else title[:28] + "…"
            self.review_hint.configure(text=f"续审《{title}》  ·  请选择 {platform} 的新版成片")
            self.review_link.pack(before=self.tree.master, fill="x", pady=(8, 0))
        else:
            self.review_link.pack_forget()
        self.share.configure(text="送新版审片" if review else "送团队审片")
        self.select()

    def set_operations_task(self, publication, clear=None):
        if not publication:
            self.operations_link.pack_forget()
            return
        title = publication["title"]
        title = title if len(title) <= 25 else title[:25] + "…"
        account = publication["account_label"]
        self.operations_hint.configure(text=f"团队投稿《{title}》  ·  账号 {account}")
        self.unlink_operations.configure(command=clear)
        self.operations_link.pack(before=self.tree.master, fill="x", pady=(8, 0))

    def select(self):
        selected = self.tree.selection()
        row = self.rows.get(selected[0], {}) if selected else {}
        self.play.configure(state="normal" if row.get("video") and Path(row["video"]).is_file() else "disabled")
        self.flag.configure(text="恢复发布资格" if row.get("held") else "暂缓这版",
                            state="normal" if row.get("editable") and not self.busy else "disabled")
        self.share.configure(state="normal" if self.selected_team_review() else "disabled")
        self.fill.configure(
            text="在工作台投稿" if selected and selected[0] == "douyin" else "填入投稿页",
            state="normal" if self.selected_browser_post() else "disabled",
        )

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
