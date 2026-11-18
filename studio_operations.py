"""Desktop workspace for shared account metadata, posting progress and text jobs."""

import datetime as dt
import hashlib
import json
import re
import time
import tkinter as tk
from tkinter import messagebox, ttk
from uuid import uuid4

from core.operations_client import MAX_RESULT, MAX_SUMMARY, OperationsClient, OperationsRunner
from core.team_client import TeamError
from core.workflow import _lock, write_json
from studio_shell import heading
from studio_theme import ACCENT, AMBER


POST_STATES = {
    "planned": "待准备", "prepared": "已准备投稿页", "submitted": "已提交平台",
    "published": "已确认上线", "failed": "失败",
}
JOB_STATES = {"queued": "排队中", "running": "处理中", "completed": "已完成", "failed": "失败"}
JOB_KINDS = {"copy": "发布文案", "storyboard": "制作分镜"}


def table(parent, columns):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[key for key, _, _, _ in columns], show="headings",
                        selectmode="browse", height=4)
    for key, label, width, stretch in columns:
        tree.heading(key, text=label)
        tree.column(key, width=width, minwidth=min(width, 60), stretch=stretch)
    tree.tag_configure("published", foreground=ACCENT)
    tree.tag_configure("completed", foreground=ACCENT)
    tree.tag_configure("failed", foreground=AMBER)
    scroll = ttk.Scrollbar(frame, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    return tree


def selected(tree):
    return next(iter(tree.selection()), None)


def sync_table(tree, rows, preferred=None):
    previous = selected(tree)
    current = tree.get_children()
    changed = current != tuple(row_id for row_id, _, _ in rows)
    if changed:
        tree.delete(*current)
    for row_id, values, tags in rows:
        if changed:
            tree.insert("", "end", iid=row_id, values=values, tags=tags)
        else:
            tree.item(row_id, values=values, tags=tags)
    choice = preferred if preferred and tree.exists(preferred) else previous
    if choice and tree.exists(choice) and selected(tree) != choice:
        tree.selection_set(choice)
        tree.focus(choice)
        tree.see(choice)


def text_box(parent, *, height):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    box = tk.Text(frame, wrap="word", width=24, height=height, padx=8, pady=6, undo=True)
    scroll = ttk.Scrollbar(frame, command=box.yview)
    box.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    box.pack(side="left", fill="both", expand=True)
    return box


def result_archive(directory, client, pending, *, read_history):
    saved, unsaved = {}, {}
    if read_history:
        for path in directory.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                dt.datetime.fromisoformat(item["created_at"])
                if (item["id"] == path.stem and item["kind"] in JOB_KINDS and item["state"] == "completed"
                        and isinstance(item["result"], str) and 0 < len(item["result"]) <= MAX_RESULT):
                    saved[path.stem] = item
            except (KeyError, OSError, TypeError, ValueError):
                continue
    received = set()
    error = None
    for job_id, item in pending:
        if job_id in saved or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            continue
        if item is None:
            try:
                record = client.job(job_id)
                result = record.get("result")
                if (record["id"] != job_id or record["state"] != "completed" or not isinstance(result, str)
                        or not 0 < len(result) <= MAX_RESULT):
                    continue
                item = {key: record[key] for key in ("id", "kind", "state", "created_at", "updated_at", "result")}
                received.add(job_id)
            except (KeyError, TeamError, TypeError) as failure:
                error = str(failure)
                break
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with _lock(directory / ".writes.sqlite3", timeout=5):
                write_json(directory / f"{job_id}.json", item)
        except (OSError, RuntimeError):
            unsaved[job_id] = item
            error = "部分结果已取回，本机保存失败。"
        else:
            saved[job_id] = item
    return saved, unsaved, received, error


class OperationsDesk:
    def __init__(self, parent, names, root_dir, start, go_release=None):
        self.parent, self.names, self.root_dir = parent, names, root_dir
        self.start, self.go_release = start, go_release
        self.client = self.read_request = None
        self.last_refresh = float("-inf")
        self.member = ""
        self.accounts, self.publications, self.jobs = {}, {}, {}
        self.own_accounts = []
        self.saving = self.connecting = self.runner_busy = False
        self.runner = OperationsRunner()
        self.release_ticket = self.pending_prepared = None
        self.archive_dir = self.archive_request = self.displayed_job = None
        self.archive_loaded = False
        self.archive, self.unsaved_results = {}, {}
        self.unread_results = set()
        self.archive_error = None
        self.result_text = ""
        self.draft = ("copy", "")
        self.draft_job = self.composer = None
        self.notice = tk.StringVar(master=parent, value="连接共享运营后即可查看团队账号和任务。")
        try:
            self.client = OperationsClient.load(root_dir)
        except (KeyError, OSError, ValueError, RuntimeError):
            self.notice.set("共享运营连接无法读取，请重新连接。")

        header = heading(parent, "共享运营", "同步矩阵账号、发布进度和文本计算任务。")
        self.connection_button = ttk.Button(header, text="连接共享运营", command=self.connect)
        self.connection_button.pack(side="right")
        tools = ttk.Frame(parent)
        tools.pack(fill="x", pady=(0, 8))
        self.refresh_button = ttk.Button(tools, text="刷新", command=self.refresh)
        self.refresh_button.pack(side="right")
        notice = ttk.Label(tools, textvariable=self.notice, style="Muted.TLabel", justify="left")
        notice.pack(side="left", fill="x", expand=True, padx=(0, 8))
        tools.bind("<Configure>", lambda e: notice.configure(wraplength=max(100, e.width - 72)))
        self.tabs = ttk.Notebook(parent, takefocus=True)
        self.tabs.pack(fill="both", expand=True)
        self.tabs.enable_traversal()
        self.build_matrix()
        self.build_publications()
        self.build_jobs()
        self.controls()
        self.parent.after_idle(lambda: self.refresh(quiet=True))
        self.parent.after(12000, self.poll)

    def tab(self, title, hint):
        page = ttk.Frame(self.tabs, padding=(8, 10, 8, 8))
        self.tabs.add(page, text=title)
        ttk.Label(page, text=hint, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        return page

    def build_matrix(self):
        page = self.tab("共享矩阵", "团队可见账号标签、平台和负责人。各平台的登录和发布授权保留在本机。")
        form = ttk.Frame(page)
        form.pack(fill="x", pady=(0, 8))
        ttk.Label(form, text="平台").pack(side="left")
        self.platform = ttk.Combobox(form, state="readonly", values=tuple(self.names.values()), width=8)
        self.platform.pack(side="left", padx=(6, 12))
        self.platform.current(0)
        ttk.Label(form, text="账号标签").pack(side="left")
        self.account_label = ttk.Entry(form, width=12)
        self.account_label.pack(side="left", fill="x", expand=True, padx=6)
        self.add_account_button = ttk.Button(form, text="登记我的账号", style="Accent.TButton",
                                             command=self.add_account)
        self.add_account_button.pack(side="right")
        self.account_label.bind("<Return>", lambda _e: self.add_account())
        self.account_table = table(page, (("platform", "平台", 100, False), ("label", "账号标签", 240, True),
                                          ("owner", "负责人", 120, True)))
        self.account_count = ttk.Label(page, style="Muted.TLabel")
        self.account_count.pack(anchor="w", pady=(6, 0))

    def build_publications(self):
        page = self.tab("发布进度", "负责人记录平台进度。填好投稿页、提交给平台和确认上线分别记录。")
        form = ttk.Frame(page)
        form.pack(fill="x", pady=(0, 8))
        ttk.Label(form, text="我的账号").pack(side="left")
        self.post_account = ttk.Combobox(form, state="readonly", width=16)
        self.post_account.pack(side="left", padx=(6, 12))
        ttk.Label(form, text="任务标题").pack(side="left")
        self.post_title = ttk.Entry(form, width=12)
        self.post_title.pack(side="left", fill="x", expand=True, padx=6)
        self.add_post_button = ttk.Button(form, text="新建发布任务", style="Accent.TButton",
                                          command=self.add_publication)
        self.add_post_button.pack(side="right")
        self.post_title.bind("<Return>", lambda _e: self.add_publication())
        footer = ttk.Frame(page)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        self.post_count = ttk.Label(footer, style="Muted.TLabel")
        self.post_count.pack(side="left")
        if self.go_release:
            self.release_button = ttk.Button(footer, command=self.open_release)
            self.release_button.pack(side="right")
        self.update_post_button = ttk.Button(footer, text="更新所选状态", command=self.change_status)
        self.update_post_button.pack(side="right", padx=(0, 6))
        self.post_table = table(page, (("title", "任务标题", 210, True), ("account", "账号", 150, True),
                                       ("owner", "负责人", 100, True), ("status", "进度", 126, False)))
        self.post_table.bind("<<TreeviewSelect>>", lambda _e: self.controls())

    def build_jobs(self):
        self.compute_tab = self.tab("计算队列", "提交所选摘要以生成文案或分镜。任务和结果仅提交者可在此查看。")
        tools = ttk.Frame(self.compute_tab)
        tools.pack(fill="x", pady=(0, 6))
        self.new_job_button = ttk.Button(tools, text="新建计算任务", style="Accent.TButton", command=self.compose)
        self.new_job_button.pack(side="left")
        self.worker_enabled = tk.BooleanVar(master=self.parent)
        self.worker_toggle = ttk.Checkbutton(tools, text="允许本机领取团队计算任务", variable=self.worker_enabled,
                                             command=self.toggle_worker)
        self.worker_toggle.pack(side="right")
        self.worker_notice = ttk.Label(self.compute_tab, style="Muted.TLabel",
                                       text="本机领取已关闭 · 开启后使用本机已安装的模型")
        self.worker_notice.pack(anchor="w", pady=(0, 6))
        panes = ttk.Panedwindow(self.compute_tab, orient="horizontal")
        panes.pack(fill="both", expand=True)
        listing = ttk.Frame(panes, padding=(0, 0, 6, 0))
        details = ttk.Frame(panes, padding=(6, 0, 0, 0))
        panes.add(listing, weight=2)
        panes.add(details, weight=3)
        self.job_table = table(listing, (("kind", "类型", 82, True), ("status", "状态", 75, False),
                                         ("time", "提交时间", 104, True)))
        self.job_table.bind("<<TreeviewSelect>>", lambda _e: self.show_job())
        self.compute_tab.bind("<Map>", lambda _e: self.show_job())
        caption = ttk.Frame(details)
        caption.pack(fill="x", pady=(0, 4))
        self.copy_result_button = ttk.Button(caption, text="复制结果", command=self.copy_result, state="disabled")
        self.copy_result_button.pack(side="right")
        ttk.Label(caption, text="任务结果", style="Headline.TLabel").pack(side="left")
        self.result = text_box(details, height=5)
        self.result.configure(undo=False)
        self.show_result("计算任务提交后会在这里显示进度和结果。")

    def account_name(self, platform, label):
        return f"{self.names.get(platform, platform)} · {label}"

    def controls(self):
        available = self.client is not None and not (self.saving or self.connecting)
        item = self.owned_publication()
        for button, active in (
            (self.connection_button, not (self.connecting or self.saving)),
            (self.refresh_button, available and self.read_request is None),
            (self.add_account_button, available and bool(self.member)),
            (self.add_post_button, available and bool(self.own_accounts)),
            (self.update_post_button, available and item is not None),
            (self.new_job_button, available and bool(self.member)),
            (self.worker_toggle, self.client is not None and bool(self.member) and not self.connecting),
        ):
            button.state(["!disabled" if active else "disabled"])
        if self.go_release:
            linked = item and item["status"] in {"planned", "failed"}
            self.release_button.configure(text="准备所选任务" if linked else "打开本机发布中心")

    def owned_publication(self):
        item = self.publications.get(selected(self.post_table))
        return item if item and item["owner"] == self.member else None

    def dialog(self, title):
        dialog = tk.Toplevel(self.parent)
        dialog.title(title)
        dialog.transient(self.parent.winfo_toplevel())
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=title, style="Headline.TLabel").pack(anchor="w", pady=(0, 8))
        dialog.grab_set()
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        return dialog, body

    def connect(self):
        dialog, body = self.dialog("连接共享运营")
        dialog.resizable(False, False)
        ttk.Label(body, text="向管理员获取共享运营服务地址和个人连接码。",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(body, text="服务地址").pack(anchor="w")
        address = ttk.Entry(body, width=45)
        address.pack(fill="x", pady=(3, 8))
        if self.client:
            address.insert(0, self.client.address)
        ttk.Label(body, text="个人连接码").pack(anchor="w")
        token = ttk.Entry(body, show="•")
        token.pack(fill="x", pady=(3, 8))
        feedback = ttk.Label(body, style="Muted.TLabel", wraplength=380)
        feedback.pack(fill="x", pady=(0, 8))

        def submit():
            if self.connecting:
                return
            try:
                code = token.get()
                if not code and self.client and address.get().strip().rstrip("/") == self.client.address:
                    code = self.client.token
                client = OperationsClient(address.get(), code)
            except ValueError as error:
                feedback.configure(text=str(error))
                return
            self.connecting = True
            self.controls()
            button.state(["disabled"])
            feedback.configure(text="正在检查连接…")

            def done(snapshot, error):
                self.connecting = False
                if dialog.winfo_exists() and error is None:
                    try:
                        client.save(self.root_dir)
                    except (OSError, RuntimeError, ValueError) as failure:
                        error = failure
                    else:
                        self.client = client
                        self.read_request = self.archive_request = self.displayed_job = None
                        self.release_ticket = self.pending_prepared = self.draft_job = None
                        self.worker_enabled.set(False)
                        self.toggle_worker()
                        for tree in (self.account_table, self.post_table, self.job_table):
                            tree.delete(*tree.get_children())
                        self.apply_snapshot(snapshot)
                        dialog.destroy()
                if error and dialog.winfo_exists():
                    feedback.configure(text=str(error))
                    button.state(["!disabled"])
                self.controls()
                self.send_prepared()

            self.start(client.snapshot, done)

        button = ttk.Button(body, text="连接", style="Accent.TButton", command=submit)
        button.pack(anchor="e")
        dialog.bind("<Return>", lambda _e: submit())
        address.focus_set()

    def refresh(self, *, preferred=None, message=None, quiet=False):
        client = self.client
        if not client or self.read_request or self.saving or self.connecting:
            return
        request = self.read_request = object()
        self.last_refresh = time.monotonic()
        if not quiet:
            self.notice.set("正在读取共享运营…")
        self.controls()

        def done(snapshot, error):
            if request is not self.read_request:
                return
            self.read_request = None
            if error:
                self.notice.set(f"{message} · 刷新失败" if message else str(error))
            else:
                self.apply_snapshot(snapshot, preferred)
                if message:
                    self.notice.set(message)
            self.controls()

        self.start(client.snapshot, done)

    def apply_snapshot(self, snapshot, preferred=None):
        self.member = snapshot["member"]["name"]
        self.accounts = {item["id"]: item for item in snapshot["accounts"]}
        self.publications = {item["id"]: item for item in snapshot["publications"]}
        self.jobs = {item["id"]: item for item in snapshot["jobs"]}
        self.notice.set(f"共享运营已连接  ·  {self.member}")
        sync_table(self.account_table, [
            (item["id"], (self.names.get(item["platform"], item["platform"]), item["label"], item["owner"]), ())
            for item in self.accounts.values()
        ], preferred)
        self.account_count.configure(text=f"共享账号 {len(self.accounts)} 个" if self.accounts else "还没有登记账号。")
        current = self.post_account.current()
        account_id = self.own_accounts[current]["id"] if 0 <= current < len(self.own_accounts) else None
        self.own_accounts = [item for item in self.accounts.values() if item["owner"] == self.member]
        self.post_account.configure(values=[self.account_name(item["platform"], item["label"])
                                            for item in self.own_accounts])
        if self.own_accounts:
            self.post_account.current(next((i for i, item in enumerate(self.own_accounts)
                                            if item["id"] == account_id), 0))
        else:
            self.post_account.set("")
        sync_table(self.post_table, [
            (item["id"], (item["title"], self.account_name(item["platform"], item["account_label"]),
                          item["owner"], POST_STATES[item["status"]]), (item["status"],))
            for item in self.publications.values()
        ], preferred)
        self.post_count.configure(text=f"最近 {len(self.publications)} 条任务" if self.publications else "还没有发布任务。")
        scope = hashlib.sha256(f"{self.client.address}\0{self.member}".encode()).hexdigest()
        directory = self.root_dir / "operations" / "results" / scope
        if directory != self.archive_dir:
            self.archive_dir = directory
            self.archive_request = None
            self.archive_loaded = False
            self.archive.clear()
            self.unsaved_results.clear()
            self.unread_results.clear()
            self.archive_error = None
        self.render_jobs(preferred)
        self.sync_results()
        self.controls()

    def render_jobs(self, preferred=None):
        rows = {**self.archive, **self.unsaved_results, **self.jobs}
        rows = sorted(rows.values(), key=lambda item: item["created_at"], reverse=True)
        job_choice = preferred or selected(self.job_table)
        if not any(item["id"] == job_choice for item in rows):
            job_choice = rows[0]["id"] if rows else None
        sync_table(self.job_table, [
            (item["id"], (JOB_KINDS[item["kind"]],
                          "本机结果" if item["id"] not in self.jobs else JOB_STATES[item["state"]],
                          dt.datetime.fromisoformat(item["created_at"]).astimezone().strftime("%m/%d %H:%M")),
             (item["state"],))
            for item in rows
        ], job_choice)
        self.show_job()

    def results_to_fetch(self):
        return {item["id"]: None for item in self.jobs.values()
                if item["state"] == "completed" and item["id"] not in self.archive}

    def sync_results(self):
        client, directory = self.client, self.archive_dir
        if not client or not directory or self.archive_request:
            return
        read_history = not self.archive_loaded
        pending = {**self.results_to_fetch(), **self.unsaved_results}
        if not pending and not read_history:
            return
        first = selected(self.job_table)
        batch = sorted(pending.items(), key=lambda entry: entry[0] != first)[:8]
        request = self.archive_request = object()

        def done(value, error):
            if request is not self.archive_request:
                return
            self.archive_request = None
            if error:
                self.archive_error = "暂时无法读取本机计算结果，稍后重试。"
            else:
                saved, unsaved, received, self.archive_error = value
                self.archive_loaded = True
                self.archive.update(saved)
                self.unsaved_results.update(unsaved)
                for job_id in saved:
                    self.unsaved_results.pop(job_id, None)
                self.unread_results.update(received)
            self.render_jobs()

        self.start(lambda: result_archive(directory, client, batch, read_history=read_history), done)

    def save_change(self, action, message, *, completed=None, failed=None):
        if not self.client or self.saving or self.connecting:
            return False
        self.saving = True
        self.read_request = None
        self.notice.set("正在保存共享运营记录…")
        self.controls()

        def done(item, error):
            self.saving = False
            self.controls()
            if error:
                self.notice.set(str(error))
                if failed:
                    failed(error)
                if getattr(error, "status", None) == 409:
                    self.refresh(message=str(error), quiet=True)
            else:
                if completed:
                    completed()
                self.refresh(preferred=item["id"], message=message, quiet=True)
            self.send_prepared()

        self.start(action, done)
        return True

    def add_account(self):
        client = self.client
        if self.add_account_button.instate(["disabled"]):
            return
        label = self.account_label.get().strip()
        if not 0 < len(label) <= 120:
            self.notice.set("请输入 1 至 120 字的账号标签。")
            self.account_label.focus_set()
            return
        platform = tuple(self.names)[self.platform.current()]
        self.save_change(lambda: client.add_account(platform, label), "账号已登记",
                         completed=lambda: self.clear_entry(self.account_label, label))

    @staticmethod
    def clear_entry(entry, value):
        if entry.get().strip() == value:
            entry.delete(0, "end")

    def add_publication(self):
        client = self.client
        index = self.post_account.current()
        if self.add_post_button.instate(["disabled"]) or index < 0:
            return
        title = self.post_title.get().strip()
        if not 0 < len(title) <= 300:
            self.notice.set("请输入 1 至 300 字的任务标题。")
            self.post_title.focus_set()
            return
        account_id = self.own_accounts[index]["id"]
        self.save_change(lambda: client.add_publication(account_id, title), "发布任务已创建",
                         completed=lambda: self.clear_entry(self.post_title, title))

    def change_status(self):
        if self.update_post_button.instate(["disabled"]):
            return
        client = self.client
        item = self.owned_publication()
        if not item:
            return
        dialog, body = self.dialog("更新发布进度")
        dialog.resizable(False, False)
        ttk.Label(body, text=item["title"], wraplength=400).pack(anchor="w", pady=(0, 8))
        ttk.Label(body, text="已准备投稿页表示已填好平台资料。请在平台提交后选“已提交平台”，确认作品上线后选“已确认上线”。",
                  wraplength=400, style="Muted.TLabel", justify="left").pack(anchor="w", pady=(0, 8))
        state = ttk.Combobox(body, values=tuple(POST_STATES.values()), state="readonly")
        state.set(POST_STATES[item["status"]])
        state.pack(fill="x", pady=(0, 8))

        def submit():
            status = tuple(POST_STATES)[state.current()]
            if status == item["status"]:
                dialog.destroy()
                return
            if self.save_change(lambda: client.set_publication(item["id"], item["revision"], status),
                                "发布进度已更新"):
                dialog.destroy()

        ttk.Button(body, text="保存进度", style="Accent.TButton", command=submit).pack(anchor="e")
        dialog.bind("<Return>", lambda _e: submit())
        state.focus_set()

    def open_release(self):
        item = self.owned_publication()
        if not item or item["status"] not in {"planned", "failed"}:
            item = None
        self.release_ticket = (self.client, item) if item else None
        self.go_release(item)

    def record_prepared(self, publication):
        if (self.release_ticket != (self.client, publication) or publication is None
                or publication["status"] not in {"planned", "failed"}):
            return False
        self.release_ticket = None
        self.pending_prepared = (self.client, publication)
        self.send_prepared()
        return True

    def send_prepared(self):
        if self.pending_prepared and not (self.saving or self.connecting):
            client, item = self.pending_prepared
            self.pending_prepared = None
            if client is self.client:
                self.save_change(lambda: client.set_publication(item["id"], item["revision"], "prepared"),
                                 "已记录投稿页准备完成")

    def stage_summary(self, summary, kind="storyboard"):
        self.tabs.select(self.compute_tab)
        if self.draft[1] and self.draft != (kind, summary) and not messagebox.askyesno(
            "替换待提交摘要", "计算队列中已有一份未提交的摘要。用这次选中的摘要替换它？", parent=self.parent,
        ):
            self.compose()
            return
        self.draft = (kind, summary)
        self.compose()

    def compose(self):
        if self.composer and self.composer.winfo_exists():
            self.composer.lift()
            return
        dialog, body = self.dialog("新建计算任务")
        self.composer = dialog
        dialog.geometry("620x380")
        dialog.minsize(480, 320)
        kind, summary = self.draft
        hint = ttk.Label(body, style="Muted.TLabel", wraplength=570, justify="left")
        hint.pack(fill="x", pady=(0, 8))
        kind_row = ttk.Frame(body)
        kind_row.pack(fill="x", pady=(0, 8))
        ttk.Label(kind_row, text="任务类型").pack(side="left", padx=(0, 8))
        kind_choice = ttk.Combobox(kind_row, values=tuple(JOB_KINDS.values()), state="readonly", width=13)
        kind_choice.set(JOB_KINDS[kind])
        kind_choice.pack(side="left")
        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        feedback = ttk.Label(body, style="Muted.TLabel", wraplength=570)
        feedback.pack(side="bottom", fill="x", pady=(5, 0))
        box = text_box(body, height=6)
        box.insert("1.0", summary)
        preview = None
        sending = False

        def close():
            if not sending:
                self.draft = (tuple(JOB_KINDS)[kind_choice.current()], box.get("1.0", "end-1c"))
                dialog.destroy()

        def edit():
            nonlocal preview
            preview = None
            hint.configure(text=f"编辑这次要发送的选题摘要（最多 {MAX_SUMMARY} 字）。请将素材、文件路径和平台登录资料留在本机。")
            box.configure(state="normal")
            kind_choice.configure(state="readonly")
            confirm.configure(text="预览将发送的摘要")
            back.configure(text="关闭", command=close)
            feedback.configure(text="")
            box.focus_set()

        def proceed():
            nonlocal preview, sending
            if sending:
                return
            if preview is None:
                text = box.get("1.0", "end-1c").strip()
                if not 0 < len(text) <= MAX_SUMMARY:
                    feedback.configure(text=f"请输入 1 至 {MAX_SUMMARY} 字的摘要。")
                    return
                preview = (tuple(JOB_KINDS)[kind_choice.current()], text)
                box.delete("1.0", "end")
                box.insert("1.0", text)
                box.configure(state="disabled")
                kind_choice.configure(state="disabled")
                hint.configure(text="确认发送以下摘要和任务类型。共享运营服务会将摘要交给已开启领取的团队电脑处理。")
                confirm.configure(text="确认发送摘要")
                back.configure(text="返回编辑", command=edit)
                feedback.configure(text="")
                return
            client = self.client
            if not client or not self.member:
                feedback.configure(text="请先连接共享运营，再提交摘要。关闭后会保留这份草稿。")
                return
            if self.draft_job and self.draft_job[:3] == (client, *preview):
                job_id = self.draft_job[3]
            else:
                job_id = uuid4().hex
                self.draft_job = (client, *preview, job_id)
            job_kind, text = preview

            def done():
                self.draft = (job_kind, "")
                self.draft_job = None
                dialog.destroy()

            def failed(error):
                nonlocal sending
                sending = False
                confirm.state(["!disabled"])
                back.state(["!disabled"])
                feedback.configure(text=str(error))

            if self.save_change(lambda: client.submit_job(job_id, job_kind, text), "计算任务已提交",
                                completed=done, failed=failed):
                sending = True
                confirm.state(["disabled"])
                back.state(["disabled"])
                feedback.configure(text="正在提交…")

        confirm = ttk.Button(footer, style="Accent.TButton", command=proceed)
        confirm.pack(side="right")
        back = ttk.Button(footer)
        back.pack(side="right", padx=(0, 6))
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _e: close())
        body.bind("<Configure>", lambda e: (hint.configure(wraplength=max(200, e.width - 32)),
                                             feedback.configure(wraplength=max(200, e.width - 32))))
        edit()

    def show_result(self, text, *, copyable=False):
        self.result_text = text if copyable else ""
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.configure(state="disabled")
        self.copy_result_button.state(["!disabled" if copyable else "disabled"])

    def copy_result(self):
        if self.result_text:
            self.parent.clipboard_clear()
            self.parent.clipboard_append(self.result_text)
            self.notice.set("计算结果已复制")

    def show_job(self):
        job_id = selected(self.job_table)
        result = self.archive.get(job_id) or self.unsaved_results.get(job_id)
        item = self.jobs.get(job_id)
        if result:
            text = result["result"]
            if self.compute_tab.winfo_viewable():
                self.unread_results.discard(job_id)
        elif not item:
            text = "选择一条计算任务查看进度和结果。" if self.jobs or self.archive else "还没有计算任务。"
        else:
            text = {
                "queued": "正在等待已开启领取的团队电脑。",
                "running": "团队电脑正在生成结果。",
                "failed": item.get("error") or "本次任务生成失败。",
                "completed": self.archive_error or "正在取回任务结果…",
            }[item["state"]]
        count = len(self.unread_results)
        self.tabs.tab(self.compute_tab, text=f"计算队列 · {count}" if count else "计算队列")
        key = (job_id, text, bool(result))
        if key != self.displayed_job:
            self.displayed_job = key
            self.show_result(text, copyable=bool(result))
        if result and job_id in self.unsaved_results:
            self.notice.set("结果已取回，本机保存失败。可复制结果或等待重试。")

    def toggle_worker(self):
        if self.worker_enabled.get():
            self.worker_notice.configure(text="本机领取已开启 · 应用打开时检查队列")
            self.run_worker()
        else:
            text = ("本机领取已关闭 · 进行中的任务会继续完成" if self.runner_busy
                    else "本机领取已关闭 · 开启后使用本机已安装的模型")
            self.worker_notice.configure(text=text)

    def run_worker(self):
        client = self.client
        if not client or not self.worker_enabled.get() or self.runner_busy or self.connecting:
            return
        self.runner_busy = True

        def done(worked, error):
            self.runner_busy = False
            if not self.worker_enabled.get():
                self.toggle_worker()
            elif client is self.client:
                text = "本机领取已开启 · 应用打开时检查队列"
                self.worker_notice.configure(text="本机暂时无法领取任务，稍后重试。" if error else text)
                if worked and self.parent.winfo_viewable():
                    self.refresh(quiet=True)

        self.start(lambda: self.runner.run_once(client, enabled=True), done)

    def poll(self):
        self.run_worker()
        pending = any(item["state"] in {"queued", "running"} for item in self.jobs.values())
        visible_or_connecting = self.parent.winfo_viewable() or not self.member
        if pending or (visible_or_connecting and time.monotonic() - self.last_refresh >= 60):
            self.refresh(quiet=True)
        self.sync_results()
        self.parent.after(12000, self.poll)
