"""Native navigation and shared panels for the operations workbench."""

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageOps, ImageTk

from config import ASSETS_DIR
from studio_theme import ACCENT, AMBER, FONT, LINE, MUTED, PAGE, RAIL, SURFACE, TEXT, icon, icon_bitmap


SCREENING = ASSETS_DIR / "screening"


PAGES = {
    "overview": ("运营总览", "overview"), "trends": ("平台热点", "trends"),
    "collection": ("采集库", "collection"), "assets": ("素材库", "assets"),
    "clips": ("作品书架", "clips"), "production": ("AI 创作", "production"),
    "release": ("发布中心", "release"), "accounts": ("矩阵账号", "accounts"),
    "copywriting": ("批量文案", "copywriting"),
}


def panel(parent, *, padding=16):
    border = ttk.Frame(parent, style="Rounded.TFrame")
    body = ttk.Frame(border, style="Panel.TFrame", padding=padding)
    body.pack(fill="both", expand=True, padx=7, pady=7)
    return border, body


class PhotoBanner(tk.Canvas):
    def __init__(self, parent, asset, *, label, title, subtitle, action=None, button=None, height=276, frame=False):
        super().__init__(parent, bg=PAGE, height=height, bd=0, highlightthickness=0)
        self.copy = label, title, subtitle
        self.frame = frame
        with Image.open(asset) as source:
            self.photo = source.convert("RGB")
        self.backdrop = self.create_image(0, 0, anchor="nw")
        self.action = ttk.Button(self, text=button, style="Accent.TButton", command=action) if action else None
        if self.action:
            self.create_window(27, height - 34, anchor="sw", window=self.action, tags="action")
        self.bind("<Configure>", self.paint)

    def paint(self, event):
        w, h = event.width, event.height
        if w < 8 or h < 8:
            return
        crop = ImageOps.fit(self.photo, (w, h), centering=(.52, .45))
        fade = Image.new("L", (1, h))
        fade.putdata([int(250 * max(0, (i / h - .52) / .48) ** 2) for i in range(h)])
        crop = Image.composite(Image.new("RGB", (w, h), PAGE), crop, fade.resize((w, h)))
        mask = Image.new("L", (w, h))
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w-1, h-1), radius=16, fill=255)
        crop = Image.composite(crop, Image.new("RGB", (w, h), PAGE), mask)
        self.picture = ImageTk.PhotoImage(crop, master=self)
        self.itemconfigure(self.backdrop, image=self.picture)
        self.delete("copy")
        label, title, subtitle = self.copy
        compact = h < 220
        if self.frame:
            for x, dx in ((12, 12), (w-13, -12)):
                for y, dy in ((12, 12), (h-13, -12)):
                    self.create_line(x+dx, y, x, y, x, y+dy, fill="#987858", tags="copy")
            self.create_line(27, 32, 39, 32, fill=AMBER, width=2, tags="copy")
        self.create_text(48 if self.frame else 27, 24, text=label, fill=AMBER, anchor="nw",
                         font=("Segoe UI", 9, "bold"), tags="copy")
        self.create_text(25, 50 if compact else 56, text=title, fill=TEXT, anchor="nw", font=(FONT, 23 if compact else 32, "bold"), tags="copy")
        self.create_text(27, 90 if compact else 113, text=subtitle, fill="#e1d8cf", anchor="nw",
                         font=(FONT, 10), width=min(390, int(w*.53)), tags="copy")
        if self.action:
            self.coords("action", 27, h-19 if compact else h-31)
            self.tag_raise("action")


def heading(parent, title, subtitle, *, kicker=None):
    box = ttk.Frame(parent)
    box.pack(fill="x", pady=(0, 18))
    copy = ttk.Frame(box)
    copy.pack(side="left", fill="x", expand=True)
    if kicker:
        tk.Label(copy, text=kicker, bg=PAGE, fg=ACCENT, font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 5))
    ttk.Label(copy, text=title, style="Title.TLabel").pack(anchor="w")
    ttk.Label(copy, text=subtitle, style="Muted.TLabel", wraplength=440, justify="left").pack(anchor="w", pady=(5, 0))
    return box


