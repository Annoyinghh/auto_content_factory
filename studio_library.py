"""Native browsing surfaces for existing media, edits and account connections."""

import os
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk

from config import OUTPUT_DIR, PLATFORM_PRESETS
from core.media_review import media_state
from core.works_library import duration_label, load_works
from studio_dashboard import PLATFORM_COLORS
from studio_shell import SCREENING, PhotoBanner, ScrollArea, heading, panel
from studio_theme import ACCENT, AMBER, FIELD, FONT, LINE, MUTED, PAGE, TEXT


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


def load_thumbnail(preview, size, fallback=None):
    for path in (preview, fallback):
        if path:
            try:
                return thumbnail(path, size)
            except (OSError, ValueError):
                continue
    return None


class CoverStage(tk.Canvas):
    """Light the shelf from the work's own cover while keeping the cover legible."""

    def __init__(self, parent, entry, open_issue, *, selected=False, platform=None):
        super().__init__(parent, bg=PAGE if platform else FIELD, width=1, height=232 if platform else 154,
                         highlightthickness=0, bd=0)
        self.entry, self.selected, self.platform = entry, selected, platform
        self.render_size = None
        self.cover = None
        self.backdrop = self.create_image(0, 0, anchor="nw")
        self.corner = self.create_polygon(0, 0, 0, 0, 0, 0, fill=AMBER, state="hidden")
        if platform:
            button = ttk.Button(self, text="打开刊物  ↗", style="Accent.TButton", command=open_issue)
            self.create_window(26, 211, anchor="sw", window=button, tags="action")
        elif entry.modified:
            self.configure(cursor="hand2")
            self.bind("<Button-1>", lambda event: open_issue())
        self.bind("<Configure>", self.paint)

    def set_cover(self, cover):
        if cover is not None:
            self.cover = cover
            self.render_size = None
            self.paint()

    def paint(self, event=None):
        w, h = (event.width, event.height) if event else (self.winfo_width(), self.winfo_height())
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
    def __init__(self, parent, names, open_editor, primary_action, start, *, clips=False, read_work=None, review_asset=None,
                 play_asset=None):
        super().__init__(parent)
        self.names, self.open_editor, self.clips = names, open_editor, clips
        self.start = start
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
        self.cards_revision = None
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
        revision = self.cards_revision = object()
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
        feature = self.featured(featured, columns) if featured else None
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
            images = []
            if start == 0 and feature:
                images.append((featured.preview, feature.set_cover))
            for index in range(start, end):
                entry = rows[index]
                apply_image = self.card(entry, first_row + index // columns, index % columns, columns)
                images.append((entry.preview, apply_image))

            def ready(frames, error):
                if self.cards_revision is not revision or not self.winfo_exists():
                    return
                if not error:
                    for (_, apply_image), frame in zip(images, frames, strict=True):
                        if frame is not None:
                            apply_image(frame)
                if end < stop:
                    self.cards_timer = self.after(20, lambda: add_row(end))
                elif stop < len(rows):
                    ttk.Button(self.grid_body, text=f"显示更多 · 还有 {len(rows)-stop} 项", command=self.show_more).grid(
                        row=(stop + columns-1)//columns + first_row, column=0, columnspan=columns, pady=9)

            if images:
                size = (760, 540) if self.clips else (206, 120)
                fallback = SCREENING / "viewfinder.png" if self.clips else None
                self.start(lambda: [load_thumbnail(path, size, fallback) for path, _ in images], ready)

        add_row(0)

    def show_more(self):
        self.limit += 18
        self.redraw()

    @staticmethod
    def show_artwork(thumb, frame=None):
        size = (206, 120)
        tile = Image.new("RGB", size, FIELD)
        if frame is not None:
            tile.paste(frame, ((size[0]-frame.width)//2, (size[1]-frame.height)//2))
        thumb.picture = ImageTk.PhotoImage(tile, master=thumb)
        thumb.configure(image=thumb.picture)

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
        return feature

    def card(self, row, y, x, columns):
        key, title, detail, preview, path, previous = row.key, row.title, row.detail, row.preview, row.path, row.previous
        border, body = panel(self.grid_body, padding=(12, 12))
        border.grid(row=y, column=x, sticky="nsew", padx=(0, 11 if x < columns-1 else 0), pady=(0, 12))
        if self.clips:
            cover = CoverStage(body, row, lambda: self.open_issue(row),
                               selected=bool(row.path and row.path == self.selected_video))
            cover.pack(fill="x")
            self.covers.append(cover)
            apply_image = cover.set_cover
        else:
            thumb = tk.Label(body, bg=FIELD, bd=0)
            self.show_artwork(thumb)
            thumb.pack(fill="x")
            apply_image = lambda image: self.show_artwork(thumb, image)
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
        return apply_image


def work_format(work):
    return {"video": "视频", "preview": "试剪视频", "image": "图文笔记",
            "text": "笔记文稿" if (work.draft or {}).get("pages") else "纯文字笔记",
            "copy": "文案", "production": "创作草稿"}[work.kind]


class AlbumCover(tk.Canvas):
    """A deck of saved editions; an unillustrated work uses its own title."""

    def __init__(self, parent, platform):
        self.mat, self.edge, self.back, self.ink = (
            ("#f4e7dd", "#cd9a82", "#e7c2ae", "#763c2d") if platform == "xiaohongshu"
            else ("#e8ebf8", "#8292cb", "#b2beeb", "#314887")
        )
        super().__init__(parent, bg=self.mat, height=151, width=1, bd=0, highlightthickness=0)
        self.work = self.art = None
        self.count = 1
        self.bind("<Configure>", self.paint)

    def update_work(self, work, count, art=None):
        self.work, self.count, self.art = work, count, art
        self.paint()

    def paint(self, _event=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if not self.work or min(w, h) < 55:
            return
        fw, fh = min(140, w - 38), min(111, h - 20)
        x, y = (w-fw)//2, (h-fh)//2
        if self.count > 1:
            for dx, skew in ((-12, -8), (12, 8)):
                self.create_polygon(x+dx, y+skew+5, x+dx+fw, y-skew+5, x+dx+fw, y+fh-skew+2,
                                    x+dx, y+fh+skew+2, fill=self.back, outline=self.edge)
        self.create_rectangle(x+2, y+4, x+fw+3, y+fh+4, fill=self.edge, outline="")
        self.create_rectangle(x, y, x+fw, y+fh, fill=self.ink, outline="")
        if self.art:
            picture = ImageOps.contain(self.art, (fw-2, fh-2))
            self.picture = ImageTk.PhotoImage(picture, master=self)
            self.create_image(w//2, h//2, image=self.picture)
        else:
            self.picture = None
            self.create_text(x+10, y+11, text=work_format(self.work), anchor="nw", fill="#fff6ef",
                             font=(FONT, 8, "bold"))
            title = self.work.title
            if len(title) > 33:
                title = title[:32].rstrip() + "…"
            self.create_text(x+10, y+32, text=title, width=fw-20, anchor="nw", fill="white",
                             font=(FONT, 10, "bold"))


class WorksLibrary(ScrollArea):
    def __init__(self, parent, names, open_editor, primary_action, start, *, read_work,
                 output_dir=OUTPUT_DIR, open_writing=None):
        super().__init__(parent)
        self.names, self.open_editor, self.start = names, open_editor, start
        self.read_work, self.open_writing, self.output_dir = read_work, open_writing, output_dir
        self.albums, self.day, self.limit = (), "", 10
        self.pending = self.timer = self.loading = None
        self.error = False

        top = heading(self.body, "作品库", "按选题整理各平台的成片、试剪和笔记。", kicker="作品库  /  选题专辑")
        ttk.Button(top, text="＋  AI 创作", style="Accent.TButton", command=primary_action).pack(side="right")
        controls = ttk.Frame(self.body)
        controls.pack(fill="x", pady=(0, 16))
        self.platform = tk.StringVar(value="所有平台")
        platforms = ttk.Combobox(controls, state="readonly", values=("所有平台", *names.values()),
                                 textvariable=self.platform, width=10)
        platforms.pack(side="left", padx=(0, 12))
        platforms.bind("<<ComboboxSelected>>", self.filter_changed)
        self.date_filter = tk.StringVar(value="全部日期")
        dates = ttk.Combobox(controls, state="readonly", values=("全部日期", "当前工作日"),
                             textvariable=self.date_filter, width=10)
        dates.pack(side="left", padx=(0, 13))
        dates.bind("<<ComboboxSelected>>", self.filter_changed)
        ttk.Label(controls, text="搜索选题或作品", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        self.query = tk.StringVar()
        ttk.Entry(controls, textvariable=self.query, width=22).pack(side="left")
        self.query.trace_add("write", self.filter_changed)
        self.count = ttk.Label(controls, style="Muted.TLabel")
        self.count.pack(side="right")
        self.shelf = ttk.Frame(self.body)
        self.shelf.pack(fill="x")

    def set_data(self, edition, day, release_rows=None):
        self.pending = (edition, str(day), release_rows or {})
        if not self.loading:
            self.load()

    def load(self):
        edition, day, release_rows = self.pending
        self.loading, self.pending = True, None
        if not self.albums:
            self.count.configure(text="正在读取作品…")

        def ready(albums, error):
            self.loading = False
            if not self.winfo_exists():
                return
            if self.pending:
                self.load()
                return
            changed = self.albums != albums or self.day != day or self.error != bool(error)
            if self.day != day:
                self.limit = 10
            self.day, self.error = day, bool(error)
            if not error:
                self.albums = albums
            if changed or not self.albums:
                self.redraw()

        self.start(lambda: load_works(self.output_dir, edition=edition, day=day, release_rows=release_rows), ready)

    def filter_changed(self, *_args):
        if self.timer:
            self.after_cancel(self.timer)
        self.limit = 10
        self.timer = self.after(110, self.redraw)

    def redraw(self):
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        for widget in self.shelf.winfo_children():
            widget.destroy()
        platform = self.platform.get()
        selected_day = self.day if self.date_filter.get() == "当前工作日" else ""
        query = self.query.get().strip().casefold()
        rows = []
        for album in self.albums:
            works = [work for work in album.works if work.platform in self.names
                     and (platform == "所有平台" or self.names[work.platform] == platform)
                     and (not selected_day or work.day == selected_day)
                     and (query in album.title.casefold() or query in work.title.casefold())]
            if works:
                rows.append((album.title, works))
        rows.sort(key=lambda row: any(work.day == self.day for work in row[1]), reverse=True)
        self.count.configure(text=f"{len(rows)} 本专辑  ·  {sum(len(works) for _, works in rows)} 项作品")
        if not rows:
            border, body = panel(self.shelf, padding=20)
            border.pack(fill="x")
            ttk.Label(body, text="暂时无法读取本地作品。" if self.error else
                      "没有符合筛选的作品。" if self.albums else "还没有保存的作品。",
                      style="PanelTitle.TLabel").pack(anchor="w")
            if not self.albums and not self.error:
                ttk.Label(body, text="生成笔记、保存草稿或导出视频后，作品会按选题出现在这里。",
                          style="PanelMuted.TLabel", wraplength=480).pack(anchor="w", pady=(5, 0))
                if self.open_writing:
                    ttk.Button(body, text="打开文案创作", command=self.open_writing).pack(anchor="w", pady=(9, 0))
            return
        for title, works in rows[:self.limit]:
            self.album(title, works)
        if len(rows) > self.limit:
            ttk.Button(self.shelf, text=f"显示更多 · 还有 {len(rows)-self.limit} 本专辑",
                       command=self.show_more).pack(pady=(0, 12))

    def show_more(self):
        self.limit += 10
        self.redraw()

    def album(self, title, works):
        section = ttk.Frame(self.shelf, padding=(0, 0, 0, 15))
        section.pack(fill="x")
        tk.Frame(section, bg=LINE, height=1).pack(fill="x", pady=(0, 10))
        group = {platform: [work for work in works if work.platform == platform] for platform in self.names}
        group = {platform: versions for platform, versions in group.items() if versions}
        tk.Label(section, bg=PAGE, fg=ACCENT, text=f"选题专辑  /  {len(group)} 个平台 · {len(works)} 项作品",
                 font=(FONT, 8, "bold")).pack(anchor="w")
        headline = ttk.Label(section, text=title, style="Headline.TLabel", justify="left")
        headline.pack(anchor="w", fill="x", pady=(3, 8))
        headline.bind("<Configure>", lambda e: headline.configure(wraplength=max(200, e.width-6)))
        grid = ttk.Frame(section)
        grid.pack(fill="x")
        for column in range(3):
            grid.columnconfigure(column, weight=1, uniform="works")
        for column, (platform, versions) in enumerate(group.items()):
            self.platform_card(grid, column, platform, versions)

    def platform_card(self, grid, column, platform, versions):
        box = ttk.Frame(grid, padding=(0, 0, 12 if column < 2 else 0, 2))
        box.grid(row=0, column=column, sticky="nsew")
        cover = AlbumCover(box, platform)
        cover.pack(fill="x")
        ttk.Label(box, text=self.names[platform], style="Headline.TLabel").pack(anchor="w", pady=(5, 0))
        kinds = tuple(dict.fromkeys(work_format(work) for work in versions))
        summary = ttk.Label(box, text=" · ".join(kinds), style="Muted.TLabel")
        summary.pack(fill="x", pady=(1, 4))
        select = None
        if len(versions) > 1:
            labels = [f"{work_format(work)} · {work.day[5:]} · {work.title}" for work in versions]
            select = ttk.Combobox(box, state="readonly", values=labels, width=16)
            select.pack(fill="x", pady=(0, 5))
            select.current(next((i for i, work in enumerate(versions) if work.kind == "image"), 0))
        title = ttk.Label(box, justify="left")
        title.pack(fill="x")
        meta = tk.Label(box, bg=PAGE, fg=ACCENT, font=(FONT, 8, "bold"), anchor="w", justify="left")
        meta.pack(fill="x", pady=(3, 5))
        actions = ttk.Frame(box)
        actions.pack(fill="x")
        action = ttk.Button(actions)
        action.pack(side="left")
        edit = ttk.Button(actions, text="继续创作", command=lambda: self.open_editor(platform))

        def wrap(event):
            for label in (summary, title, meta):
                label.configure(wraplength=max(80, event.width-14))

        box.bind("<Configure>", wrap)

        def choose(_event=None):
            work = versions[max(0, select.current())] if select else versions[0]
            cover.update_work(work, len(versions))
            title.configure(text=work.title)
            details = [work.state]
            edit.pack_forget()
            if work.package:
                details.extend([PLATFORM_PRESETS[platform]["aspect_ratio"], duration_label(work.package)])
                action.configure(text="预览视频", command=lambda: self.read_work(
                    work.package, work.title, self.names[platform], work.preview))
                if work.editable:
                    edit.pack(side="left", padx=(5, 0))
            elif work.draft:
                if work.pictures:
                    details.append(f"{len(work.pictures)} 张")
                action.configure(text="阅读图文" if work.pictures else "阅读文稿",
                                 command=lambda: NotePreview(self, work, self.names[platform], self.start))
            else:
                action.configure(text="继续创作", command=lambda: self.open_editor(platform))
            meta.configure(text="  ·  ".join(filter(None, (*details, work.day))))
            if work.preview:
                def ready(image, error):
                    if not error and cover.winfo_exists() and cover.work is work:
                        cover.update_work(work, len(versions), image)
                self.start(lambda: load_thumbnail(work.preview, (300, 260)), ready)

        if select:
            select.bind("<<ComboboxSelected>>", choose)
        choose()


class NotePreview(tk.Toplevel):
    """Read the actual note text and page through its locally saved pictures."""

    def __init__(self, parent, work, platform, start):
        super().__init__(parent)
        self.work, self.start, self.page, self.art = work, start, 0, None
        self.loading_image = False
        self.title(f"{platform} · {work.title}")
        self.transient(parent.winfo_toplevel())
        self.configure(bg=PAGE)
        self.geometry("850x590")
        self.minsize(620, 410)
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        heading(root, work.title, f"{work_format(work)}  ·  {work.day}", kicker=f"{platform}  /  本地作品")
        columns = ttk.Frame(root)
        columns.pack(fill="both", expand=True)
        if work.pictures:
            art_panel = ttk.Frame(columns, width=332)
            art_panel.pack(side="left", fill="both", padx=(0, 14))
            art_panel.pack_propagate(False)
            self.image = tk.Label(art_panel, bg=FIELD, fg=MUTED)
            self.image.pack(fill="both", expand=True)
            self.image.bind("<Configure>", self.paint)
            self.credit = ttk.Label(art_panel, style="Muted.TLabel", wraplength=320)
            self.credit.pack(anchor="w", pady=(5, 0))
            pages = ttk.Frame(art_panel)
            pages.pack(fill="x", pady=(5, 0))
            self.back = ttk.Button(pages, text="‹ 上一张", command=lambda: self.show_page(self.page-1))
            self.back.pack(side="left")
            self.next = ttk.Button(pages, text="下一张 ›", command=lambda: self.show_page(self.page+1))
            self.next.pack(side="right")
            self.pager = ttk.Label(pages, style="Muted.TLabel")
            self.pager.pack(expand=True)
            self.bind("<Left>", lambda _: self.show_page(self.page-1))
            self.bind("<Right>", lambda _: self.show_page(self.page+1))
        text_panel = ttk.Frame(columns)
        text_panel.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(text_panel)
        scrollbar.pack(side="right", fill="y")
        words = tk.Text(text_panel, wrap="word", font=(FONT, 11), fg=TEXT, bg=FIELD, relief="flat",
                        highlightthickness=1, highlightbackground=LINE, padx=14, pady=12, spacing3=6,
                        yscrollcommand=scrollbar.set)
        words.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=words.yview)
        draft = work.draft
        text = [draft.get("script", "")]
        if draft.get("caption"):
            text.extend(["发布配文", draft["caption"]])
        for index, page in enumerate(draft.get("pages", ()), 1):
            text.extend([f"{index:02d}  {page.get('title', '')}", page.get("text", "")])
        words.insert("1.0", "\n\n".join(filter(None, text)))
        words.configure(state="disabled")
        bottom = ttk.Frame(root)
        bottom.pack(fill="x", pady=(11, 0))
        if work.directory:
            ttk.Button(bottom, text="打开作品文件夹", command=lambda: os.startfile(work.directory)).pack(side="left")
        if work.document:
            ttk.Button(bottom, text="打开本批文档", command=lambda: os.startfile(work.document)).pack(side="left", padx=6)
        close = ttk.Button(bottom, text="关闭", command=self.destroy)
        close.pack(side="right")
        self.bind("<Escape>", lambda _: self.destroy())
        if work.pictures:
            self.show_page(0)
        close.focus_set()

    def show_page(self, index):
        if not 0 <= index < len(self.work.pictures):
            return
        self.page, self.art, self.loading_image = index, None, True
        picture = self.work.pictures[index]
        photo = picture.get("photo") or {}
        self.credit.configure(text="  ·  ".join(filter(None, (photo.get("author"), photo.get("license")))))
        self.pager.configure(text=f"{index+1} / {len(self.work.pictures)}")
        self.back.configure(state="normal" if index else "disabled")
        self.next.configure(state="normal" if index+1 < len(self.work.pictures) else "disabled")
        self.image.configure(image="", text="正在读取图片…")

        def ready(image, error):
            if self.winfo_exists() and self.page == index:
                self.art = image if not error else None
                self.loading_image = False
                self.paint()

        self.start(lambda: load_thumbnail(picture["file"], (700, 900)), ready)

    def paint(self, _event=None):
        w, h = self.image.winfo_width(), self.image.winfo_height()
        if self.loading_image:
            return
        if self.art is None:
            self.image.configure(image="", text="无法读取本地图片。")
        elif w > 12 and h > 12:
            art = ImageOps.contain(self.art, (w-8, h-8))
            self.picture = ImageTk.PhotoImage(art, master=self)
            self.image.configure(image=self.picture, text="")


class MatrixPage:
    def __init__(self, parent, names, accounts, save_douyin, open_douyin):
        bar = heading(parent, "矩阵账号", "查看平台连接状态、投稿账号和已授权作品。")
        ttk.Button(bar, text="开放平台设置", command=accounts).pack(side="right")
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

        border, body = panel(parent)
        border.pack(fill="both", expand=True, pady=(16, 0))
        header = ttk.Frame(body, style="Panel.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="抖音个人号", style="PanelTitle.TLabel").pack(side="left")
        ttk.Button(header, text="添加抖音账号", style="Accent.TButton", command=save_douyin).pack(side="right")
        ttk.Label(body, text="每个号首次扫码后会保留自己的登录。发稿时在发布中心选择账号。",
                  style="PanelMuted.TLabel").pack(anchor="w", pady=(6, 12))
        listing = ttk.Frame(body, style="Panel.TFrame")
        listing.pack(fill="both", expand=True)
        self.douyin = ttk.Treeview(listing, show="headings", columns=("account",), height=5, selectmode="browse")
        self.douyin.heading("account", text="账号备注")
        self.douyin.column("account", anchor="w")
        scroll = ttk.Scrollbar(listing, command=self.douyin.yview)
        self.douyin.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.douyin.pack(side="left", fill="both", expand=True)
        controls = ttk.Frame(body, style="Panel.TFrame")
        controls.pack(fill="x", pady=(10, 0))
        self.sign_in = ttk.Button(controls, text="登录 / 查看账号",
                                  command=lambda: open_douyin(self.douyin.selection()[0]))
        self.sign_in.pack(side="left")
        self.rename = ttk.Button(controls, text="修改备注",
                                 command=lambda: save_douyin(self.douyin.selection()[0]))
        self.rename.pack(side="left", padx=8)
        self.douyin.bind("<<TreeviewSelect>>", self.update_douyin_actions)
        self.update_douyin_actions()

    def update_douyin_actions(self, _event=None):
        state = "normal" if self.douyin.selection() else "disabled"
        self.sign_in.configure(state=state)
        self.rename.configure(state=state)

    def update(self, rows, douyin_profiles):
        for key, (status, account) in self.cards.items():
            row = rows.get(key, {})
            connected = bool(row.get("identity"))
            status.configure(text="开放平台已授权" if connected else
                             ("工作台内登录" if key == "douyin" else "创作者后台"))
            account.configure(text=row.get("account", "—") if connected else
                              ("在下方管理个人号" if key == "douyin" else "在浏览器登录和管理作品"))
        selected = self.douyin.selection()
        self.douyin.delete(*self.douyin.get_children())
        for profile_id, label in douyin_profiles.items():
            self.douyin.insert("", "end", iid=profile_id, values=(label,))
        if selected and selected[0] in douyin_profiles:
            self.douyin.selection_set(selected[0])
        elif len(douyin_profiles) == 1:
            self.douyin.selection_set(next(iter(douyin_profiles)))
        self.update_douyin_actions()
