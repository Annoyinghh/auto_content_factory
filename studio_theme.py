"""Visual system for the desktop workbench and its native dialogs."""

import sys
from tkinter import font, ttk

from PIL import Image, ImageDraw, ImageTk


RAIL = "#19191d"
PAGE = "#131317"
SURFACE = "#222225"
FIELD = "#17181c"
LINE = "#3c3837"
TEXT = "#f3eee6"
MUTED = "#b2aaa4"
ACCENT = "#76d1c4"
AMBER = "#efb275"
SELECTED = "#253a38"
FONT = "Microsoft YaHei UI"


def _chip(root, fill, surround):
    tile = Image.new("RGB", (66, 54), surround)
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, 65, 53), radius=18, fill=fill)
    return ImageTk.PhotoImage(tile.resize((22, 18), Image.Resampling.LANCZOS), master=root)


def icon(root, name, color=MUTED):
    size = max(16, round(root.winfo_fpixels("13p")))
    return ImageTk.PhotoImage(icon_bitmap(name, color).resize((size, size), Image.Resampling.LANCZOS), master=root)


def icon_bitmap(name, color=MUTED):
    tile = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(tile)
    if name == "brand":
        draw.rounded_rectangle((1, 1, 62, 62), radius=15, fill="#242322", outline="#5c4b3b", width=2)
        for x, dx in ((12, 11), (51, -11)):
            for y, dy in ((12, 11), (51, -11)):
                draw.line([(x + dx, y), (x, y), (x, y + dy)], fill=AMBER, width=3)
        draw.ellipse((22, 22, 41, 41), outline=ACCENT, width=3)
        draw.polygon([(29, 27), (37, 32), (29, 37)], fill=ACCENT)
    elif name == "overview":
        for x in (12, 34):
            for y in (12, 34):
                draw.rounded_rectangle((x, y, x + 16, y + 16), radius=4, outline=color, width=3)
    elif name == "collection":
        draw.line([(11, 33), (20, 15), (44, 15), (53, 33), (53, 50), (11, 50), (11, 33),
                   (24, 33), (28, 39), (36, 39), (40, 33), (53, 33)], fill=color, width=4, joint="curve")
    elif name == "trends":
        draw.line([(13, 48), (26, 35), (35, 41), (51, 22)], fill=color, width=4, joint="curve")
        draw.line([(39, 22), (51, 22), (51, 34)], fill=color, width=4, joint="curve")
    elif name == "production":
        draw.rounded_rectangle((10, 15, 54, 49), radius=7, outline=color, width=4)
        draw.polygon([(28, 24), (28, 40), (41, 32)], fill=color)
    elif name == "release":
        draw.line([(11, 31), (53, 12), (43, 52), (30, 38), (11, 31)], fill=color, width=4, joint="curve")
        draw.line([(30, 38), (53, 12)], fill=color, width=4)
    elif name == "plus":
        draw.line((32, 13, 32, 51), fill=color, width=4)
        draw.line((13, 32, 51, 32), fill=color, width=4)
    elif name == "search":
        draw.ellipse((12, 10, 43, 41), outline=color, width=4)
        draw.line((39, 39, 52, 52), fill=color, width=4)
    elif name == "bell":
        draw.line([(11, 46), (17, 37), (17, 25), (21, 16), (30, 12), (39, 15),
                   (46, 24), (46, 36), (53, 46), (11, 46)], fill=color, width=4, joint="curve")
        draw.arc((25, 44, 40, 56), 5, 175, fill=color, width=3)
    elif name == "accounts":
        draw.ellipse((22, 10, 42, 30), outline=color, width=4)
        draw.arc((12, 32, 52, 65), 195, 345, fill=color, width=4)
    elif name == "copywriting":
        draw.rounded_rectangle((15, 11, 49, 53), radius=5, outline=color, width=4)
        for y, right in ((23, 41), (32, 41), (41, 35)):
            draw.line((23, y, right, y), fill=color, width=3)
    elif name == "assets":
        draw.line([(9, 22), (25, 22), (31, 27), (54, 27), (54, 50), (9, 50), (9, 22)], fill=color, width=4)
        draw.line((14, 18, 38, 18), fill=color, width=3)
    elif name == "clips":
        draw.rounded_rectangle((10, 12, 54, 52), radius=5, outline=color, width=4)
        for x in (18, 46):
            draw.line((x, 14, x, 50), fill=color, width=3)
            for y in (24, 40):
                draw.line((x - 6, y, x + 6, y), fill=color, width=3)
    return tile


def _button(root, style, name, idle, active, selected, disabled):
    surround = RAIL if name == "Nav.TButton" else SURFACE if name in {"Metric.TButton", "Segment.TButton"} else PAGE
    pictures = [_chip(root, color, surround) for color in (idle, active, selected, disabled)]
    root._theme_images.extend(pictures)
    element = f"{name}.surface"
    style.element_create(element, "image", pictures[0], ("disabled", pictures[3]),
                         ("selected", pictures[2]), ("active", pictures[1]), border=(7, 4), sticky="nsew")
    style.layout(name, [(element, {"sticky": "nsew", "children": [
        ("Button.focus", {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})
        ]})
    ]})])


