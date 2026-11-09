"""Native browsing surfaces for existing media, edits and account connections."""

import os
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk

from core.media_review import media_state
from studio_dashboard import PLATFORM_COLORS
from studio_shell import SCREENING, PhotoBanner, ScrollArea, heading, panel
from studio_theme import AMBER, FIELD, FONT, MUTED, PAGE, TEXT


@dataclass
class LibraryEntry:
    key: str
    title: str
    detail: str
    preview: str
    path: str
    previous: bool = False
    package: dict | None = None
    modified: float = 0
    asset_id: str = ""
    issue_id: str | None = None
    review: str = ""
    video_asset: bool = False


@lru_cache(maxsize=48)
def _thumbnail(path, modified, size):
    with Image.open(path) as image:
        image.thumbnail(size)
        return image.convert("RGB")


def thumbnail(path, size):
    return _thumbnail(str(path), Path(path).stat().st_mtime_ns, size)


class CoverStage(tk.Canvas):
    """Light the shelf from the work's own cover while keeping the cover legible."""

    def __init__(self, parent, entry, open_issue, *, selected=False, platform=None):
        super().__init__(parent, bg=PAGE if platform else FIELD, width=1, height=232 if platform else 154,
                         highlightthickness=0, bd=0)
        self.entry, self.selected, self.platform = entry, selected, platform
        self.render_size = None
        self.cover = None
        for path in (entry.preview, SCREENING / "viewfinder.png"):
            try:
                self.cover = thumbnail(path, (760, 540))
                break
            except (OSError, ValueError):
                continue
        self.backdrop = self.create_image(0, 0, anchor="nw")
        self.corner = self.create_polygon(0, 0, 0, 0, 0, 0, fill=AMBER, state="hidden")
        if platform:
            button = ttk.Button(self, text="打开刊物  ↗", style="Accent.TButton", command=open_issue)
            self.create_window(26, 211, anchor="sw", window=button, tags="action")
        elif entry.modified:
            self.configure(cursor="hand2")
            self.bind("<Button-1>", lambda event: open_issue())
        self.bind("<Configure>", self.paint)

    def paint(self, event):
        w, h = event.width, event.height
        if w < 80 or h < 80 or self.render_size == (w, h):
            return
        self.render_size = (w, h)
        feature = bool(self.platform)
        base = "#1c191a" if feature else FIELD
        matte = Image.new("RGB", (w, h), base)
        if self.cover:
            start = int(w * .42) if feature else 0
            glow = ImageOps.fit(self.cover, ((w-start)//4, h//4)).filter(ImageFilter.GaussianBlur(6))
            glow = glow.resize((w-start, h), Image.Resampling.BILINEAR)
            mask = Image.new("L", (w-start, 1), 57)
            if feature:
                mask.putdata([int(116 * min(1, x / (w * .34))) for x in range(w-start)])
            matte.paste(glow, (start, 0), mask.resize((w-start, h)))
            frame = ImageOps.contain(self.cover, (int(w * (.42 if feature else .89)), h-43 if feature else h-27))
            x = int(w * (.755 if feature else .5)) - frame.width//2
            y = (h-frame.height)//2 - 2
            draw = ImageDraw.Draw(matte)
            draw.rectangle((x-4, y+6, x+frame.width+11, y+frame.height+10), fill="#0f0e11")
            draw.rectangle((x, y+2, x+frame.width+5, y+frame.height+4), fill="#9c8b76")
            draw.line((x+1, y+frame.height+2, x+frame.width+4, y+frame.height+2), fill="#c3b5a1")
            draw.rectangle((x-5, y-1, x-1, y+frame.height), fill="#624d3e")
            matte.paste(frame, (x, y))
            draw.line((x, y, x, y+frame.height-1), fill="#8a7869")
        draw = ImageDraw.Draw(matte)
        edge = Image.new("L", (w, h))
        ImageDraw.Draw(edge).rounded_rectangle((0, 0, w-1, h-1), radius=13 if feature else 6, fill=255)
        draw.rounded_rectangle((0, 0, w-1, h-1), radius=13 if feature else 6, outline="#4c403a" if feature else "#3a3432")
        backdrop = Image.composite(matte, Image.new("RGB", (w, h), self.cget("background")), edge)
        self.picture = ImageTk.PhotoImage(backdrop, master=self)
        self.itemconfigure(self.backdrop, image=self.picture)
        self.coords(self.corner, w-30, 2, w-6, 2, w-3, 5, w-3, 29)
        self.set_selected(self.selected)
        if feature:
            self.draw_feature_copy(w)

    def draw_feature_copy(self, width):
        self.delete("copy")
        self.create_line(26, 34, 40, 34, fill=AMBER, width=2, tags="copy")
        self.create_text(49, 26, text="最新成片   /   本期刊物", fill=AMBER, anchor="nw",
                         font=(FONT, 9, "bold"), tags="copy")
        title = self.entry.title[:240]
        if len(self.entry.title) > len(title):
            title += "…"
        headline = self.create_text(25, 53, text=title, width=int(width * .49)-27,
                                    fill=TEXT, font=(FONT, 18, "bold"), anchor="nw", tags="copy")
        if self.bbox(headline)[3] > 149:
            self.itemconfigure(headline, font=(FONT, 15, "bold"))
            while len(title) > 8 and self.bbox(headline)[3] > 149:
                title = title[:-8].rstrip()
                self.itemconfigure(headline, text=title + "…")
        self.create_text(26, min(155, self.bbox(headline)[3]+8), text=f"{self.platform}   ·   {self.entry.detail}",
                         fill="#d8ccc1", anchor="nw", font=(FONT, 9), tags="copy")
        self.tag_raise("action")

    def set_selected(self, selected):
        self.selected = selected
        self.itemconfigure(self.corner, state="normal" if selected else "hidden")


class MediaLibrary(ScrollArea):
    def __init__(self, parent, names, open_editor, primary_action, *, clips=False, read_work=None, review_asset=None,
                 play_asset=None):
        super().__init__(parent)
        self.names, self.open_editor, self.clips = names, open_editor, clips
        self.rows = []
        self.read_work = read_work
        self.review_asset = review_asset
        self.play_asset = play_asset
        self.selected_video = None
        self.columns = 0
        self.limit = 18
        self.covers = []
        self.search_timer = None
        self.cards_timer = None
        title = "作品书架" if clips else "素材库"
        subtitle = "打开一本影像刊物，按章翻看成片。" if clips else "先看素材和来源，审核可用后可加入分镜。"
        header = heading(self.body, title, subtitle, kicker="热采放映室  /  作品收藏" if clips else None)
        ttk.Button(header, text="＋  创建视频" if clips else "＋  导入素材", style="Accent.TButton",
                   command=primary_action).pack(side="right")
        controls = ttk.Frame(self.body)
        controls.pack(fill="x", pady=(0, 15))
        self.platform = tk.StringVar(value="所有平台")
        select = ttk.Combobox(controls, state="readonly", values=("所有平台", *names.values()), textvariable=self.platform, width=12)
        select.pack(side="left")
        select.bind("<<ComboboxSelected>>", lambda event: self.filter_changed())
        self.review_filter = tk.StringVar(value="全部状态")
        if review_asset:
            filter_box = ttk.Combobox(controls, state="readonly", values=("全部状态", "待审核", "可用"),
                                      textvariable=self.review_filter, width=9)
            filter_box.pack(side="left", padx=(7, 0))
            filter_box.bind("<<ComboboxSelected>>", lambda event: self.filter_changed())
        ttk.Label(controls, text="名称筛选", style="Muted.TLabel").pack(side="left", padx=(13, 6))
        self.query = tk.StringVar()
        ttk.Entry(controls, textvariable=self.query, width=22).pack(side="left")
        self.query.trace_add("write", lambda *args: self.filter_changed())
        self.count = ttk.Label(controls, style="Muted.TLabel")
        self.count.pack(side="right")
        self.grid_body = ttk.Frame(self.body)
        self.grid_body.pack(fill="x")
        self.body.bind("<Configure>", self.resize_cards, add="+")
        self.empty = "当前日期暂无草稿或成片。" if clips else "当前日期暂无可用素材。"

    def filter_changed(self):
        self.limit = 18
        if self.search_timer is not None:
            self.after_cancel(self.search_timer)
        self.search_timer = self.after(140, self.apply_filter)

    def apply_filter(self):
        self.search_timer = None
        self.redraw()

    def resize_cards(self, event):
        columns = 3 if event.width >= 815 else 2
        if columns != self.columns:
            self.columns = columns
            self.redraw()

    def set_data(self, edition, day, release_rows=None):
        old_rows, old_day = self.rows, getattr(self, "day", None)
        self.rows = []
        self.day = day
        for key, item in edition.get("items", {}).items():
            if key not in self.names:
                continue
            if self.clips:
                state = (release_rows or {}).get(key, {}).get("state")
                self.add_clip(key, item, previous=False, state=state)
                for older in reversed(edition.get("previous_items", {}).get(key, ())):
                    self.add_clip(key, older, previous=True)
            else:
                for asset in item.get("media", ()):
                    state = media_state(item, asset)
                    if state == "removed":
                        continue
                    path = asset.get("path") or ""
                    kind = "视频" if asset.get("kind") == "video" else "照片"
                    detail = "本地导入" if asset.get("local") else (asset.get("license") or "授权素材")
                    self.rows.append(LibraryEntry(key, asset["title"], f"{kind} · {detail}", asset.get("preview") or "", path,
                                                  asset_id=asset["page_url"], issue_id=item.get("id"), review=state,
                                                  video_asset=kind == "视频"))
        if old_rows != self.rows or old_day != day:
            if old_day != day:
                self.limit = 18
            self.redraw()

    def add_clip(self, key, item, *, previous, state=None):
        package = item.get("package") or {}
        title = item.get("story", {}).get("title", "未命名选题")
        video = package.get("video") or ""
        duration = int(package.get("duration") or 0)
        status = "历史版本" if previous else (state or "待审片") if video else "草稿"
        detail = f"{status} · {duration//60:02d}:{duration%60:02d}" if video and duration else status
        cover = package.get("cover") or (str(Path(video).parent / "slide_00.png") if video else "")
        try:
            modified = Path(video).stat().st_mtime if video else 0
        except OSError:
            modified = 0
        self.rows.append(LibraryEntry(key, title, detail, cover, video, previous, package, modified))

    def redraw(self):
        if self.cards_timer is not None:
            self.after_cancel(self.cards_timer)
            self.cards_timer = None
        for widget in self.grid_body.winfo_children():
            widget.destroy()
        self.covers.clear()
        columns = self.columns or 2
        for column in range(3):
            self.grid_body.columnconfigure(column, weight=int(column < columns), uniform="library" if column < columns else "")
        platform = self.platform.get()
        query = self.query.get().strip().casefold()
        status = {"待审核": "pending", "可用": "approved"}.get(self.review_filter.get())
        rows = [row for row in self.rows if (platform == "所有平台" or self.names[row.key] == platform)
                and query in row.title.casefold() and (status is None or row.review == status)]
        if self.review_asset:
            rows.sort(key=lambda row: row.review != "pending")
        self.count.configure(text=f"{getattr(self, 'day', '—')}   ·   {len(rows)} 项")
        if not rows:
            if not self.clips and not self.rows:
                banner = PhotoBanner(self.grid_body, SCREENING / "contact-sheet.png", label="素材库  /  CONTACT SHEET",
                                     title="第一格留给你的素材", subtitle=self.empty, height=192)
                banner.grid(row=0, column=0, columnspan=columns, sticky="ew")
                return
            border, card = panel(self.grid_body, padding=27)
            border.grid(row=0, column=0, columnspan=columns, sticky="ew")
            ttk.Label(card, text="没有符合筛选的内容。" if self.rows else self.empty, style="PanelMuted.TLabel").pack(anchor="w")
        playable = [row for row in rows if row.modified and not row.previous]
        featured = max(playable, key=lambda row: row.modified, default=None) if self.clips else None
        if featured:
            self.featured(featured, columns)
        if self.clips and rows:
            rail = ttk.Frame(self.grid_body)
            rail.grid(row=int(bool(featured)), column=0, columnspan=columns, sticky="ew", pady=(0, 9))
            ttk.Label(rail, text="刊物索引", style="Muted.TLabel").pack(side="left", padx=(0, 10))
            tk.Frame(rail, bg="#57453a", height=1).pack(side="left", fill="x", expand=True)
        first_row = bool(featured) + self.clips
        stop = min(len(rows), self.limit)

        def add_row(start):
            self.cards_timer = None
            end = min(start + columns, stop)
            for index in range(start, end):
                self.card(rows[index], first_row + index // columns, index % columns, columns)
            if end < stop:
                self.cards_timer = self.after(20, lambda: add_row(end))
            elif stop < len(rows):
                ttk.Button(self.grid_body, text=f"显示更多 · 还有 {len(rows)-stop} 项", command=self.show_more).grid(
                    row=(stop + columns-1)//columns + first_row, column=0, columnspan=columns, pady=9)

        add_row(0)

    def show_more(self):
        self.limit += 18
        self.redraw()

    @staticmethod
    def artwork(parent, entry):
        size = (206, 120)
        thumb = tk.Label(parent, bg=FIELD, bd=0)
        tile = Image.new("RGB", size, FIELD)
        try:
            frame = thumbnail(entry.preview, size)
            tile.paste(frame, ((size[0]-frame.width)//2, (size[1]-frame.height)//2))
        except (OSError, ValueError):
            pass
        thumb.picture = ImageTk.PhotoImage(tile, master=thumb)
        thumb.configure(image=thumb.picture)
        return thumb

    def open_issue(self, row):
        self.selected_video = row.path
        self.read_work(row.package, row.title, self.names[row.key], row.preview)
        for cover in self.covers:
            cover.set_selected(cover.entry.path == row.path)

    def featured(self, entry, columns):
        feature = CoverStage(self.grid_body, entry, lambda: self.open_issue(entry), platform=self.names[entry.key],
                             selected=self.selected_video in (None, entry.path))
        feature.grid(row=0, column=0, columnspan=columns, sticky="ew", pady=(0, 15))
        self.covers.append(feature)

    def card(self, row, y, x, columns):
        key, title, detail, preview, path, previous = row.key, row.title, row.detail, row.preview, row.path, row.previous
        border, body = panel(self.grid_body, padding=(12, 12))
        border.grid(row=y, column=x, sticky="nsew", padx=(0, 11 if x < columns-1 else 0), pady=(0, 12))
        if self.clips:
            cover = CoverStage(body, row, lambda: self.open_issue(row),
                               selected=bool(row.path and row.path == self.selected_video))
            cover.pack(fill="x")
            self.covers.append(cover)
        else:
            self.artwork(body, row).pack(fill="x")
        name = ttk.Label(body, text=title, wraplength=210, style="PanelTitle.TLabel")
        name.pack(anchor="w", pady=(9, 4))
        if self.clips:
            cover.bind("<Configure>", lambda event: name.configure(wraplength=max(160, event.width-3)), add="+")
        status = "  ·  待审核" if row.review == "pending" else "  ·  可用" if row.review == "approved" else ""
        ttk.Label(body, text=f"{self.names[key]}  ·  {detail}{status}", style="PanelMuted.TLabel").pack(anchor="w")
        buttons = ttk.Frame(body, style="Panel.TFrame")
        buttons.pack(fill="x", pady=(10, 0))
        if self.review_asset:
            if row.review == "pending":
                ttk.Button(buttons, text="审核可用", command=lambda: self.review_asset(row, "approved")).pack(side="left")
            ttk.Button(buttons, text="移出", command=lambda: self.review_asset(row, "removed")).pack(side="left", padx=(3, 0))
            if row.asset_id.startswith("https://commons.wikimedia.org/wiki/"):
                ttk.Button(buttons, text="来源", command=lambda: webbrowser.open(row.asset_id)).pack(side="left", padx=(3, 0))
        elif not previous:
            ttk.Button(buttons, text="继续创作" if self.clips else "查看选题",
                       command=lambda: self.open_editor(key)).pack(side="left")
        if path and Path(path).is_file():
            if self.clips:
                caption, action = "翻阅", lambda: self.open_issue(row)
            elif row.video_asset and self.play_asset:
                caption, action = "播放审看", lambda: self.play_asset(row)
            else:
                caption, action = "打开", lambda: os.startfile(path)
            ttk.Button(buttons, text=caption, command=action).pack(side="right")


class MatrixPage:
    def __init__(self, parent, names, accounts):
        bar = heading(parent, "矩阵账号", "查看平台连接状态、投稿账号和已授权作品。")
        ttk.Button(bar, text="账号设置", style="Accent.TButton", command=accounts).pack(side="right")
        grid = ttk.Frame(parent)
        grid.pack(fill="x")
        self.cards = {}
        for index, (key, name) in enumerate(names.items()):
            grid.columnconfigure(index, weight=1, uniform="accounts")
            border, card = panel(grid)
            border.grid(row=0, column=index, sticky="nsew", padx=(0, 10 if index < len(names)-1 else 0))
            tk.Label(card, text=name, fg=PLATFORM_COLORS[key], bg="#20323e", font=(FONT, 10, "bold"), padx=8, pady=5).pack(anchor="w")
            status = ttk.Label(card, style="PanelTitle.TLabel")
            status.pack(anchor="w", pady=(13, 4))
            account = ttk.Label(card, style="PanelMuted.TLabel", wraplength=150)
            account.pack(anchor="w", pady=(0, 13))
            self.cards[key] = (status, account)
            ttk.Button(card, text="查看账号与作品 ↗", command=lambda platform=key: accounts(platform)).pack(anchor="w")

    def update(self, rows):
        for key, (status, account) in self.cards.items():
            row = rows.get(key, {})
            connected = bool(row.get("identity"))
            status.configure(text="已连接" if connected else "创作者后台" if key == "xiaohongshu" else "未连接")
            account.configure(text=row.get("account", "—") if connected else
                              "平台后台管理" if key == "xiaohongshu" else "可配置开放平台应用")
