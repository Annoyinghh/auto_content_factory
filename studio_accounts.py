"""Desktop entry to official creator sign-in and paged video management."""

import datetime as dt
import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog, ttk
from urllib.parse import urlsplit

from config import CREATOR_URLS, OUTPUT_DIR
from core.accounts import account_hint, application_fields, begin_login, save_application
from core.desktop_jobs import run_worker


class AccountDesk:
    def __init__(self, parent, names, start, platform="douyin"):
        self.start = start
        self.window = tk.Toplevel(parent)
        self.window.title("内容工厂 · 账号与作品")
        self.window.geometry("790x525")
        self.window.minsize(620, 420)
        self.channels = {name: platform for platform, name in names.items()}
        self.items = {}
        self.next_page = None
        page = ttk.Frame(self.window, padding=18)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="账号与作品", style="Headline.TLabel").pack(anchor="w")
        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=12)
        self.channel = ttk.Combobox(actions, values=list(self.channels), state="readonly", width=12)
        self.channel.set(names[platform])
        self.channel.pack(side="left")
        self.channel.bind("<<ComboboxSelected>>", lambda event: self.change_platform())
        self.sign_in = ttk.Button(actions, text="官方授权登录", command=self.login)
        self.sign_in.pack(side="left", padx=7)
        self.refresh = ttk.Button(actions, text="读取作品", command=lambda: self.load())
        self.refresh.pack(side="left")
        self.settings = ttk.Button(actions, text="应用配置", command=self.configure_application)
        self.settings.pack(side="left", padx=7)
        ttk.Button(actions, text="创作者后台", command=lambda: webbrowser.open(CREATOR_URLS[self.platform])).pack(
            side="right",
        )
        self.status = ttk.Label(page, style="Muted.TLabel", wraplength=585)
        self.status.pack(fill="x", pady=(0, 6))
        self.videos = ttk.Treeview(page, columns=("state",), height=8, selectmode="browse")
        self.videos.heading("#0", text="视频标题")
        self.videos.heading("state", text="平台状态")
        self.videos.column("#0", width=400)
        self.videos.column("state", width=125)
        self.videos.pack(fill="both", expand=True)
        self.videos.bind("<Double-Button-1>", lambda event: self.open_video())
        footer = ttk.Frame(page)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Button(footer, text="打开所选作品", command=self.open_video).pack(side="left")
        self.more = ttk.Button(footer, text="下一页", command=lambda: self.load(self.next_page))
        self.more.pack(side="right")
        self.change_platform()

    @property
    def platform(self):
        return self.channels[self.channel.get()]

    def change_platform(self):
        platform = self.platform
        self.request_token = None
        self.list_account = None
        self.videos.delete(*self.videos.get_children())
        self.items.clear()
        self.next_page = None
        self.more.configure(state="disabled")
        available = platform != "xiaohongshu"
        self.sign_in.configure(state="normal" if available else "disabled")
        self.refresh.configure(state="normal" if available else "disabled")
        self.settings.configure(state="normal" if available else "disabled")
        if available:
            label, _ = account_hint(platform)
            self.status.configure(text=label)
        else:
            self.status.configure(text="小红书作品可在官方创作者后台登录、管理和发布。")

    def login(self):
        platform = self.platform
        try:
            login = begin_login(platform)
        except (ValueError, RuntimeError, OSError) as error:
            messagebox.showerror("请先配置开放平台应用", str(error), parent=self.window)
            return
        webbrowser.open(login["url"])
        callback = simpledialog.askstring(
            "完成官方授权", "在浏览器完成登录后，复制完整回调地址并粘贴到这里。",
            parent=self.window, show="•",
        )
        if not callback:
            return
        self.change_platform()
        self._job("login", platform, {"login": login, "callback": callback}, lambda result: self.load())

    def configure_application(self):
        platform = self.platform
        try:
            client, redirect, partition = application_fields(platform)
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            messagebox.showerror("无法读取应用配置", str(error), parent=self.window)
            return
        dialog = tk.Toplevel(self.window)
        dialog.title(f"{self.channel.get()} · 开放平台应用")
        dialog.transient(self.window)
        dialog.resizable(False, False)
        form = ttk.Frame(dialog, padding=18)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="开放平台应用", style="Headline.TLabel").pack(anchor="w")
        ttk.Label(form, text="填写已获批的应用资料。留空可保留同一应用已保存的密钥。",
                  style="Muted.TLabel", wraplength=425).pack(anchor="w", pady=(0, 8))
        fields = [("应用 ID", client, ""), ("应用密钥", "", "•"), ("登记的回调地址", redirect, "")]
        if platform == "bilibili":
            fields.append(("投稿分区 ID（仅读取作品时可留空）", partition, ""))
        inputs = []
        for label, value, mask in fields:
            ttk.Label(form, text=label).pack(anchor="w")
            entry = ttk.Entry(form, width=55, show=mask)
            entry.insert(0, value)
            entry.pack(fill="x", pady=(2, 8))
            inputs.append(entry)

        def save():
            try:
                save_application(platform, *(entry.get() for entry in inputs))
            except (ValueError, RuntimeError, OSError, KeyError) as error:
                messagebox.showerror("应用配置未保存", str(error), parent=dialog)
                return
            dialog.destroy()
            self.change_platform()
            label, _ = account_hint(platform)
            self.status.configure(text=f"应用配置已保存 · {label}")

        ttk.Button(form, text="保存应用配置", style="Accent.TButton", command=save).pack(anchor="e", pady=(8, 0))
        dialog.bind("<Return>", lambda event: save())
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        dialog.grab_set()
        inputs[0].focus_set()

    def load(self, cursor=0):
        platform = self.platform
        label, account = account_hint(platform)
        if account != self.list_account:
            self.change_platform()
            cursor = 0
        if not account:
            self.status.configure(text=f"{label} · 请先授权登录")
            return
        self._job("videos", platform, {"cursor": cursor, "expected_account": account},
                  lambda result: self.show_results(result, label, account))

    def _job(self, stage, platform, payload, completed):
        token = self.request_token = object()
        self.sign_in.configure(state="disabled")
        self.refresh.configure(state="disabled")
        self.more.configure(state="disabled")
        self.status.configure(text="正在与官方平台连接…")

        def done(result, error):
            if not self.window.winfo_exists() or self.request_token is not token or self.platform != platform:
                return
            self.request_token = None
            if stage == "videos" and account_hint(platform)[1] != payload["expected_account"]:
                self.discard_old_account()
                return
            self.sign_in.configure(state="normal")
            self.refresh.configure(state="normal")
            self.more.configure(state="normal" if self.next_page is not None else "disabled")
            if error:
                self.status.configure(text=str(error))
                return
            completed(result)

        self.start(lambda: run_worker(stage, dt.date.today(), OUTPUT_DIR, platform, payload), done)

    def show_results(self, result, label, account):
        self.videos.delete(*self.videos.get_children())
        self.items.clear()
        for item in result["items"]:
            row = self.videos.insert("", "end", text=item["title"] or "未命名视频", values=(item["status"],))
            self.items[row] = item["url"]
        self.next_page = result["next"]
        self.list_account = account
        self.more.configure(state="normal" if self.next_page is not None else "disabled")
        self.status.configure(text=f"{label} · 本页 {len(self.items)} 个作品")

    def discard_old_account(self):
        self.change_platform()
        self.status.configure(text="账号已切换，请重新读取作品。")

    def open_video(self):
        selected = self.videos.selection()
        if not selected:
            return
        if self.list_account != account_hint(self.platform)[1]:
            self.discard_old_account()
            return
        url = self.items.get(selected[0], "")
        parsed = urlsplit(url)
        allowed = {"www.douyin.com", "v.douyin.com", "www.iesdouyin.com", "www.bilibili.com", "b23.tv"}
        webbrowser.open(url if parsed.scheme == "https" and parsed.netloc in allowed else CREATOR_URLS[self.platform])
