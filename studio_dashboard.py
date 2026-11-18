"""Local-day reports, observed hotspots, and known accounts."""

import datetime as dt
import hashlib
import tkinter as tk
import webbrowser
from collections import Counter
from tkinter import filedialog, messagebox, ttk

from config import DATA_DIR
from core.account_metrics import import_metrics, read_metrics, write_template
from core.daily_news import web_url
from core.workflow import edition_path, read_edition
from studio_shell import ScrollArea, panel
from studio_theme import ACCENT, FIELD, FONT, LINE, SURFACE


PLATFORM_COLORS = {"douyin": "#bbc4ff", "bilibili": "#dce2ff", "xiaohongshu": "#ffd0b4"}
PLATFORMS = ("douyin", "bilibili", "xiaohongshu")
NAVY = "#192853"
ALL = "全部"


class OperationsDashboard(ScrollArea):
    def __init__(self, parent, names, navigate, on_platform, *, data_dir=DATA_DIR):
        super().__init__(parent)
        self.names = names
        self.data_dir = data_dir
        self.articles = []
        self.links = {}
        self.accounts = []
        self.account_values = None
        self.day = ""

        header = ttk.Frame(self.body)
        header.pack(fill="x", pady=(0, 15))
        ttk.Label(header, text="今天的报道与热点", style="Title.TLabel").pack(side="left")
        self.date_label = ttk.Label(header, style="Muted.TLabel")
        self.date_label.pack(side="right")

        summary, tiles = panel(self.body, padding=0)
        summary.pack(fill="x", pady=(0, 12))
        self.numbers = []
        for column, (title, detail) in enumerate((("今日采集报道", "篇 · 按首次入库去重"),
                                                  ("今日热点", "条 · 各平台观测合计"))):
            tiles.columnconfigure(column, weight=2, uniform="today_totals")
            tile = ttk.Frame(tiles, style="Panel.TFrame", padding=(15, 9))
            tile.grid(row=0, column=column, sticky="nsew")
            ttk.Label(tile, text=title, style="PanelTitle.TLabel").pack(anchor="w")
            number = tk.Label(tile, text="0", bg=SURFACE, fg=ACCENT, font=("Segoe UI", 34, "bold"), padx=0, pady=0)
            number.pack(anchor="w")
            ttk.Label(tile, text=detail, style="PanelMuted.TLabel").pack(anchor="w")
            self.numbers.append(number)
        tk.Frame(tiles, bg=LINE, width=1).grid(row=0, column=1, sticky="nsw", pady=9)

        tiles.columnconfigure(2, weight=3, uniform="today_totals")
        block = tk.Frame(tiles, bg=NAVY, padx=15, pady=9)
        block.grid(row=0, column=2, sticky="nsew")
        block_header = tk.Frame(block, bg=NAVY)
        block_header.pack(fill="x")
        tk.Label(block_header, text="各平台今日热点", bg=NAVY, fg="white", padx=0,
                 font=(FONT, 12, "bold")).pack(side="left")
        self.rank_status = tk.Label(block_header, bg=NAVY, fg="#dbe1fc", padx=0, font=(FONT, 9))
        self.rank_status.pack(side="right")
        platforms = tk.Frame(block, bg=NAVY)
        platforms.pack(fill="x", pady=(5, 0))
        self.platform_counts = {}
        for column, platform in enumerate(PLATFORMS):
            platforms.columnconfigure(column, weight=1, uniform="platform_totals")
            cell = tk.Frame(platforms, bg=NAVY)
            cell.grid(row=0, column=column, sticky="ew")
            ttk.Button(cell, text=names[platform], command=lambda key=platform: on_platform(key)).pack(anchor="w")
            count = tk.Label(cell, text="0", bg=NAVY, fg="white", padx=0, font=("Segoe UI", 21, "bold"))
            count.pack(anchor="w")
            self.platform_counts[platform] = count

        reports = ttk.Frame(self.body)
        reports.pack(fill="x", pady=(0, 12))
        reports.columnconfigure(0, weight=1, uniform="today_reports")
        reports.columnconfigure(1, weight=2, uniform="today_reports")
        categories, category_body = panel(reports, padding=(9, 6))
        categories.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ttk.Label(category_body, text="按分类看报道", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 6))
        self.category_rows = ttk.Frame(category_body, style="Panel.TFrame")
        self.category_rows.pack(fill="x")
        self.category = tk.StringVar(value=ALL)
        self.category_buttons = {}

        report_panel, report_body = panel(reports, padding=(9, 6))
        report_panel.grid(row=0, column=1, sticky="nsew")
        report_header = ttk.Frame(report_body, style="Panel.TFrame")
        report_header.pack(fill="x", pady=(0, 6))
        ttk.Label(report_header, text="今日报道", style="PanelTitle.TLabel").pack(side="left")
        self.report_status = ttk.Label(report_header, style="PanelAccent.TLabel")
        self.report_status.pack(side="right")
        self.report_tree = self._table(report_body, (
            ("title", "标题", 240, 95, True), ("source", "来源", 95, 63, False), ("collected", "入库", 53, 53, False),
        ), 5)
        self.open_button = ttk.Button(report_body, text="打开原文 ↗", command=self.open_report, state="disabled")
        self.open_button.pack(anchor="e", pady=(6, 0))
        self.report_tree.bind("<<TreeviewSelect>>", lambda _: self.update_open_button())
        self.report_tree.bind("<Double-1>", lambda event: self.open_report() if self.report_tree.identify_row(event.y) else None)
        self.report_tree.bind("<Return>", lambda _: self.open_report())
        self.category.trace_add("write", lambda *_: self.show_reports())

        accounts, account_body = panel(self.body, padding=(9, 6))
        accounts.pack(fill="x")
        account_header = ttk.Frame(account_body, style="Panel.TFrame")
        account_header.pack(fill="x", pady=(0, 6))
        ttk.Label(account_header, text="我的账号数据", style="PanelTitle.TLabel").pack(side="left")
        ttk.Button(account_header, text="管理账号 ↗", command=lambda: navigate("accounts")).pack(side="right")
        ttk.Button(account_header, text="CSV 模板", command=self.save_metrics_template).pack(side="right", padx=(0, 6))
        self.import_button = ttk.Button(account_header, text="导入今日 CSV", command=self.import_account_metrics)
        self.import_button.pack(side="right", padx=(0, 6))
        self.account_tree = self._table(account_body, (
            ("name", "我的账号", 155, 95, True), ("platform", "平台", 58, 56, False),
            ("connection", "账号来源", 112, 102, False), ("posts", "发布", 56, 50, False),
            ("views", "播放／阅读", 85, 74, False), ("likes", "点赞", 55, 49, False),
            ("comments", "评论", 55, 49, False), ("followers", "净增关注", 76, 69, False),
        ), 1, selectmode="none")
        self.account_status = ttk.Label(account_body, style="PanelMuted.TLabel", justify="left")
        self.account_status.pack(anchor="w", pady=(7, 0))
        account_body.bind("<Configure>", lambda event: self.account_status.configure(wraplength=max(240, event.width - 22)))
        self.set_data({}, {}, {})
        self.schedule_rollover()

    @staticmethod
    def _table(parent, columns, height, *, selectmode="browse"):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill="x")
        table = ttk.Treeview(frame, columns=tuple(col[0] for col in columns), show="headings",
                             height=height, selectmode=selectmode)
        for key, title, width, minimum, stretch in columns:
            table.heading(key, text=title, anchor="w")
            table.column(key, width=width, minwidth=minimum, stretch=stretch, anchor="w")
        bar = ttk.Scrollbar(frame, command=table.yview)
        table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        table.pack(side="left", fill="x", expand=True)

        def wheel(event):
            first, last = table.yview()
            if (event.delta > 0 and first > 0) or (event.delta < 0 and last < 1):
                step = -1 if event.delta > 0 else 1
                table.yview_scroll(step * max(1, abs(event.delta) // 120), "units")
                return "break"

        table.bind("<MouseWheel>", wheel)
        return table

    def schedule_rollover(self):
        now = dt.datetime.now()
        if self.day != now.date().isoformat():
            self.set_today({})
            self.show_accounts()
        midnight = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time.min)
        self.after(max(1, int(1000 * (midnight.timestamp() - now.timestamp())) + 25), self.schedule_rollover)

    def set_data(self, snapshot, douyin_profiles, account_rows):
        self.set_today(snapshot.get("today", {}))
        accounts = []
        for platform in PLATFORMS:
            account = account_rows.get(platform, {})
            if platform == "douyin":
                accounts.extend((label, self.names[platform], "本机浏览器配置", self.account_key(platform, "browser", key))
                                for key, label in douyin_profiles.items())
            if account.get("identity"):
                accounts.append((account["account"], self.names[platform], "开放平台账号",
                                 self.account_key(platform, "authorized", account["identity"])))
        self.accounts = accounts
        self.show_accounts()

    @staticmethod
    def account_key(platform, connection, identity):
        return hashlib.sha256(f"{platform}/{connection}/{identity}".encode()).hexdigest()

    def importable_accounts(self):
        names = Counter((platform, label) for label, platform, _, _ in self.accounts)
        return {(platform, label): key for label, platform, _, key in self.accounts if names[(platform, label)] == 1}

    def show_accounts(self, imported=0):
        try:
            metrics = read_metrics(self.data_dir)
            error = False
        except (ValueError, OSError):
            metrics = {}
            error = True
        rows = []
        for label, platform, source, key in self.accounts:
            values = metrics.get(key, (None,) * 5)
            formatted = ["—" if value is None else f"{value:,}" for value in values]
            if values[-1] and values[-1] > 0:
                formatted[-1] = f"{values[-1]:+,}"
            rows.append((label, platform, source, *formatted))
        importable = self.importable_accounts()
        self.import_button.configure(state="normal" if importable else "disabled")
        if error:
            status = "本机账号数据无法读取。"
        elif imported:
            status = f"已导入 {imported} 个账号的今日数据。— 表示未填写。"
        elif not rows:
            status = "暂无可识别的本机账号。"
        elif any(key in metrics for _, _, _, key in self.accounts):
            status = "今日数据来自本机 CSV。— 表示未填写。"
        else:
            status = "尚未导入今日指标。可保存 CSV 模板，填写平台后台数据。"
        if len(importable) != len(self.accounts):
            status += " 同平台重名账号需先修改备注。"
        self.account_status.configure(text=status)
        if rows == self.account_values:
            return
        self.account_values = rows
        self.account_tree.delete(*self.account_tree.get_children())
        for row in rows:
            self.account_tree.insert("", "end", values=row)
        self.account_tree.configure(height=min(4, max(1, len(rows))))

    def save_metrics_template(self):
        parent = self.winfo_toplevel()
        destination = filedialog.asksaveasfilename(parent=parent, title="保存今日账号 CSV 模板", defaultextension=".csv",
                                                    initialfile="账号今日指标.csv", filetypes=[("CSV 文件", "*.csv")])
        if not destination:
            return
        try:
            write_template(destination)
        except OSError as error:
            messagebox.showerror("模板未保存", str(error), parent=parent)
            return
        messagebox.showinfo("填写今日账号数据", "每个账号填写一行，平台和账号须与面板一致。\n"
                            f"日期列设为文本，填写本机今天（{dt.date.today()}）。指标填写整数或留空。\n"
                            "‘新增关注’填写净变化，可为负数。用 CSV UTF-8 格式保存后导入。\n"
                            "同一账号重新导入会替换该账号当天的整行数据。", parent=parent)

    def import_account_metrics(self):
        parent = self.winfo_toplevel()
        source = filedialog.askopenfilename(parent=parent, title="导入今日账号 CSV", filetypes=[("CSV 文件", "*.csv")])
        if not source:
            return
        try:
            count = import_metrics(self.data_dir, source, self.importable_accounts())
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror("账号数据未导入", str(error), parent=parent)
            return
        self.show_accounts(imported=count)

    def set_today(self, today):
        date = dt.date.today().isoformat()
        if today.get("date") != date:
            today = {}
        articles, counts = today.get("articles", []), today.get("rank_counts", {})
        self.date_label.configure(text=f"{date}  ·  本机时区")
        self.numbers[0].configure(text=f"{len(articles):,}")
        self.numbers[1].configure(text=f"{sum(counts.get(key, 0) for key in PLATFORMS):,}")
        self.rank_status.configure(text="按平台去重" if counts else "暂无今日观测")
        for key, count in self.platform_counts.items():
            count.configure(text=f"{counts.get(key, 0):,}")
        if date == self.day and articles == self.articles:
            return
        self.day, self.articles = date, articles
        grouped = Counter(story["category"] for story in articles)
        self.category_buttons.clear()
        for child in self.category_rows.winfo_children():
            child.destroy()
        if not grouped:
            ttk.Label(self.category_rows, text="有今日报道后可按分类筛选。", style="PanelMuted.TLabel").pack(anchor="w", pady=5)
        for category, count in ([(ALL, len(articles)), *grouped.most_common()] if grouped else []):
            row = ttk.Frame(self.category_rows, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            button = ttk.Button(row, text=category, width=4, style="Segment.TButton",
                                command=lambda selected=category: self.category.set(selected))
            button.pack(side="left")
            self.category_buttons[category] = button
            ttk.Label(row, text=f"{count:,}", style="PanelMuted.TLabel", width=5, anchor="e").pack(side="right")
            bar = tk.Canvas(row, width=40, height=5, bg=FIELD, bd=0, highlightthickness=0)
            bar.pack(side="left", fill="x", expand=True, padx=(6, 0))
            bar.bind("<Configure>", lambda event, ratio=count / len(articles): self.paint_bar(event, ratio))
        if self.category.get() not in self.category_buttons:
            self.category.set(ALL)
        else:
            self.show_reports()

    @staticmethod
    def paint_bar(event, ratio):
        bar = event.widget
        bar.delete("all")
        bar.create_rectangle(0, 0, event.width * ratio, event.height, fill=ACCENT, outline="")

    def show_reports(self):
        category = self.category.get()
        for name, button in self.category_buttons.items():
            button.state(["selected" if name == category else "!selected"])
        self.report_tree.delete(*self.report_tree.get_children())
        self.links.clear()
        rows = [row for row in self.articles if category == ALL or row["category"] == category]
        for story in rows:
            key = self.report_tree.insert("", "end", values=(story["title"], story.get("source", ""),
                                          dt.datetime.fromtimestamp(story["collected_at"]).strftime("%H:%M")))
            if (url := story.get("url")) and web_url(url):
                self.links[key] = url
        self.report_status.configure(text=f"{category} · {len(rows):,} 篇" if rows else "今天还没有采集到报道")
        self.update_open_button()

    def update_open_button(self):
        key = next(iter(self.report_tree.selection()), None)
        self.open_button.configure(state="normal" if key in self.links else "disabled")

    def open_report(self):
        key = next(iter(self.report_tree.selection()), None)
        if url := self.links.get(key):
            webbrowser.open(url)


def recent_editions(output, days):
    return {day: read_edition(edition_path(output, day)) for day in days}