class ScrollArea(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=PAGE, bd=0, highlightthickness=0, yscrollincrement=12)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(self, command=self.canvas.yview)
        bar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=bar.set)
        self.body = ttk.Frame(self.canvas, padding=(24, 22, 24, 24))
        window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.winfo_toplevel().bind("<MouseWheel>", self._wheel, add="+")

    def _wheel(self, event):
        widget = str(event.widget)
        canvas = str(self.canvas)
        if self.winfo_viewable() and (widget == canvas or widget.startswith(canvas + ".")):
            self.canvas.yview_scroll(-int(event.delta / 40), "units")
            return "break"


class WorkbenchShell:
    def __init__(self, root, navigate, search):
        self.rail = ttk.Frame(root, style="Rail.TFrame", width=210, padding=(13, 15))
        self.rail.pack(side="left", fill="y")
        self.rail.pack_propagate(False)
        tk.Frame(root, background=LINE, width=1).pack(side="left", fill="y")
        main = ttk.Frame(root)
        main.pack(side="left", fill="both", expand=True)
        brand = ttk.Frame(self.rail, style="Rail.TFrame", padding=(7, 4, 0, 19))
        brand.pack(fill="x")
        self.mark = ImageTk.PhotoImage(icon_bitmap("brand").resize((37, 37), Image.Resampling.LANCZOS), master=root)
        ttk.Label(brand, image=self.mark, style="Rail.TLabel").pack(side="left", padx=(0, 9))
        label = ttk.Frame(brand, style="Rail.TFrame")
        label.pack(side="left")
        ttk.Label(label, text="热采放映室", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(label, text="SCREENING ROOM", style="RailMuted.TLabel").pack(anchor="w")
        self.buttons, self.icons = {}, {}
        for group, keys in (
            ("运营", ("overview", "trends", "collection")),
            ("内容", ("production", "clips")),
            ("发布与账号", ("release", "accounts")),
        ):
            ttk.Label(self.rail, text=group, style="RailMuted.TLabel", padding=(10, 10, 0, 4)).pack(fill="x")
            for key in keys:
                title, glyph = PAGES[key]
                self.icons[key] = icon(root, glyph), icon(root, glyph, ACCENT)
                button = ttk.Button(self.rail, text=title, image=self.icons[key][0], compound="left",
                                    style="Nav.TButton", command=lambda page=key: navigate(page))
                button.pack(fill="x", pady=1)
                self.buttons[key] = button
        foot = ttk.Frame(self.rail, style="Rail.TFrame")
        foot.pack(side="bottom", fill="x", padx=6, pady=3)
        tk.Frame(foot, height=1, bg=LINE).pack(fill="x", pady=(0, 10))
        ttk.Label(foot, text="个人工作区", style="Rail.TLabel").pack(anchor="w")
        ttk.Label(foot, text="本地数据库 · Windows", style="RailMuted.TLabel").pack(anchor="w", pady=(3, 0))

        header = ttk.Frame(main, padding=(24, 11))
        header.pack(fill="x")
        ttk.Label(header, text="工作空间    /", style="Muted.TLabel").pack(side="left", padx=(0, 12))
        self.crumb = ttk.Label(header)
        self.crumb.pack(side="left")
        self.date = ttk.Combobox(header, state="readonly", width=12)
        self.date.pack(side="right")
        self.search = ttk.Entry(header, width=24)
        self.search.pack(side="right", padx=(9, 16))
        self.search.bind("<Return>", lambda e: search(self.search.get().strip()))
        self.search_button = ttk.Button(header, text="搜索热点", command=lambda: search(self.search.get().strip()))
        self.search_button.pack(side="right")
        root.bind("<Control-f>", lambda e: self.search.focus_set())
        tk.Frame(main, height=1, bg=LINE).pack(fill="x")
        self.workspace = ttk.Frame(main)
        self.workspace.pack(fill="both", expand=True)

    def show(self, page):
        self.crumb.configure(text=PAGES[page][0])
        for key, button in self.buttons.items():
            active = key == page
            button.state(["selected" if active else "!selected"])
            button.configure(image=self.icons[key][int(active)])
