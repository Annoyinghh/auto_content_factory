"""Operations dashboard and an interactive 3D projection of local counts."""

import datetime as dt
import math
import time
import tkinter as tk
from collections import Counter
from tkinter import ttk

from core.intake import RETENTION_DAYS, has_report
from core.workflow import edition_path, read_edition
from studio_shell import SCREENING, PhotoBanner, ScrollArea, panel
from studio_theme import ACCENT, AMBER, FIELD, FONT, LINE, MUTED, SURFACE, TEXT, icon


PLATFORM_COLORS = {"douyin": "#7fe5d9", "bilibili": "#7cb9f1", "xiaohongshu": "#efa6ae"}


def _blend(a, b, t):
    return "#" + "".join(f"{round(int(a[i:i+2], 16) * (1-t) + int(b[i:i+2], 16) * t):02x}" for i in (1, 3, 5))


class DataDepth(tk.Canvas):
    def __init__(self, parent, on_day):
        super().__init__(parent, bg=FIELD, height=277, bd=0, highlightthickness=0, takefocus=True,
                         highlightbackground=LINE, highlightcolor=ACCENT)
        self.on_day = on_day
        self.points = ()
        self.label = ""
        self.color = ACCENT
        self.motion = True
        self.gain = 1.0
        self.yaw = 0.51
        self.hover = None
        self.press = None
        self.timer = None
        self.bind("<Configure>", lambda event: self.draw())
        self.bind("<Unmap>", lambda event: self.stop())
        self.bind("<Motion>", self.point_at)
        self.bind("<Leave>", lambda event: self.highlight(None))
        self.bind("<ButtonPress-1>", self.start_drag)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<ButtonRelease-1>", self.end_drag)
        self.bind("<Left>", lambda event: self.turn(-0.08))
        self.bind("<Right>", lambda event: self.turn(0.08))

    def set_data(self, label, points, color):
        values = tuple(points)
        if (label, values, color) == (self.label, self.points, self.color):
            return
        self.label, self.points, self.color = label, values, color
        self.hover = None
        self.reveal()

    def stop(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        self.gain = 1.0

    def reveal(self, *, entrance=False):
        self.stop()
        final_angle = self.yaw
        start_angle = max(.06, final_angle - .18) if entrance else final_angle
        self.animate(start_angle, final_angle, grow=True)

    def animate(self, start_angle, final_angle, *, grow=False):
        self.stop()
        if not self.motion or not self.winfo_viewable():
            self.yaw = final_angle
            self.draw()
            return
        start = time.monotonic()

        def frame():
            if not self.winfo_viewable():
                self.timer = None
                self.yaw = final_angle
                self.gain = 1.0
                return
            phase = min(1, (time.monotonic() - start) / (0.62 if grow else 0.31))
            ease = 1 - (1 - phase) ** 3
            self.gain = ease if grow else 1.0
            self.yaw = start_angle + (final_angle - start_angle) * ease
            self.draw()
            self.timer = self.after(20, frame) if phase < 1 else None

        frame()

    def turn(self, delta):
        self.animate(self.yaw, max(0.06, min(1.04, self.yaw + delta)))

    def start_drag(self, event):
        self.stop()
        self.focus_set()
        self.press = (event.x, self.yaw, self.hover)

    def drag(self, event):
        if self.press:
            x, angle, _ = self.press
            self.yaw = max(0.06, min(1.04, angle + (event.x - x) / 240))
            self.draw()

    def end_drag(self, event):
        if self.press:
            x, _, index = self.press
            if index is not None and abs(event.x - x) < 5:
                self.on_day(self.points[index][0])
        self.press = None

    def point_at(self, event):
        if item := self.find_withtag("current"):
            for tag in self.gettags(item[0]):
                if tag.startswith("day:"):
                    self.highlight(int(tag[4:]))
                    return
        self.highlight(None)

    def highlight(self, index):
        if self.hover == index:
            return
        self.hover = index
        self.configure(cursor="hand2" if index is not None else "")
        self.draw()

    def draw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if not self.points or width < 160 or height < 120:
            return
        self.configure(takefocus=True)
        n = len(self.points)
        ceiling = max(value or 0 for _, value in self.points)
        magnitude = max(1, 10 ** max(0, int(math.log10(max(1, ceiling))) - 1))
        step = max(1, math.ceil(ceiling / (4 * magnitude)) * magnitude)
        origin, base = 50, height - 48
        dx = round(min(77, width * .128) * math.sin(self.yaw) + 13)
        dy = round(52 * math.cos(self.yaw) + 15)
        plot = width - origin - dx - 24
        usable_height = max(40, base - dy - 34)

        def project(x, level, depth):
            return origin + plot * x + dx * depth, base - level * usable_height - dy * depth

        def line(a, b, **kwargs):
            self.create_line(*project(*a), *project(*b), **kwargs)

        def face(vertices, **kwargs):
            self.create_polygon(*(coordinate for vertex in vertices for coordinate in project(*vertex)), **kwargs)

        face(((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)), fill="#1c292b", outline="#3c5353")
        for z in (0.25, 0.5, 0.75):
            line((0, 0, z), (1, 0, z), fill="#2b3c3d")
        for index in range(n + 1):
            line((index/n, 0, 0), (index/n, 0, 1), fill="#2b3c3d")
        for index in range(5):
            level = index/4
            line((0, level, 1), (1, level, 1), fill="#354245", dash=(2, 4))
            line((0, level, 0), (0, level, 1), fill="#293739", dash=(2, 4))
            x, y = project(0, level, 1)
            self.create_text(x - 8, y, text=str(index * step), anchor="e", fill=MUTED, font=("Segoe UI", 8))
        line((0, 0, 1), (0, 1, 1), fill="#3c5353")

        for index in range(n - 1, -1, -1):
            day, value = self.points[index]
            x = (index + 0.5) / n
            left, right = x - 0.23/n, x + 0.23/n
            front, back = 0.31, 0.72
            level = max(0.012, (value or 0) * self.gain / (4 * step))
            tags = (f"day:{index}",)
            hovered = index == self.hover
            if value:
                side = _blend(self.color, "#0b2334", 0.62 if not hovered else 0.46)
                body = _blend(self.color, "#0c3045", 0.24 if not hovered else 0.06)
                top = _blend(self.color, "#ffffff", 0.25)
                face(((left, 0, front), (right, 0, front), (right, level, front), (left, level, front)),
                     fill=body, outline=_blend(body, LINE, .32), tags=tags)
                face(((right, 0, front), (right, 0, back), (right, level, back), (right, level, front)), fill=side, tags=tags)
                face(((left, level, front), (right, level, front), (right, level, back), (left, level, back)), fill=top, tags=tags)
                if n < 12 and not hovered:
                    px, py = project(x, level, back)
                    self.create_text(px, py - 10, text=str(value), fill=self.color, font=("Segoe UI", 9, "bold"), tags=tags)
            else:
                line((left, 0, .45), (right, 0, .45), fill="#547081" if value == 0 else "#2d4657", width=3, tags=tags)
            if n <= 9 or index in {0, n-1} or (n-index-1) % 5 == 0:
                px, _ = project(x, 0, 0)
                self.create_text(px, base + 18, text=day.strftime("%m/%d"), fill=MUTED, font=("Segoe UI", 8), tags=tags)

        if not ceiling:
            self.create_text(width / 2, 61, text="当前区间暂无记录", fill=MUTED, font=(FONT, 10))
        if self.hover is not None:
            day, value = self.points[self.hover]
            count = f"{value} 条" if value is not None else "超出采集保留期"
            self.create_text(width - 12, 10, text=f"{day:%m/%d}  ·  {self.label}  {count}",
                             anchor="ne", fill=self.color, font=(FONT, 9, "bold"))


class OperationsDashboard(ScrollArea):
    def __init__(self, parent, names, navigate, on_day, on_platform, create):
        super().__init__(parent)
        self.names = names
        self.on_platform = on_platform
        self.on_day = on_day
        self.hero = PhotoBanner(self.body, SCREENING / "studio.png", label="热采放映室  /  今日播报",
                                title="今日节目单", subtitle="查看热点、成片与待审作品。",
                                action=create, button="＋  创建视频", frame=True)
        self.hero.pack(fill="x", pady=(0, 12))

        strip, tiles = panel(self.body, padding=(0, 3))
        strip.pack(fill="x", pady=(0, 14))
        self.stats = []
        for i, (title, dest, color) in enumerate((
            ("今日资讯", "collection", ACCENT), ("平台热榜", "trends", AMBER),
            ("今日成片", "clips", "#a8c6dd"), ("待发布成片", "release", "#dca99c"),
        )):
            column = i * 2
            tiles.columnconfigure(column, weight=1, uniform="metric")
            card = ttk.Frame(tiles, style="Panel.TFrame", padding=(10, 0))
            card.grid(row=0, column=column, sticky="nsew")
            if i < 3:
                tk.Frame(tiles, bg=LINE, width=1).grid(row=0, column=column+1, sticky="ns", pady=6)
            row = ttk.Frame(card, style="Panel.TFrame")
            row.pack(fill="x")
            glyph = ttk.Label(row, style="Panel.TLabel")
            glyph.picture = icon(self, dest, color)
            glyph.configure(image=glyph.picture)
            glyph.pack(side="left", padx=(0, 5))
            ttk.Button(row, text=title, style="Metric.TButton", command=lambda key=dest: navigate(key)).pack(side="left", fill="x", expand=True)
            number = ttk.Label(card, style="Number.TLabel")
            number.pack(anchor="w")
            caption = ttk.Label(card, style="PanelMuted.TLabel")
            caption.pack(anchor="w", pady=(0, 1))
            self.stats.append((number, caption))

        center = ttk.Frame(self.body)
        center.pack(fill="x", pady=(0, 16))
        center.columnconfigure(0, weight=3, uniform="main-panels")
        center.columnconfigure(1, weight=2, uniform="main-panels")
        border, chart = panel(center)
        border.grid(row=0, column=0, sticky="nsew", padx=(0, 13))
        bar = ttk.Frame(chart, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 12))
        ttk.Label(bar, text="内容趋势", style="PanelTitle.TLabel").pack(side="left")
        self.metric = tk.StringVar(value="资讯")
        self.period = tk.StringVar(value="近 7 天")
        for var, values, columns in ((self.period, ("近 7 天", "近 30 天"), 8),
                                      (self.metric, ("资讯", "成片"), 5)):
            menu = ttk.Combobox(bar, textvariable=var, values=values, width=columns, state="readonly")
            menu.pack(side="right", padx=(6, 0))
            menu.bind("<<ComboboxSelected>>", lambda event: self.update_chart())
        self.graph = DataDepth(chart, on_day)
        self.graph.pack(fill="x")
        self.bind("<Configure>", self.resize_chart, add="+")
        toolbar = ttk.Frame(chart, style="Panel.TFrame")
        toolbar.pack(fill="x", pady=(9, 0))
        self.graph_note = ttk.Label(toolbar, text="拖动旋转 · 悬停读数", style="PanelMuted.TLabel")
        self.graph_note.pack(side="left")
        ttk.Button(toolbar, text="视角", width=4, command=self.rotate).pack(side="right")
        self.motion_button = ttk.Button(toolbar, text="动效开", width=5, command=self.toggle_motion)
        self.motion_button.pack(side="right", padx=(0, 6))

        border, right = panel(center)
        border.grid(row=0, column=1, sticky="nsew")
        ttk.Label(right, text="平台运营", style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(right, text="榜单覆盖与可用报道", style="PanelMuted.TLabel").pack(anchor="w", pady=(4, 14))
        self.platform_cards = {}
        for key, name in names.items():
            tk.Frame(right, height=1, bg=LINE).pack(fill="x", pady=(2, 8))
            title = ttk.Frame(right, style="Panel.TFrame")
            title.pack(fill="x")
            tk.Label(title, text=name, bg=_blend(SURFACE, PLATFORM_COLORS[key], .14),
                     fg=PLATFORM_COLORS[key], font=(FONT, 9, "bold"), padx=8, pady=3).pack(side="left")
            ttk.Button(title, text="热榜 ↗", command=lambda platform=key: on_platform(platform)).pack(side="right")
            count = ttk.Label(right, style="Panel.TLabel")
            count.pack(anchor="w", pady=(5, 4))
            meter = tk.Canvas(right, bg="#343436", bd=0, height=4, highlightthickness=0)
            meter.pack(fill="x", pady=(0, 11))
            self.platform_cards[key] = (count, meter)
            meter.bind("<Configure>", lambda e, platform=key: self.paint_meter(platform))
        self.platform_values = {}
        self.stamp = ttk.Label(right, style="PanelMuted.TLabel")
        self.stamp.pack(anchor="w", pady=(3, 0))

        footer = ttk.Frame(self.body)
        footer.pack(fill="both", expand=True)
        footer.columnconfigure(0, weight=1, uniform="secondary")
        footer.columnconfigure(1, weight=1, uniform="secondary")
        self.activity = self.feed(footer, 0, "内容流水线", "剪辑库 ↗", lambda: navigate("clips"))
        self.hot = self.feed(footer, 1, "当前高位热点", "全部热点 ↗", lambda: navigate("trends"))
        self.articles, self.renders = Counter(), Counter()
        self.checked = None

    def resize_chart(self, event):
        compact = event.height < 640
        height = 205 if compact else 277
        hero_height = 185 if compact else 276
        if int(self.hero.cget("height")) != hero_height:
            self.hero.configure(height=hero_height)
        if int(self.graph.cget("height")) != height:
            self.graph.configure(height=height)

    @staticmethod
    def feed(parent, column, title, button, action):
        border, card = panel(parent)
        border.grid(row=0, column=column, sticky="nsew", padx=(0, 13) if column == 0 else 0)
        row = ttk.Frame(card, style="Panel.TFrame")
        row.pack(fill="x", pady=(0, 4))
        ttk.Label(row, text=title, style="PanelTitle.TLabel").pack(side="left")
        ttk.Button(row, text=button, command=action).pack(side="right")
        rows = ttk.Frame(card, style="Panel.TFrame")
        rows.pack(fill="both", expand=True)
        return rows

    @staticmethod
    def fill_feed(parent, records, empty):
        for child in parent.winfo_children():
            child.destroy()
        if not records:
            ttk.Label(parent, text=empty, style="PanelMuted.TLabel").pack(anchor="w", pady=14)
        for label, title in records:
            tk.Frame(parent, height=1, bg=LINE).pack(fill="x", pady=(8, 6))
            row = ttk.Frame(parent, style="Panel.TFrame")
            row.pack(fill="x")
            ttk.Label(row, text=label, style="PanelAccent.TLabel").pack(side="left", padx=(0, 10))
            ttk.Label(row, text=title, style="Panel.TLabel").pack(side="left", fill="x")

    def rotate(self):
        self.graph.turn(0.22 if self.graph.yaw < 0.84 else -0.66)

    def toggle_motion(self):
        self.graph.motion = not self.graph.motion
        self.motion_button.configure(text="动效开" if self.graph.motion else "动效关")
        self.graph.reveal()

    def paint_meter(self, platform):
        _, meter = self.platform_cards[platform]
        meter.delete("all")
        total, matched = self.platform_values.get(platform, (0, 0))
        if total and matched:
            meter.create_rectangle(0, 0, meter.winfo_width() * matched / total, 4,
                                   outline="", fill=PLATFORM_COLORS[platform])

    def update_chart(self):
        today = dt.date.today()
        period = 7 if self.period.get() == "近 7 天" else 30
        articles = self.metric.get() == "资讯"
        source = self.articles if articles else self.renders
        points = []
        for offset in range(period-1, -1, -1):
            day = today - dt.timedelta(days=offset)
            observed = not articles or (self.checked and offset < RETENTION_DAYS)
            points.append((day, source[day] if observed else None))
        self.graph.set_data(self.metric.get(), points, ACCENT if articles else "#82b8ff")
        self.graph_note.configure(text="拖动旋转 · 悬停读数" if not articles or period == 7 else "资讯仅保留 7 天 · 灰色为无记录")

    def set_data(self, snapshot, matches, editions, release_rows):
        today = dt.date.today()
        self.checked = snapshot.get("checked")
        self.articles = Counter(dt.datetime.fromisoformat(story["published"]).astimezone().date()
                                for story in snapshot["articles"])
        self.renders = Counter({day: sum(bool(item.get("package")) for item in edition["items"].values())
                                for day, edition in editions.items()})
        daily = editions.get(today, {}).get("items", {})
        boards = snapshot.get("ranks", {})
        total_ranks = sum(len(board.get("entries", ())) for board in boards.values())
        pending = sum(bool(row.get("video") and row.get("editable") and not row.get("held")) for row in release_rows.values())
        ready_platforms = sum(bool(board.get("entries")) for board in boards.values())
        for (number, caption), (value, note) in zip(self.stats, (
            (self.articles[today], "按报道发布时间"), (total_ranks, f"来自 {ready_platforms} 个平台"),
            (self.renders[today], f"今日 {len(daily)} 项选题"), (pending, "当前工作日期"),
        )):
            number.configure(text=f"{value:,}")
            caption.configure(text=note)
        for key, (count, _) in self.platform_cards.items():
            entries = boards.get(key, {}).get("entries", ())
            matched = sum(has_report(row.get("article")) for row in matches.get(key, ()))
            self.platform_values[key] = len(entries), matched
            count.configure(text=f"{len(entries)} 条热点   ·   {matched} 条有正文" if entries else "暂无榜单记录")
            self.paint_meter(key)
        latest_board = max((board.get("published", 0) for board in boards.values()), default=0)
        stamp = dt.datetime.fromtimestamp(latest_board).strftime("%m/%d  %H:%M") if latest_board else "—"
        self.stamp.configure(text=f"最近榜单   {stamp}")
        self.fill_feed(self.activity, [(self.names[key], item["story"]["title"]) for key, item in daily.items()],
                       "今日制作记录将在这里显示。")
        top = [(self.names[key], rows[0]["title"]) for key, board in boards.items() if key in self.names
               and (rows := board.get("entries"))]
        self.fill_feed(self.hot, top, "榜单入库后显示各平台首位热点。")
        self.update_chart()


def recent_editions(output, days):
    return {day: read_edition(edition_path(output, day)) for day in days}
