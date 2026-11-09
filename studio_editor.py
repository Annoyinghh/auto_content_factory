"""Small storyboard editor used for manual and AI-assisted cuts."""

import tkinter as tk
from tkinter import messagebox, ttk
from core.editing import validate_cut
from core.episodes import MOTIONS


class CutEditor:
    def __init__(self, parent, name, draft, media, save, *, initial_scene=0):
        self.window = tk.Toplevel(parent)
        self.window.title(f"{name} · 分镜草稿")
        self.window.transient(parent)
        self.window.geometry("960x650")
        self.window.minsize(820, 540)
        self.save = save
        self.rows = []
        self.media = media
        self.episode = len(draft["scenes"]) > 6 or bool(draft.get("chapter_starts"))
        self.plan = draft.get("editorial_plan")
        self.chapter_panels = {}
        self.assets = {"文字卡片": ""}
        self.assets.update({f"{index} · {asset['title'][:54]}": asset["page_url"]
                            for index, asset in enumerate(media, 1)})
        display_name = {url: label for label, url in self.assets.items()}
        page = ttk.Frame(self.window, padding=18)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="编辑分镜", style="Headline.TLabel").pack(anchor="w")
        if self.plan:
            ttk.Label(page, text="全片主线", style="Muted.TLabel").pack(anchor="w")
            self.direction = ttk.Entry(page)
            self.direction.insert(0, self.plan["throughline"])
            self.direction.pack(fill="x", pady=(2, 4))
        else:
            ttk.Label(page, text="修改口播和选材，保存后可按草稿重新出片。", style="Muted.TLabel").pack(anchor="w")
        content = ttk.Frame(page)
        content.pack(fill="both", expand=True, pady=12)
        self.shots = ttk.Treeview(content, show="tree", selectmode="browse", height=12)
        self.shots.column("#0", width=230, stretch=False)
        self.shots.pack(side="left", fill="y", padx=(0, 12))
        self.shots.bind("<<TreeviewSelect>>", self.select_scene)
        style = ttk.Style(self.window)
        style.layout("Storyboard.TNotebook.Tab", [])
        self.tabs = ttk.Notebook(content, style="Storyboard.TNotebook")
        self.tabs.pack(side="left", fill="both", expand=True)
        starts = {entry["scene"]: entry["title"] for entry in draft.get("chapter_starts", [])}
        for index, scene in enumerate(draft["scenes"]):
            self.add_scene(scene, display_name.get(scene["media"], "文字卡片"))
            if index in starts:
                self.chapter_panels[str(self.rows[-1][0])] = starts[index]
        if 0 <= initial_scene < len(self.rows):
            self.tabs.select(self.rows[initial_scene][0])
        self.refresh_scenes()
        actions = ttk.Frame(page)
        actions.pack(fill="x")
        ttk.Button(actions, text="上移分镜", command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(actions, text="下移分镜", command=lambda: self.move(1)).pack(side="left", padx=6)
        ttk.Button(actions, text="保存剪辑草稿", style="Accent.TButton", command=self.submit).pack(side="right")

    def add_scene(self, scene, media_label):
        panel = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(panel, text=f"分镜 {len(self.rows) + 1}")
        ttk.Label(panel, text="分镜标题").pack(anchor="w")
        title = ttk.Entry(panel)
        title.insert(0, scene["title"])
        title.pack(fill="x", pady=(2, 8))
        ttk.Label(panel, text="口播").pack(anchor="w")
        text = tk.Text(panel, height=5, wrap="word", padx=6, pady=5)
        text.insert("1.0", scene["text"])
        text.pack(fill="both", expand=True, pady=(2, 8))
        row = ttk.Frame(panel)
        row.pack(fill="x")
        ttk.Label(row, text="画面").pack(side="left")
        media = ttk.Combobox(row, state="readonly", values=list(self.assets))
        media.set(media_label)
        media.pack(side="left", fill="x", expand=True, padx=6)
        zoom = tk.BooleanVar(master=self.window, value=scene["zoom"])
        ttk.Checkbutton(row, text="照片缓慢推近", variable=zoom).pack(side="right")
        motion = None
        if self.episode:
            effects = ttk.Frame(panel)
            effects.pack(fill="x", pady=(8, 0))
            ttk.Label(effects, text="镜头动效").pack(side="left")
            motion = ttk.Combobox(effects, state="readonly", width=13, values=("自动", *MOTIONS.values()))
            motion.set(MOTIONS.get(scene.get("motion"), "自动"))
            motion.pack(side="left", padx=6)
        self.rows.append((panel, title, text, media, zoom, motion))

    def refresh_scenes(self):
        selected = self.tabs.select()
        self.shots.delete(*self.shots.get_children())
        for index, (panel, title, *_) in enumerate(self.rows):
            self.shots.insert("", "end", iid=str(panel), text=f"{index + 1:02d}   {title.get()}")
        if selected:
            self.shots.selection_set(selected)
            self.shots.see(selected)

    def select_scene(self, event):
        if selected := self.shots.selection():
            self.tabs.select(selected[0])

    def move(self, offset):
        panel = self.tabs.select()
        old = self.tabs.index(panel)
        new = old + offset
        if 0 <= new < len(self.rows):
            if panel in self.chapter_panels or str(self.rows[new][0]) in self.chapter_panels:
                return
            self.tabs.insert(new, panel)
            self.rows.insert(new, self.rows.pop(old))
            for index, (tab, *_) in enumerate(self.rows, 1):
                self.tabs.tab(tab, text=f"分镜 {index}")
            self.refresh_scenes()

    def submit(self):
        motion_codes = {label: code for code, label in MOTIONS.items()}
        scenes = []
        for _, title, text, media, zoom, motion in self.rows:
            scene = {"title": title.get().strip(), "text": text.get("1.0", "end-1c").strip(),
                     "media": self.assets[media.get()], "zoom": zoom.get()}
            if motion and (code := motion_codes.get(motion.get())):
                scene["motion"] = code
            scenes.append(scene)
        if any(not 1 <= len(scene["title"]) <= 36 or not 1 <= len(scene["text"]) <= 280 for scene in scenes):
            messagebox.showerror("草稿未保存", "分镜标题需为 1–36 字，口播需为 1–280 字。", parent=self.window)
            return
        try:
            cut = validate_cut({"scenes": scenes}, self.media, episode=self.episode)
        except ValueError as error:
            messagebox.showerror("草稿未保存", str(error), parent=self.window)
            return
        starts = [{"scene": i, "title": self.chapter_panels[str(panel)]} for i, (panel, *_) in enumerate(self.rows)
                  if str(panel) in self.chapter_panels]
        if starts:
            cut["chapter_starts"] = starts
        if self.plan:
            direction = self.direction.get().strip()
            if not 1 <= len(direction) <= 180:
                messagebox.showerror("草稿未保存", "全片主线需为 1–180 字。", parent=self.window)
                return
            cut["editorial_plan"] = {**self.plan, "throughline": direction}
        self.save(cut, self.window.destroy)
