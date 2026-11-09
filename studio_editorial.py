"""The report chooser embedded in the production workspace."""

import datetime as dt
import tkinter as tk
from tkinter import ttk

from studio_shell import panel


class EditorialPicker:
    def __init__(self, parent, names, confirm, close):
        self.names = names
        self.confirm = confirm
        self.frame, body = panel(parent, padding=(12, 10))
        toolbar = ttk.Frame(body, style="Panel.TFrame")
        toolbar.pack(fill="x")
        self.title = ttk.Label(toolbar, style="PanelTitle.TLabel", wraplength=480)
        self.title.pack(side="left", fill="x", expand=True)
        ttk.Button(toolbar, text="返回稿件", command=close).pack(side="right")
        controls = ttk.Frame(body, style="Panel.TFrame")
        controls.pack(fill="x", pady=7)
        self.platform = ttk.Combobox(controls, values=list(names.values()), state="readonly", width=8)
        self.platform.pack(side="left")
        self.query = tk.StringVar()
        ttk.Label(controls, text="搜索报道", style="PanelMuted.TLabel").pack(side="left", padx=(12, 5))
        ttk.Entry(controls, textvariable=self.query, width=20).pack(side="left", fill="x", expand=True)
        self.action = ttk.Button(controls, text="自动生成试剪", style="Accent.TButton", command=self.submit)
        self.action.pack(side="right", padx=(10, 0))
        rows = ttk.Frame(body, style="Panel.TFrame")
        rows.pack(fill="both", expand=True)
        columns = (("selected", "勾选", 57), ("title", "报道标题", 265), ("source", "来源", 95),
                   ("time", "发布时间", 100), ("ready", "正文", 66))
        self.table = ttk.Treeview(rows, show="headings", columns=[c[0] for c in columns],
                                  selectmode="browse", height=8)
        for key, title, width in columns:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=45, stretch=key == "title")
        bar = ttk.Scrollbar(rows, command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)
        self.table.bind("<ButtonRelease-1>", self.click)
        self.table.bind("<space>", self.toggle_focus)
        self.table.bind("<<TreeviewSelect>>", self.show_excerpt)
        self.query.trace_add("write", lambda *_: self.redraw())
        self.note = ttk.Label(body, style="PanelMuted.TLabel", wraplength=650)
        self.note.pack(anchor="w", pady=(6, 0))
        self.excerpt = ttk.Label(body, style="PanelMuted.TLabel", wraplength=650, justify="left")
        self.excerpt.pack(anchor="w", pady=(3, 0))
        self.options = {}
        self.rows = {}
        self.checked = {}
        self.busy = False

    def load(self, options, platform, checked=()):
        self.options = options
        self.rows = {report["id"]: report for report in options.get("reports", [])}
        self.checked = dict.fromkeys(key for key in checked if key in self.rows)
        self.busy = False
        self.title.configure(text=options.get("anchor", {}).get("title", "选择本期报道"))
        self.platform.set(self.names[platform])
        self.query.set("")
        self.redraw()

    def redraw(self):
        current = self.table.focus()
        self.table.delete(*self.table.get_children())
        query = self.query.get().strip().casefold()
        recommended = self.options.get("recommended", [])
        keys = dict.fromkeys([*self.checked, *recommended, *self.rows])
        for key in keys:
            row = self.rows[key]
            if query not in f"{row['title']} {row['source']} {row['summary']}".casefold():
                continue
            stamp = dt.datetime.fromisoformat(row["published"]).astimezone().strftime("%m/%d %H:%M")
            title = f"推荐 · {row['title']}" if key in recommended else row["title"]
            check = "✓" if key in self.checked else "□" if row["available"] else "—"
            self.table.insert("", "end", iid=key, values=(check, title, row["source"], stamp,
                                                         "可用" if row["available"] else "待补"))
        if current and self.table.exists(current):
            self.table.focus(current)
            self.table.selection_set(current)
        self.update_note()

    def click(self, event):
        if self.table.identify_column(event.x) == "#1":
            self.toggle(self.table.identify_row(event.y))

    def toggle_focus(self, event):
        self.toggle(self.table.focus())
        return "break"

    def toggle(self, key):
        if not self.busy and key in self.rows and self.rows[key]["available"]:
            if key in self.checked:
                del self.checked[key]
            elif len(self.checked) < 8:
                self.checked[key] = None
            self.table.set(key, "selected", "✓" if key in self.checked else "□")
            self.update_note()

    def show_excerpt(self, _event=None):
        row = self.rows.get(self.table.focus(), {})
        self.excerpt.configure(text=" ".join(row.get("summary", "").split())[:140])

    def update_note(self):
        count = len(self.checked)
        warning = self.options.get("warning", "")
        self.note.configure(text=f"已勾选 {count} 篇 · 点击第一列或按空格勾选 2–8 篇。{warning}")
        self.action.configure(state="normal" if 2 <= count <= 8 and not self.busy else "disabled")

    def submit(self):
        if not self.busy and 2 <= len(self.checked) <= 8:
            platform = next(key for key, name in self.names.items() if name == self.platform.get())
            self.confirm(platform, {"anchor": self.options["anchor"], "source_ids": list(self.checked)})
