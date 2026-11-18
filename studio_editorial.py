"""Assemble the sources and visual references for one video."""

import re
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk

from core.editorial_link import editorial_link_id
from studio_shell import ScrollArea, panel
from studio_theme import ACCENT, AMBER, FONT, LINE, MUTED, SELECTED, SURFACE, TEXT

_SHORT_DURATIONS = {"1 分钟": 60, "2 分钟": 120, "3 分钟": 180}
_LEGACY_DURATION = "12 分钟"
_MAP = "#eff1f9"
_ROUTE = "#8994b9"
_KINDS = {"report": "报道", "note": "笔记", "asset": "本机素材"}


def _excerpt(row):
    return " ".join(row.get("summary", "").split())[:420]


def _points(row, topic):
    title = re.sub(r"\W+", "", topic.casefold())
    terms = {title[index:index + 2] for index in range(len(title) - 1)}
    sentences = list(dict.fromkeys(
        part.strip()[:280] for part in re.findall(r"[^。！？\n]+[。！？]?", row.get("summary", ""))
        if len(part.strip()) >= 8
    ))
    return sorted(sentences, key=lambda text: sum(term in text.casefold() for term in terms), reverse=True)[:3]


class EditorialPicker:
    def __init__(self, parent, names, confirm, close, stage_team_summary=None, *, read_link=None):
        self.names, self.confirm = names, confirm
        self.stage_team_summary, self.read_link = stage_team_summary, read_link
        self.frame, body = panel(parent, padding=(14, 10))
        self.busy = self.reading = False
        self.options = {}
        self.records, self.links = {}, {}
        self.legacy_duration = False
        self.selected, self.focus, self.focus_quote = [], "", ""
        self.expanded, self.active = set(), ""
        self.branch_buttons, self.queue_buttons = {}, {}
        self.request = object()
        self.message = tk.StringVar(master=body)
        self.query = tk.StringVar(master=body)
        self.input = tk.StringVar(master=body)
        ttk.Style(body).configure("BranchMap.TFrame", background=_MAP)

        header = ttk.Frame(body, style="Panel.TFrame")
        header.pack(fill="x", pady=(0, 8))
        ttk.Button(header, text="返回热点", command=close).pack(side="right")
        tk.Label(header, text="素材组装", bg=SURFACE, fg=TEXT, font=(FONT, 20, "bold")).pack(side="left")
        self.empty = tk.Frame(body, bg=_MAP, highlightbackground=LINE, highlightthickness=1)
        invitation = tk.Frame(self.empty, bg=_MAP)
        invitation.place(relx=.5, rely=.47, anchor="center")
        tk.Label(invitation, text="素材工作区", bg=_MAP, fg=ACCENT, font=(FONT, 9, "bold")).pack()
        tk.Label(invitation, text="从一条选题开始", bg=_MAP, fg=TEXT, font=(FONT, 22, "bold")).pack(pady=(5, 8))
        tk.Label(invitation, text="选题后，关联报道会出现在分支图里。", bg=_MAP, fg=MUTED).pack()
        ttk.Button(invitation, text="去热点选题", style="Accent.TButton", command=close).pack(pady=(14, 0))

        footer = ttk.Frame(body, style="Panel.TFrame")
        footer.pack(side="bottom", fill="x")
        self.status = ttk.Label(footer, textvariable=self.message, style="PanelMuted.TLabel", justify="left")
        self.status.pack(anchor="w", fill="x", pady=(5, 0))
        self._wrap(footer, self.status)
        self.controls = ttk.Frame(footer, style="Panel.TFrame")
        ttk.Label(self.controls, text="平台", style="PanelMuted.TLabel").pack(side="left", padx=(0, 5))
        self.platform = ttk.Combobox(self.controls, values=list(names.values()), state="readonly", width=8)
        self.platform.pack(side="left")
        self.duration = ttk.Combobox(self.controls, values=tuple(_SHORT_DURATIONS), state="readonly", width=7)
        self.platform.bind("<<ComboboxSelected>>", lambda _event: self.controls_state())
        self.action = ttk.Button(self.controls, text="生成试剪", style="Accent.TButton", command=self.submit)
        self.action.pack(side="right")
        self.team_action = ttk.Button(self.controls, text="交给团队算力", command=self.open_team_summary)
        if stage_team_summary:
            self.team_action.pack(side="right", padx=6)

        self.work = ttk.Frame(body, style="Panel.TFrame")
        self.work.columnconfigure(0, weight=3)
        self.work.columnconfigure(1, minsize=234, weight=1)
        self.work.rowconfigure(0, weight=1)
        candidates = ttk.Frame(self.work, style="Panel.TFrame")
        candidates.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        chosen = ttk.Frame(self.work, style="Panel.TFrame")
        chosen.grid(row=0, column=1, sticky="nsew")
        ttk.Label(candidates, text="分支图", style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(candidates, text="虚线候选  ·  实线已接入", style="PanelMuted.TLabel").pack(anchor="w")
        search = ttk.Frame(candidates, style="Panel.TFrame")
        search.pack(fill="x", pady=(5, 6))
        ttk.Label(search, text="搜采集库", style="PanelMuted.TLabel").pack(side="left", padx=(0, 6))
        ttk.Entry(search, textvariable=self.query, width=12).pack(side="left", fill="x", expand=True)
        self.map = self._scroll(candidates, background=_MAP, style="BranchMap.TFrame", padding=(9, 8, 9, 10))
        self.map.body.columnconfigure(1, weight=1)
        self.map.body.bind("<Configure>", self._draw_routes, add="+")

        ttk.Label(chosen, text="交给 AI 的素材", style="PanelTitle.TLabel").pack(anchor="w")
        self.count = ttk.Label(chosen, style="PanelMuted.TLabel")
        self.count.pack(anchor="w")
        self.focus_label = ttk.Label(chosen, style="PanelAccent.TLabel", justify="left")
        self.focus_label.pack(anchor="w", fill="x", pady=(5, 4))
        self._wrap(chosen, self.focus_label)
        selection_tools = ttk.Frame(chosen, style="Panel.TFrame")
        selection_tools.pack(fill="x", pady=(0, 4))
        self.up = ttk.Button(selection_tools, text="上移", command=lambda: self.move(-1))
        self.down = ttk.Button(selection_tools, text="下移", command=lambda: self.move(1))
        self.promote = ttk.Button(selection_tools, text="设为重点", command=self.set_focus)
        self.drop = ttk.Button(selection_tools, text="断开", command=self.disconnect)
        for button in (self.up, self.down, self.promote, self.drop):
            button.pack(side="left", padx=(0, 3))
        extras = ttk.Frame(chosen, style="Panel.TFrame")
        extras.pack(side="bottom", fill="x", pady=(5, 0))
        ttk.Label(extras, text="粘贴报道链接或输入笔记" if read_link else "补充笔记",
                  style="PanelMuted.TLabel").pack(anchor="w")
        input_row = ttk.Frame(extras, style="Panel.TFrame")
        input_row.pack(fill="x", pady=(3, 4))
        self.entry = ttk.Entry(input_row, textvariable=self.input, width=10)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self.add_input)
        self.add_button = ttk.Button(input_row, text="添加", command=self.add_input)
        self.add_button.pack(side="right", padx=(4, 0))
        self.media_button = ttk.Button(extras, text="添加本机图片或视频", command=self.add_files)
        self.media_button.pack(anchor="w")
        self.queue = self._scroll(chosen, background=SURFACE, style="Panel.TFrame", padding=(0, 2, 3, 4))
        self.query.trace_add("write", lambda *_: self.redraw_candidates())
        self.clear()

    @staticmethod
    def _wrap(parent, label, inset=2):
        parent.bind("<Configure>", lambda event: label.configure(wraplength=max(20, event.width - inset)), add="+")

    @staticmethod
    def _scroll(parent, *, background, style, padding):
        area = ScrollArea(parent)
        area.configure(style="Panel.TFrame")
        area.canvas.configure(bg=background, width=1, height=1)
        area.body.configure(style=style, padding=padding)
        area.pack(fill="both", expand=True)
        return area

    def clear(self, message="在热点或采集报道里选择一个选题。"):
        self.request = object()
        self.options = {}
        self.records, self.links = {}, {}
        self.legacy_duration = False
        self.selected, self.focus, self.focus_quote = [], "", ""
        self.expanded, self.active = set(), ""
        self.busy = self.reading = False
        self.message.set(message)
        self.input.set("")
        self.work.pack_forget()
        self.controls.pack_forget()
        self.empty.pack(fill="both", expand=True)
        self.query.set("")
        self.redraw()

    def load(self, options, platform, checked=(), target_seconds=None, brief=None):
        self.clear()
        self.options = options
        self.platform.set(self.names[platform])
        self.legacy_duration = platform == "xiaohongshu" and target_seconds == 720
        self.duration.set(_LEGACY_DURATION if self.legacy_duration else next(
            (label for label, seconds in _SHORT_DURATIONS.items() if seconds == target_seconds), "2 分钟"))
        self.records.update({f"report:{row['id']}": {**row, "kind": "report"} for row in options["reports"]})
        self.links.update({f"report:{row['id']}": {"id": row["id"], "url": row["url"]}
                           for row in options["reports"] if editorial_link_id(row["url"]) == row["id"]})
        brief = brief or {}
        for index, note in enumerate(brief.get("notes", ())):
            self.records[f"note:{index}"] = {"kind": "note", "title": note}
        for index, filename in enumerate(brief.get("asset_paths", ())):
            self.records[f"asset:{index}"] = {"kind": "asset", "title": Path(filename).name, "path": filename}
        initial = brief.get("sequence", [f"report:{key}" for key in checked])
        self.selected = [key for key in dict.fromkeys(initial) if self.available(key)]
        focus = brief.get("focus_id", "")
        self.focus = focus if focus.startswith("note:") else f"report:{focus}" if focus else ""
        if self.focus not in self.selected:
            self.focus = ""
        self.focus_quote = brief.get("focus_quote", "") if self.focus else ""
        self.message.set(options.get("warning") or "接入至少一篇有正文的报道，即可生成试剪。")
        self.empty.pack_forget()
        self.work.pack(fill="both", expand=True)
        self.controls.pack(fill="x", pady=(6, 0))
        self.redraw()

    def available(self, key):
        row = self.records.get(key)
        return bool(row and (row["kind"] != "report" or row.get("available")))

    def redraw(self, active=None):
        self.redraw_queue(active)
        self.redraw_candidates()

    def redraw_queue(self, active=None):
        previous = self.frame.focus_get()
        active = active or self.active
        self.active = active if active in self.selected else self.selected[0] if self.selected else ""
        for child in self.queue.body.winfo_children():
            child.destroy()
        previous_key = next((key for key, button in self.queue_buttons.items() if button == previous), "")
        self.queue_buttons.clear()
        if self.options and not self.selected:
            ttk.Label(self.queue.body, text="展开左边的分支，接入这条视频需要的报道。",
                      style="PanelMuted.TLabel", wraplength=185, justify="left").pack(anchor="w", pady=8)
        for index, key in enumerate(self.selected, 1):
            row = self.records[key]
            bg = SELECTED if key == self.active else SURFACE
            strip = tk.Frame(self.queue.body, bg=bg, highlightbackground=LINE, highlightthickness=1)
            strip.pack(fill="x", pady=(0, 4))
            button = ttk.Button(strip, text=f"{index:02d}", width=3, style="Segment.TButton",
                                command=lambda current=key: self.choose(current))
            button.pack(side="left", padx=(4, 6), pady=4)
            button.state(["selected"] if key == self.active else ["!selected"])
            button.bind("<Delete>", lambda _event, current=key: self.disconnect(current))
            button.bind("<FocusIn>", lambda _event, widget=button: self._see(self.queue, widget))
            self.queue_buttons[key] = button
            copy = tk.Frame(strip, bg=bg)
            copy.pack(side="left", fill="x", expand=True, padx=(0, 5), pady=4)
            kind = _KINDS[row["kind"]] + (" · 重点" if key == self.focus else "")
            kind_label = tk.Label(copy, text=kind, bg=bg, fg=ACCENT, font=(FONT, 8), anchor="w")
            kind_label.pack(fill="x")
            title = row["title"].splitlines()[0]
            label = tk.Label(copy, text=title[:55] + ("…" if len(title) > 55 else ""), bg=bg,
                             fg=TEXT, font=(FONT, 9), anchor="w", justify="left", cursor="hand2")
            label.pack(fill="x")
            self._wrap(copy, label)
            for widget in (strip, copy, kind_label, label):
                widget.bind("<Button-1>", lambda _event, current=key: self.choose(current))
        if previous_key in self.queue_buttons:
            self.queue_buttons[previous_key].focus_set()
        self.controls_state()

    def choose(self, key):
        self.redraw_queue(key)

    def redraw_candidates(self):
        previous = self.frame.focus_get()
        focus_button = next((ref for ref, button in self.branch_buttons.items() if button == previous), None)
        self.branch_buttons.clear()
        for child in self.map.body.winfo_children():
            child.destroy()
        self.edges = None
        self.cards = {}
        if not self.options:
            return
        topic = self.options["anchor"]
        query = self.query.get().strip().casefold()
        suggested = [f"report:{key}" for key in self.options.get("recommended", ())]
        anchor = f"report:{topic.get('id', '')}"
        manual = [key for key, row in self.records.items() if row["kind"] != "report" or key in self.links]
        keys = dict.fromkeys([*self.selected, anchor, *suggested, *manual, *(self.records if query else ())])
        visible = [key for key in keys if key in self.records and (not query or query in (
            self.records[key]["title"] + " " + self.records[key].get("source", "")).casefold())]
        self.topic_node = tk.Frame(self.map.body, bg=ACCENT, padx=10, pady=6)
        self.topic_node.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        tk.Label(self.topic_node, text="当前选题", bg=ACCENT, fg="#e6eaff", font=(FONT, 8, "bold")).pack(anchor="w")
        title = tk.Label(self.topic_node, text=topic["title"], bg=ACCENT, fg="#ffffff",
                         font=(FONT, 13, "bold"), justify="left", anchor="w")
        title.pack(fill="x")
        self._wrap(self.topic_node, title, 20)
        self.edges = tk.Canvas(self.map.body, bg=_MAP, width=25, height=1, bd=0, highlightthickness=0)
        self.edges.grid(row=0, column=0, rowspan=len(visible) + 2, sticky="nsew")
        self.edges.bind("<Configure>", self._draw_routes)
        for index, key in enumerate(visible, 1):
            self._branch(index, key)
        if not visible:
            message = "没有符合搜索条件的报道。" if query else "暂无关联报道。试着搜索采集库，或粘贴一篇报道的链接。"
            empty = tk.Label(self.map.body, text=message, fg=MUTED, bg=_MAP, justify="left", anchor="w")
            empty.grid(row=1, column=1, sticky="ew", pady=10)
            self._wrap(self.map.body, empty, 25)
        if focus_button in self.branch_buttons:
            self.branch_buttons[focus_button].focus_set()
        self.map.after_idle(self._draw_routes)
        self.controls_state()

    def _branch(self, index, key):
        row = self.records[key]
        linked = key in self.selected
        border = "#9ba8f1" if linked else "#cdd3e5"
        card = tk.Frame(self.map.body, bg=SURFACE, padx=8, pady=5,
                        highlightbackground=border, highlightthickness=1)
        card.grid(row=index, column=1, sticky="ew", pady=(0, 6))
        self.cards[key] = card
        header = tk.Frame(card, bg=SURFACE)
        header.pack(fill="x")
        connect = ttk.Button(header, text="断开" if linked else "接入", command=lambda: self.toggle_candidate(key))
        connect.pack(side="right")
        disclose = ttk.Button(header, text="收起" if key in self.expanded else "展开",
                              command=lambda: self.toggle_detail(key))
        disclose.pack(side="right", padx=(4, 4))
        for role, button in (("connect", connect), ("details", disclose)):
            self.branch_buttons[key, role] = button
            button.bind("<FocusIn>", lambda _event, widget=button: self._see(self.map, widget))
        copy = tk.Frame(header, bg=SURFACE)
        copy.pack(fill="x", expand=True)
        status = "已接入" if linked else "候选" if self.available(key) else "待补正文"
        status = f"{_KINDS[row['kind']]}  /  {status}"
        tk.Label(copy, text=status, fg=ACCENT if linked else MUTED, bg=SURFACE,
                 font=(FONT, 8), anchor="w").pack(fill="x")
        headline = tk.Label(copy, text=row["title"].splitlines()[0], fg=TEXT, bg=SURFACE,
                            font=(FONT, 10, "bold"), anchor="w", justify="left", cursor="hand2")
        headline.pack(fill="x")
        self._wrap(copy, headline)
        headline.bind("<Button-1>", lambda _event: self.toggle_detail(key))
        if key in self.expanded:
            self._details(card, key, row)

    def _details(self, card, key, row):
        ttk.Separator(card).pack(fill="x", pady=(6, 5))
        if row["kind"] == "report":
            source = row.get("source") or "来源未标注"
            label = tk.Label(card, text=f"出处 · {source}", fg=MUTED, bg=SURFACE, justify="left", anchor="w")
            label.pack(fill="x")
            self._wrap(card, label, 18)
            if url := row.get("url"):
                ttk.Button(card, text="打开原文", command=lambda: webbrowser.open(url)).pack(anchor="w", pady=(3, 0))
            points = _points(row, self.options["anchor"]["title"])
            caption = "正文摘录" if points else "暂无可用正文摘录。"
            ttk.Label(card, text=caption, style="PanelAccent.TLabel").pack(anchor="w", pady=(6, 2))
            for index, point in enumerate(points, 1):
                excerpt = tk.Frame(card, bg=_MAP, padx=8, pady=4)
                excerpt.pack(fill="x", pady=(0, 4))
                label = tk.Label(excerpt, text=f"{index:02d}  {point}", bg=_MAP, fg=TEXT, anchor="w", justify="left")
                label.pack(fill="x")
                self._wrap(excerpt, label, 18)
                caption = "已设为重点" if self.focus == key and self.focus_quote == point else "用这段作重点"
                if key not in self.selected:
                    caption = "接入并用这段作重点"
                button = ttk.Button(excerpt, text=caption, command=lambda quote=point: self.focus_on_quote(key, quote))
                button.pack(anchor="w", pady=(3, 0))
                self.branch_buttons[key, index] = button
                button.bind("<FocusIn>", lambda _event, widget=button: self._see(self.map, widget))
        else:
            label = tk.Label(card, text=row.get("path", row["title"]), fg=MUTED, bg=SURFACE,
                             justify="left", anchor="w")
            label.pack(fill="x")
            self._wrap(card, label, 18)

    def _draw_routes(self, _event=None):
        if not self.edges or not self.cards:
            return
        y = self.topic_node.winfo_y() + self.topic_node.winfo_height() // 2
        points = [(key, card.winfo_y() + min(card.winfo_height() // 2, 23)) for key, card in self.cards.items()]
        self.edges.delete("all")
        self.edges.create_line(25, y, 9, y, 9, points[-1][1], fill=_ROUTE, width=1)
        for key, center in points:
            if key in self.selected:
                self.edges.create_line(9, center, 25, center, fill=ACCENT, width=2, tags="linked")
                self.edges.create_oval(6, center - 3, 12, center + 3, fill=ACCENT, outline=ACCENT)
            else:
                self.edges.create_line(9, center, 25, center, fill=_ROUTE, dash=(2, 3), width=2, tags="candidate")

    @staticmethod
    def _see(area, widget):
        height = area.body.winfo_height()
        top = widget.winfo_rooty() - area.body.winfo_rooty()
        visible = area.canvas.canvasy(0)
        viewport = area.canvas.winfo_height()
        if top < visible or top + widget.winfo_height() > visible + viewport:
            area.canvas.yview_moveto(max(0, top - 5) / max(height, 1))

    def toggle_detail(self, key):
        if key in self.expanded:
            self.expanded.remove(key)
        else:
            self.expanded.add(key)
        self.redraw_candidates()

    def controls_state(self, _event=None):
        short = self.platform.get() in (self.names["douyin"], self.names["xiaohongshu"])
        durations = tuple(_SHORT_DURATIONS)
        if self.legacy_duration and self.platform.get() == self.names["xiaohongshu"]:
            durations += (_LEGACY_DURATION,)
        self.duration.configure(values=durations)
        if short and self.duration.get() not in durations:
            self.duration.set("2 分钟")
        if short and not self.duration.winfo_manager():
            self.duration.pack(side="left", after=self.platform, padx=(5, 0))
        elif not short:
            self.duration.pack_forget()
        selected = self.active
        index = self.selected.index(selected) if selected in self.selected else -1
        reports = self.report_keys()
        editable = bool(self.options and not self.busy)
        states = (
            (self.action, editable and bool(reports)), (self.team_action, editable and bool(reports)),
            (self.add_button, editable and not self.reading), (self.entry, editable), (self.media_button, editable),
            (self.up, editable and index > 0), (self.down, editable and 0 <= index < len(self.selected) - 1),
            (self.promote, editable and index >= 0 and self.records[selected]["kind"] != "asset"),
            (self.drop, editable and index >= 0),
        )
        for button, enabled in states:
            button.configure(state="normal" if enabled else "disabled")
        for (key, role), button in self.branch_buttons.items():
            if role != "details":
                button.configure(state="normal" if editable and self.available(key) else "disabled")
        self.count.configure(text=f"{len(self.selected)} 项已接入  ·  从上到下按优先级使用")
        title = self.focus_quote or self.records.get(self.focus, {}).get("title", "")
        if title:
            compact = " ".join(title.split())
            self.focus_label.configure(text=f"重点 · {compact[:44]}" + ("…" if len(compact) > 44 else ""))
        else:
            self.focus_label.configure(text="重点 · 尚未指定")

    def toggle_candidate(self, key):
        if not self.busy and self.available(key):
            if key in self.selected:
                self.disconnect(key)
            else:
                self.connect(key)

    def connect(self, key):
        kind = self.records[key]["kind"]
        if sum(self.records[current]["kind"] == kind for current in self.selected) >= 8:
            self.message.set("每类内容最多可接入 8 项。")
            return
        if key not in self.selected:
            self.selected.append(key)
        self.redraw(key)

    def disconnect(self, key=None):
        key = key or self.active
        if not self.busy and key in self.selected:
            self.selected.remove(key)
            if self.focus == key:
                self.focus = ""
                self.focus_quote = ""
            self.redraw()

    def move(self, offset):
        key = self.active
        if self.busy or key not in self.selected:
            return
        index = self.selected.index(key)
        if 0 <= index + offset < len(self.selected):
            self.selected.pop(index)
            self.selected.insert(index + offset, key)
            self.redraw(key)

    def set_focus(self):
        key = self.active
        if not self.busy and key in self.selected and self.records[key]["kind"] != "asset":
            self.focus = key
            self.focus_quote = ""
            self.redraw(key)

    def focus_on_quote(self, key, quote):
        if self.busy or not self.available(key):
            return
        self.connect(key)
        if key in self.selected:
            self.focus, self.focus_quote = key, quote
            self.redraw(key)

    def add_input(self, _event=None):
        value = self.input.get().strip()
        if self.options and not self.busy and not self.reading and value:
            if len(value) > 2000:
                self.message.set("补充内容最多 2000 字。")
            else:
                self.input.set("")
                if self.read_link and re.fullmatch(r"https?://\S+", value, re.IGNORECASE):
                    self.add_link(value)
                else:
                    self.add_record("note", value)
        return "break"

    def add_files(self):
        if not self.options or self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self.frame, title="选择本机图片或视频",
                                          filetypes=[("图片与视频", "*.jpg *.jpeg *.png *.mp4 *.mov *.webm")])
        for name in paths[:8]:
            self.add_record("asset", Path(name).name, path=name)

    def add_record(self, kind, title, **extra):
        for key, row in self.records.items():
            if row["kind"] == kind and row["title"] == title and row.get("path") == extra.get("path"):
                self.connect(key)
                return
        key = f"{kind}:{sum(row['kind'] == kind for row in self.records.values())}"
        self.records[key] = {"kind": kind, "title": title, **extra}
        self.connect(key)

    def add_link(self):
        if not self.options or self.busy or self.reading or not self.read_link:
            return
        url = simpledialog.askstring("读取报道链接", "输入公开报道的 HTTPS 地址。读取后可选择接入。", parent=self.frame)
        if not url or not url.strip():
            return
        request = self.request
        self.reading = True
        self.message.set("正在读取外部报道…")
        self.controls_state()

        def ready(row, error):
            if request is not self.request:
                return
            self.reading = False
            if error:
                self.message.set(str(error))
            else:
                key = f"report:{row['id']}"
                self.records[key] = {**row, "kind": "report"}
                self.links[key] = row
                self.message.set("报道已读入候选分支，可展开核对后接入。")
                self.redraw_candidates()
                if self.table.exists(key):
                    self.table.selection_set(key)
                    self.table.see(key)
            self.controls_state()

        self.read_link(url.strip(), ready)

    def report_keys(self):
        return [key for key in self.selected if key.startswith("report:")]

    def open_team_summary(self):
        if not self.busy and self.stage_team_summary:
            selected = "\n\n".join(_excerpt(self.records[key]) for key in self.report_keys())
            if selected:
                self.stage_team_summary(selected)

    def submit(self):
        reports = self.report_keys()
        if self.busy or not reports:
            return
        platform = next(key for key, name in self.names.items() if name == self.platform.get())
        notes, files, sequence = [], [], []
        aliases = {}
        for key in self.selected:
            row = self.records[key]
            if row["kind"] == "note":
                aliases[key] = f"note:{len(notes)}"
                notes.append(row["title"])
            elif row["kind"] == "asset":
                aliases[key] = f"asset:{len(files)}"
                files.append(row["path"])
            sequence.append(aliases.get(key, key))
        focus = aliases.get(self.focus, self.focus).removeprefix("report:")
        payload = {"anchor": self.options["anchor"], "source_ids": [key.removeprefix("report:") for key in reports],
                   "brief": {"focus_id": focus, "notes": notes, "asset_paths": files, "sequence": sequence,
                             "extra_reports": [self.links[key] for key in reports if key in self.links]}}
        if self.focus_quote and self.focus in reports:
            payload["brief"]["focus_quote"] = self.focus_quote
        if platform in {"douyin", "xiaohongshu"}:
            duration = self.duration.get()
            payload["target_seconds"] = 720 if duration == _LEGACY_DURATION else _SHORT_DURATIONS[duration]
        self.confirm(platform, payload)