def apply_theme(root):
    root.configure(bg=PAGE)
    root._theme_images = []
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        font.nametofont(name, root).configure(family=FONT, size=10)
    for kind in ("Text", "Listbox"):
        root.option_add(f"*{kind}.background", FIELD)
        root.option_add(f"*{kind}.foreground", TEXT)
        root.option_add(f"*{kind}.selectBackground", SELECTED)
        root.option_add(f"*{kind}.selectForeground", TEXT)
    root.option_add("*Text.insertBackground", TEXT)
    root.option_add("*Text.highlightThickness", 0)
    root.option_add("*Text.relief", "flat")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=PAGE, foreground=TEXT, font=(FONT, 10), bordercolor=LINE,
                    lightcolor=PAGE, darkcolor=PAGE, troughcolor=PAGE, focuscolor=ACCENT)
    style.configure("TFrame", background=PAGE)
    style.configure("Rail.TFrame", background=RAIL)
    style.configure("TLabel", background=PAGE, foreground=TEXT)
    style.configure("Rail.TLabel", background=RAIL, foreground=TEXT)
    style.configure("RailMuted.TLabel", background=RAIL, foreground=MUTED, font=(FONT, 9))
    style.configure("Brand.TLabel", background=RAIL, foreground=TEXT, font=(FONT, 13, "bold"))
    style.configure("Title.TLabel", font=(FONT, 24, "bold"))
    style.configure("Headline.TLabel", font=(FONT, 16, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED, font=(FONT, 9))
    style.configure("Panel.TFrame", background=SURFACE)
    surface = Image.new("RGB", (34, 34), PAGE)
    ImageDraw.Draw(surface).rounded_rectangle((0, 0, 33, 33), radius=13, fill=SURFACE, outline=LINE)
    skin = ImageTk.PhotoImage(surface, master=root)
    root._theme_images.append(skin)
    style.element_create("ScreeningPanel.surface", "image", skin, border=15, sticky="nsew")
    style.layout("Rounded.TFrame", [("ScreeningPanel.surface", {"sticky": "nsew"})])
    for name, foreground, typography in (
        ("Panel", TEXT, (FONT, 10)), ("PanelTitle", TEXT, (FONT, 11, "bold")),
        ("PanelMuted", MUTED, (FONT, 9)), ("PanelAccent", ACCENT, (FONT, 9, "bold")),
        ("Number", TEXT, ("Segoe UI", 26, "bold")),
    ):
        style.configure(f"{name}.TLabel", background=SURFACE, foreground=foreground, font=typography)
    for name, colors in {
        "TButton": ("#302d31", "#423a36", SELECTED, SURFACE),
        "Accent.TButton": (AMBER, "#ffd6aa", AMBER, SURFACE),
        "Nav.TButton": (RAIL, "#302a29", SELECTED, RAIL),
        "Metric.TButton": (SURFACE, "#342e2b", SELECTED, SURFACE),
        "Segment.TButton": ("#302d31", "#423a36", SELECTED, SURFACE),
    }.items():
        _button(root, style, name, *colors)
        style.configure(name, foreground=TEXT, padding=(9, 3), width=0, anchor="center", focusthickness=1)
        style.map(name, foreground=[("disabled", "#56788a")])
    style.configure("Nav.TButton", foreground=MUTED, anchor="w", padding=(9, 4))
    style.map("Nav.TButton", foreground=[("selected", ACCENT), ("active", TEXT)])
    style.configure("Metric.TButton", foreground=MUTED, anchor="w", padding=(3, 2), font=(FONT, 9))
    style.map("Metric.TButton", foreground=[("active", TEXT)])
    style.map("Segment.TButton", foreground=[("selected", ACCENT), ("active", TEXT)])
    style.configure("Accent.TButton", foreground="#04121b", padding=(12, 4), font=(FONT, 10, "bold"))
    style.configure("TEntry", fieldbackground=FIELD, padding=(8, 5), insertcolor=TEXT, lightcolor=FIELD, darkcolor=FIELD)
    style.configure("TCombobox", fieldbackground=FIELD, background=SURFACE, padding=(8, 5), arrowsize=12,
                    arrowcolor=MUTED, lightcolor=FIELD, darkcolor=FIELD)
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)])
    style.configure("TNotebook", background=PAGE, borderwidth=0)
    style.configure("TNotebook.Tab", background=FIELD, foreground=MUTED, padding=(14, 8), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", TEXT)])
    style.configure("Treeview", background=PAGE, fieldbackground=PAGE, foreground=TEXT, borderwidth=0, rowheight=38,
                    lightcolor=PAGE, darkcolor=PAGE)
    style.configure("Feed.Treeview", rowheight=41)
    style.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, padding=(9, 8), borderwidth=0,
                    lightcolor=SURFACE, darkcolor=SURFACE, relief="flat", font=(FONT, 9))
    style.map("Treeview.Heading", background=[("active", SURFACE)])
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", TEXT)])
    style.configure("Vertical.TScrollbar", background=LINE, troughcolor=PAGE, arrowcolor=MUTED, arrowsize=10,
                    borderwidth=0, lightcolor=LINE, darkcolor=LINE)
    style.map("Vertical.TScrollbar", background=[("active", "#474b56")])
    root.after_idle(lambda: _native_caption(root))


def _native_caption(root):
    if sys.platform != "win32":
        return
    import ctypes

    try:
        handle = ctypes.c_void_p(int(root.frame(), 0))
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 20, ctypes.byref(value), ctypes.sizeof(value))
        for attribute, rgb in ((35, RAIL), (36, TEXT)):
            colorref = ctypes.c_uint(int.from_bytes(bytes.fromhex(rgb[1:]), "little"))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, attribute, ctypes.byref(colorref), ctypes.sizeof(colorref))
    except (AttributeError, OSError, ValueError):
        pass
