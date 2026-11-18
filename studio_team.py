"""Native review inbox for a shared team workspace."""

import datetime as dt
import tkinter as tk
from queue import Empty, SimpleQueue
from tkinter import ttk

from core.team_client import TeamClient, TeamError
from studio_shell import heading, panel
from studio_theme import ACCENT, AMBER, FONT, MUTED


STATES = {"pending": "待审片", "approved": "已通过", "changes_requested": "需修改", "comment": "留言"}
FILTERS = {"全部状态": "", **{label: state for state, label in STATES.items() if state != "comment"}}


class TeamDesk:
    def __init__(self, parent, names, root_dir, start, play, go_release):
        self.parent, self.names, self.root_dir = parent, names, root_dir
        self.start, self.play_clip = start, play
        self.go_release = go_release
        self.selected, self.current = None, None
        self.detached_selection = False
        self.filters = ("", "")
        self.cursors = ("",)
        self.next_cursor = None
        self.load_version = 0
        self.drafts = {}
        self.previewing = set()
        self.loading = self.saving = False
        self.notice = tk.StringVar(master=parent, value="连接团队后即可查看共享成片。")
        try:
            self.client = TeamClient.load(root_dir)
        except (KeyError, OSError, ValueError, RuntimeError):
            self.client = None
            self.notice.set("团队连接无法读取，请重新连接。")

        header = heading(parent, "团队审片", "共享成片、记录反馈，并核对每一版的审核结果。")
        ttk.Button(header, text="连接团队", command=self.connect).pack(side="right")
        tools = ttk.Frame(parent)
        tools.pack(fill="x", pady=(0, 9))
        ttk.Label(tools, textvariable=self.notice, style="Muted.TLabel").pack(side="left")
        ttk.Button(tools, text="刷新", command=self.refresh).pack(side="right")
        ttk.Button(tools, text="前往发布中心", command=go_release).pack(side="right", padx=6)

        panes = ttk.Panedwindow(parent, orient="horizontal")
        panes.pack(fill="both", expand=True)
        inbox, list_body = panel(panes, padding=10)
        details, detail = panel(panes, padding=12)
        panes.add(inbox, weight=2)
        panes.add(details, weight=3)
        ttk.Label(list_body, text="审片清单", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 9))
        filters = ttk.Frame(list_body, style="Panel.TFrame")
        filters.pack(fill="x", pady=(0, 9))
        filters.columnconfigure(0, weight=1)
        self.search = tk.StringVar(master=parent)
        self.status_filter = tk.StringVar(master=parent, value="全部状态")
        search = ttk.Entry(filters, textvariable=self.search, width=10)
        search.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        search.bind("<Return>", self.apply_filters)
        search_button = ttk.Button(filters, text="搜索", command=self.apply_filters)
        search_button.grid(row=0, column=1, padx=(0, 5))
        status = ttk.Combobox(filters, state="readonly", textvariable=self.status_filter, values=tuple(FILTERS), width=8)
        status.grid(row=0, column=2)
        status.bind("<<ComboboxSelected>>", self.apply_filters)
        ttk.Label(list_body, text="可按成片标题或提交人查找", style="PanelMuted.TLabel").pack(anchor="w", pady=(0, 7))
        listing = ttk.Frame(list_body, style="Panel.TFrame")
        self.queue = ttk.Treeview(listing, columns=("title", "state"), show="headings",
                                  selectmode="browse", height=7)
        for key, text, width in (("title", "成片", 195), ("state", "审核", 65)):
            self.queue.heading(key, text=text)
            self.queue.column(key, width=width, minwidth=55, anchor="w", stretch=key == "title")
        self.queue.tag_configure("approved", foreground=ACCENT)
        self.queue.tag_configure("changes_requested", foreground=AMBER)
        scroll = ttk.Scrollbar(listing, command=self.queue.yview)
        self.queue.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.queue.pack(side="left", fill="both", expand=True)
        self.queue.bind("<<TreeviewSelect>>", self.select)
        paging = ttk.Frame(list_body, style="Panel.TFrame")
        paging.pack(side="bottom", fill="x", pady=(8, 0))
        self.count = ttk.Label(paging, text="等待连接", style="PanelMuted.TLabel")
        self.count.pack(side="left")
        self.next_page = ttk.Button(paging, text="下一页", command=self.page_forward, state="disabled")
        self.next_page.pack(side="right")
        self.previous_page = ttk.Button(paging, text="上一页", command=self.page_back, state="disabled")
        self.previous_page.pack(side="right", padx=(0, 5))
        listing.pack(fill="both", expand=True)

        self.title = ttk.Label(detail, text="选中一条成片开始审片", style="PanelTitle.TLabel", justify="left")
        self.title.pack(fill="x")
        self.info = ttk.Label(detail, style="PanelMuted.TLabel")
        self.info.pack(anchor="w", pady=(3, 9))
        detail.bind("<Configure>", self.wrap_details)
        review_tools = ttk.Frame(detail, style="Panel.TFrame")
        review_tools.pack(fill="x", pady=(0, 8))
        self.play = ttk.Button(review_tools, text="播放这版成片", command=self.preview)
        self.play.pack(side="left")
        self.version_list = ttk.Button(review_tools, text="关联版本", command=self.show_versions)
        self.version_list.pack(side="left", padx=5)
        self.send_next = ttk.Button(review_tools, text="送审新版", command=lambda: self.go_release(self.current))
        self.send_next.pack(side="left")
        notes = ttk.Frame(detail, style="Panel.TFrame")
        notes.pack(fill="both", expand=True, pady=(0, 9))
        self.notes = tk.Text(notes, height=5, width=24, wrap="word", padx=8, pady=8, state="disabled")
        self.notes.tag_configure("heading", foreground=ACCENT, font=(FONT, 10, "bold"))
        self.notes.tag_configure("muted", foreground=MUTED)
        notes_scroll = ttk.Scrollbar(notes, command=self.notes.yview)
        self.notes.configure(yscrollcommand=notes_scroll.set)
        notes_scroll.pack(side="right", fill="y")
        self.notes.pack(side="left", fill="both", expand=True)
        ttk.Label(detail, text="审片反馈", style="PanelMuted.TLabel").pack(anchor="w")
        self.response = tk.Text(detail, height=3, width=24, wrap="word", padx=8, pady=6)
        self.response.pack(fill="x", pady=(4, 8))
        buttons = ttk.Frame(detail, style="Panel.TFrame")
        buttons.pack(fill="x")
        self.actions = []
        for decision, label in (("comment", "留言"), ("changes_requested", "退回修改"), ("approved", "通过")):
            button = ttk.Button(buttons, text=label, command=lambda value=decision: self.send_feedback(value),
                                style="Accent.TButton" if decision == "approved" else "TButton")
            button.pack(side="right", padx=(6, 0))
            self.actions.append(button)
        self.render(None)
        self.parent.after(15000, self.poll)

    def wrap_details(self, event):
        width = max(180, event.width - 12)
        self.title.configure(wraplength=width)
        self.info.configure(wraplength=width)

    def connect(self, after=None):
        if self.saving:
            self.notice.set("请等待正在进行的团队操作完成。")
            return
        dialog = tk.Toplevel(self.parent)
        dialog.title("连接团队审片")
        dialog.transient(self.parent.winfo_toplevel())
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="连接团队", style="Headline.TLabel").pack(anchor="w")
        ttk.Label(body, text="向团队管理员获取服务地址和个人连接码。",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 14))
        ttk.Label(body, text="服务地址").pack(anchor="w")
        address = ttk.Entry(body, width=45)
        address.pack(fill="x", pady=(3, 10))
        if self.client:
            address.insert(0, self.client.address)
        ttk.Label(body, text="个人连接码").pack(anchor="w")
        token = ttk.Entry(body, show="•")
        token.pack(fill="x", pady=(3, 10))
        result = ttk.Label(body, style="Muted.TLabel", wraplength=340)
        result.pack(fill="x", pady=(0, 9))

        def submit():
            try:
                value = token.get()
                if not value and self.client and address.get().strip().rstrip("/") == self.client.address:
                    value = self.client.token
                client = TeamClient(address.get(), value)
            except ValueError as error:
                result.configure(text=str(error))
                return
            button.configure(state="disabled")
            result.configure(text="正在检查连接…")

            def finished(page, error):
                if not dialog.winfo_exists():
                    return
                if error is None:
                    try:
                        client.save(self.root_dir)
                    except (OSError, RuntimeError) as failure:
                        error = failure
                if error:
                    result.configure(text=str(error))
                    button.configure(state="normal")
                    return
                self.client = client
                self.selected, self.current = None, None
                self.detached_selection = False
                self.load_version += 1
                self.loading = False
                self.reset_filters()
                self.filters, self.cursors = ("", ""), ("",)
                self.drafts.clear()
                self.set_response("")
                self.render(None)
                self.update_queue(*page)
                dialog.destroy()
                if after:
                    after()

            self.start(client.review_page, finished)

        button = ttk.Button(body, text="连接", style="Accent.TButton", command=submit)
        button.pack(anchor="e")
        dialog.bind("<Return>", lambda _event: submit() if button.instate(["!disabled"]) else None)
        dialog.grab_set()
        address.focus_set()

    def poll(self):
        if self.parent.winfo_viewable() and self.client and not self.loading:
            self.refresh()
        self.parent.after(15000, self.poll)

    def reset_filters(self):
        self.search.set("")
        self.status_filter.set("全部状态")

    def apply_filters(self, _event=None):
        search = self.search.get().strip()
        if len(search) > 120:
            self.notice.set("搜索词最多 120 字。")
            return
        if not self.client:
            self.notice.set("请先连接团队，再搜索共享成片。")
            return
        self.refresh(filters=(search, FILTERS[self.status_filter.get()]), cursors=("",))

    def page_back(self):
        if len(self.cursors) > 1 and not self.loading:
            self.refresh(cursors=self.cursors[:-1])

    def page_forward(self):
        if self.next_cursor and not self.loading:
            self.refresh(cursors=(*self.cursors, self.next_cursor))

    def refresh(self, preferred=None, *, filters=None, cursors=None):
        client = self.client
        if not client or self.saving:
            return
        filters = self.filters if filters is None else filters
        cursors = self.cursors if cursors is None else cursors
        self.load_version += 1
        version = self.load_version
        self.loading = True
        self.notice.set("正在刷新团队审片…")

        def finished(page, error):
            if client is not self.client or version != self.load_version:
                return
            self.loading = False
            if error:
                self.notice.set(str(error))
                return
            self.filters, self.cursors = filters, cursors
            self.update_queue(*page, preferred=preferred)

        self.start(lambda: client.review_page(*filters, before=cursors[-1]), finished)

    def update_queue(self, items, next_cursor, *, preferred=None):
        selection = preferred or self.selected
        current = self.queue.get_children()
        changed = current != tuple(item["id"] for item in items)
        if changed:
            self.queue.delete(*current)
        for item in items:
            name = self.names.get(item["platform"], item["platform"])
            state = item["status"]
            values = (f"{name}  ·  {item['title']}", STATES[state])
            if changed:
                self.queue.insert("", "end", iid=item["id"], values=values, tags=(state,))
            else:
                self.queue.item(item["id"], values=values, tags=(state,))
        self.next_cursor = next_cursor
        self.next_page.configure(state="normal" if next_cursor else "disabled")
        self.previous_page.configure(state="normal" if len(self.cursors) > 1 else "disabled")
        self.count.configure(text=f"第 {len(self.cursors)} 页 · {len(items)} 条" if items else "没有匹配的成片")
        if not self.saving:
            self.notice.set(f"团队已连接  ·  更新于 {dt.datetime.now():%H:%M}")
        if selection and self.queue.exists(selection):
            self.queue.selection_set(selection)
            if changed or preferred:
                self.queue.see(selection)
            self.select(force=True)
        elif self.detached_selection and selection == self.selected:
            self.load_review(selection, force=True)
        else:
            self.select()

    def select(self, _event=None, *, force=False):
        review_id = next(iter(self.queue.selection()), None)
        if review_id is None and self.detached_selection:
            return
        self.detached_selection = False
        self.load_review(review_id, force=force)

    def load_review(self, review_id, *, force=False):
        if review_id == self.selected and not force:
            return
        if self.selected and self.selected != review_id:
            self.drafts[self.selected] = self.response.get("1.0", "end-1c")
        if self.selected != review_id:
            self.set_response(self.drafts.get(review_id, ""))
            self.render(None)
        self.selected = review_id
        if not review_id or not self.client:
            return
        client = self.client

        def finished(item, error):
            if review_id != self.selected or client is not self.client:
                return
            if error:
                self.notice.set(str(error))
            elif not self.current or item["revision"] > self.current["revision"]:
                self.render(item)
            elif item["revision"] == self.current["revision"]:
                self.current["versions"] = item.get("versions", [])
                self.enable_feedback()

        self.start(lambda: client.review(review_id), finished)

    def show_versions(self):
        versions = self.current.get("versions", []) if self.current else []
        if not versions:
            return
        dialog = tk.Toplevel(self.parent)
        dialog.title("关联版本")
        dialog.transient(self.parent.winfo_toplevel())
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="关联版本", style="Headline.TLabel").pack(anchor="w", pady=(0, 9))
        listing = ttk.Frame(body)
        listing.pack(fill="both", expand=True, pady=(0, 8))
        tree = ttk.Treeview(listing, columns=("relation", "title", "state"), show="headings", height=5,
                            selectmode="browse")
        for key, label, width in (("relation", "关系", 80), ("title", "成片和提交人", 295), ("state", "审核", 76)):
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=55, stretch=key == "title")
        scroll = ttk.Scrollbar(listing, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        for version in versions:
            relation = "上一版" if version["relation"] == "previous" else "后续版本"
            tree.insert("", "end", iid=version["id"], values=(
                relation, f"{version['title']}  ·  {version['author']}", STATES[version["status"]],
            ))

        def open_selected(_event=None):
            selected = tree.selection()
            if selected:
                dialog.destroy()
                self.open_version(selected[0])

        tree.selection_set(versions[0]["id"])
        tree.bind("<Double-1>", open_selected)
        tree.bind("<Return>", open_selected)
        ttk.Button(body, text="打开这版", style="Accent.TButton", command=open_selected).pack(anchor="e")
        dialog.grab_set()
        tree.focus_set()

    def open_version(self, review_id):
        self.detached_selection = not self.queue.exists(review_id)
        if self.detached_selection:
            self.queue.selection_remove(*self.queue.selection())
        else:
            self.queue.selection_set(review_id)
            self.queue.see(review_id)
        self.load_review(review_id)

    def set_response(self, text):
        state = self.response.cget("state")
        self.response.configure(state="normal")
        self.response.delete("1.0", "end")
        self.response.insert("1.0", text)
        self.response.configure(state=state)

    def render(self, item):
        self.current = item
        self.title.configure(text=item["title"] if item else "选中一条成片开始审片")
        if item:
            timestamp = dt.datetime.fromisoformat(item["created_at"]).astimezone().strftime("%m/%d %H:%M")
            self.info.configure(text=f"{STATES[item['status']]}  ·  {item['author']} 提交  ·  {timestamp}")
        else:
            self.info.configure(text="")
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        if item:
            self.notes.insert("end", "发布文案\n", "heading")
            self.notes.insert("end", (item["caption"] or "这版未附文案。") + "\n\n")
            self.notes.insert("end", "审核记录\n", "heading")
            for entry in item["feedback"]:
                timestamp = dt.datetime.fromisoformat(entry["created_at"]).astimezone().strftime("%m/%d %H:%M")
                self.notes.insert("end", f"{entry['author']}  ·  {STATES[entry['decision']]}  ·  {timestamp}\n", "muted")
                if entry["text"]:
                    self.notes.insert("end", entry["text"] + "\n")
                self.notes.insert("end", "\n")
            if not item["feedback"]:
                self.notes.insert("end", "还没有反馈。", "muted")
        self.notes.configure(state="disabled")
        self.play.configure(state="normal" if item and item["id"] not in self.previewing else "disabled")
        self.response.configure(state="normal" if item else "disabled")
        self.enable_feedback()

    def enable_feedback(self):
        self.version_list.configure(state="normal" if self.current and self.current.get("versions") else "disabled")
        self.send_next.configure(state="normal" if self.current and not self.saving else "disabled")
        for button in self.actions:
            button.configure(state="normal" if self.current and not self.saving else "disabled")

    def share(self, platform, package, *, previous_id=None, on_success=None):
        if self.saving:
            return
        if not self.client:
            self.connect(lambda: self.share(platform, package, previous_id=previous_id, on_success=on_success))
            return
        client = self.client
        self.saving = True
        self.enable_feedback()
        self.notice.set("正在校验这版成片…")
        updates = SimpleQueue()
        uploading = True

        def show_progress():
            if not uploading:
                return
            latest = None
            while True:
                try:
                    latest = updates.get_nowait()
                except Empty:
                    break
            if latest:
                sent, total = latest
                message = "正在完成送审…" if sent == total else f"正在上传成片 · {sent * 100 // total}%"
                self.notice.set(message)
            self.parent.after(150, show_progress)

        def finished(review_id, error):
            nonlocal uploading
            uploading = False
            self.saving = False
            self.enable_feedback()
            if error:
                message = str(error)
                if isinstance(error, TeamError) and (error.status is None or error.status >= 500):
                    message += " 可在发布中心重新送审同一成片。"
                self.notice.set(message)
            else:
                self.reset_filters()
                self.refresh(preferred=review_id, filters=("", ""), cursors=("",))
                if on_success:
                    on_success()

        self.start(lambda: client.share(platform, package, previous_id=previous_id,
                                        resume_dir=self.root_dir / "team-cache" / "uploads",
                                        progress=lambda sent, total: updates.put((sent, total))), finished)
        self.parent.after(150, show_progress)

    def preview(self):
        item, client = self.current, self.client
        if not item or not client or item["id"] in self.previewing:
            return
        self.previewing.add(item["id"])
        self.play.configure(state="disabled")
        self.notice.set("正在准备播放这版成片…")

        def finished(path, error):
            self.previewing.discard(item["id"])
            if item["id"] != self.selected or client is not self.client:
                return
            self.play.configure(state="normal")
            if error:
                self.notice.set(str(error))
            else:
                self.notice.set("已打开成片阅读器。")
                self.play_clip({"video": str(path)}, item["title"], item["platform"], None, autoplay=True)

        self.start(lambda: client.download(item, self.root_dir / "team-cache"), finished)

    def send_feedback(self, decision):
        item, client = self.current, self.client
        if not item or not client or self.saving:
            return
        raw = self.response.get("1.0", "end-1c")
        text = raw.strip()
        if not text and decision in {"comment", "changes_requested"}:
            self.notice.set("请先填写审片意见。")
            self.response.focus_set()
            return
        if len(text) > 2000:
            self.notice.set("审片意见最多 2,000 字。")
            return
        self.saving = True
        self.enable_feedback()
        self.notice.set("正在保存审片反馈…")

        def finished(result, error):
            self.saving = False
            self.enable_feedback()
            if error:
                self.notice.set(str(error))
                if isinstance(error, TeamError) and error.status == 409:
                    self.select(force=True)
                return
            if item["id"] == self.selected and self.response.get("1.0", "end-1c") == raw:
                self.set_response("")
            elif self.drafts.get(item["id"]) == raw:
                self.drafts.pop(item["id"], None)
            if item["id"] == self.selected:
                self.render(result)
            self.refresh()

        self.start(lambda: client.feedback(item["id"], item["revision"], decision, text), finished)
