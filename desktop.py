"""Windows operator console and headless daily entry point."""

import datetime as dt
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from functools import cached_property
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from config import APP_ICON, BASE_DIR, DATA_DIR, OUTPUT_DIR, PLATFORM_PRESETS, PROFILES, get_ffmpeg
from core.desktop_jobs import desktop_command, run_worker
from core.douyin_profiles import douyin_profiles, profile_directory, save_profile
from core.intake import REFRESH_MINUTES, read_intake
from core.media_review import usable_media
from core.workflow import MANUAL_TITLE_LIMIT, edition_path, read_edition, release_directory, run_hot_daily, run_stage
from studio_dashboard import OperationsDashboard, recent_editions
from studio_departments import IntakeDesk, ReleaseDesk, prepare_intake_view
from studio_editorial import EditorialPicker
from studio_editor import StoryboardTimeline
from studio_library import MediaLibrary, MatrixPage, WorksLibrary, load_thumbnail
from studio_shell import SCREENING, ScrollArea, WorkbenchShell, heading, panel
from studio_theme import AMBER, FIELD, TEXT, apply_theme, icon


NAMES = {"xiaohongshu": "小红书", "douyin": "抖音", "bilibili": "B站"}
PUBLISHERS = {"douyin", "bilibili"}
STATUS = {"ready": "待审片", "pending": "等待制作", "failed": "制作失败"}
TASK_NAME = "AutoContentFactoryDaily"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def schedule_command(*args):
    return subprocess.run(
        ["schtasks.exe", *args, "/TN", TASK_NAME],
        capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW, timeout=20,
    )


def scheduled_action(flag):
    command = desktop_command()
    if not getattr(sys, "frozen", False):
        command[0] = str(Path(command[0]).with_name("pythonw.exe"))
    return subprocess.list2cmdline([*command, flag])


class Desk:
    def __init__(self, root):
        self.root = root
        self.jobs = queue.Queue()
        self.readers = []
        self.post_panel = None
        self.operations_task = None
        try:
            self.douyin_accounts = douyin_profiles(DATA_DIR)
        except (OSError, ValueError) as error:
            self.douyin_accounts = {}
            root.after_idle(lambda message=str(error): messagebox.showerror("无法读取抖音账号", message, parent=root))
        self.edition = {}
        self.busy = False
        self.active = set()
        self.publishing = False
        self.collecting = False
        self.intake_request = self.intake_applied = 0
        self.collection_done = threading.Event()
        self.collection_done.set()
        self.cover = None
        self.task_enabled = None
        root.title("热采放映室 · 内容运营")
        root.geometry(f"{min(1357, int(root.winfo_screenwidth() * .94))}x{min(871, int(root.winfo_screenheight() * .88))}")
        root.minsize(960, 620)
        apply_theme(root)
        if sys.platform == "win32":
            root.iconbitmap(default=str(APP_ICON))
        else:
            self.brand_icon = icon(root, "brand")
            root.iconphoto(True, self.brand_icon)
        self.shell = WorkbenchShell(root, self.show_department, self.search_hotspots)
        self.date = self.shell.date
        self.date.bind("<<ComboboxSelected>>", lambda event: self.show_edition())
        stage = self.shell.workspace
        stage.columnconfigure(0, weight=1)
        stage.rowconfigure(0, weight=1)
        self.sections = {}
        for key in ("trends", "production", "release", "accounts", "team", "operations"):
            self.sections[key] = ttk.Frame(stage, padding=(24, 20, 24, 16))
        topic_area, production, release = (self.sections[key] for key in ("trends", "production", "release"))
        self.topic_tabs = ttk.Notebook(topic_area)
        self.topic_tabs.pack(fill="both", expand=True)
        trends, collection, brief = (ttk.Frame(self.topic_tabs, padding=(0, 10, 0, 0)) for _ in range(3))
        self.topic_views = {"trends": trends, "collection": collection, "brief": brief}
        for view, title in ((trends, "平台热点"), (collection, "采集报道"), (brief, "素材组装")):
            self.topic_tabs.add(view, text=title)
        self.intake = IntakeDesk(collection, trends, NAMES, self.start_editorial, self.open_hotspot_writing)
        self.release = ReleaseDesk(release, NAMES, PUBLISHERS, self.open_accounts, self.load_editions,
                                   lambda platform, payload: self.launch("hold", platform, payload), self.publish_batch,
                                   self.prepare_selected_post, self.select_work_date, self.share_for_review)
        self.release.set_douyin_profiles(self.douyin_accounts)
        self.dashboard = OperationsDashboard(stage, NAMES, self.show_department, self.open_trends, data_dir=DATA_DIR)
        self.sections.update(overview=self.dashboard, clips=ttk.Frame(stage))
        self.page = None
        for frame in self.sections.values():
            frame.grid(row=0, column=0, sticky="nsew")
            frame.grid_remove()

        header = heading(production, "AI 创作", "把组装好的内容写成分镜，试剪后继续编辑和导出。")
        ttk.Button(header, text="从平台热点选题", style="Accent.TButton",
                   command=lambda: self.show_department("trends")).pack(side="right")
        ttk.Button(header, text="从采集库选题",
                   command=lambda: self.show_department("collection")).pack(side="right", padx=(0, 7))
        ttk.Button(header, text="图文／文字", command=self.open_copywriting).pack(side="right", padx=(0, 7))
        style = ttk.Style(root)
        style.configure("Workbench.TNotebook", borderwidth=0, tabmargins=(0, 0, 0, 4))
        style.configure("Workbench.TNotebook.Tab", borderwidth=0, padding=(15, 7))
        self.production_tabs = ttk.Notebook(production, style="Workbench.TNotebook")
        self.production_tabs.pack(fill="both", expand=True)
        self.story_tab = ttk.Frame(self.production_tabs, padding=(0, 9, 0, 0))
        self.assets = MediaLibrary(self.production_tabs, NAMES, self.open_production, self.import_from_library,
                                   self.start, review_asset=self.review_asset, play_asset=self.play_asset)
        self.writing_tab = ttk.Frame(self.production_tabs)
        self.production_tabs.add(self.story_tab, text="选题与分镜")
        self.production_tabs.add(self.assets, text="素材库")
        self.production_tabs.add(self.writing_tab, text="批量文案")
        self.production_tabs.bind("<<NotebookTabChanged>>", self.refresh_production_tab)
        self.picker = EditorialPicker(brief, NAMES, self.confirm_editorial, self.close_picker,
                                      self.open_team_summary, read_link=self.read_editorial_link)
        self.picker.frame.pack(fill="both", expand=True)
        self.picker_request = None
        self.picker_day = None
        self.progress_check = 0.0
        bar = ttk.Frame(self.story_tab)
        bar.pack(fill="x", pady=(12, 12))
        self.produce_episode = ttk.Button(bar, text="制作今日合辑", command=self.run_today)
        self.produce_episode.pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="刷新", command=self.load_editions).pack(side="left")
        ttk.Button(bar, text="输出目录", command=lambda: os.startfile(OUTPUT_DIR)).pack(side="left", padx=8)
        self.schedule = ttk.Button(bar, text="检查每日计划…", command=self.toggle_schedule, state="disabled")
        self.schedule.pack(side="right")
        self.status = ttk.Label(self.story_tab, style="Muted.TLabel", wraplength=620)
        self.status.pack(side="bottom", anchor="w", pady=(8, 0))
        content = ttk.Frame(self.story_tab)
        content.pack(fill="both", expand=True, pady=(4, 0))
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)
        self.channels = ttk.Treeview(content, columns=("state",), height=4, selectmode="browse")
        self.channels.heading("#0", text="平台")
        self.channels.heading("state", text="状态")
        self.channels.column("#0", width=106, stretch=False)
        self.channels.column("state", width=98, stretch=False)
        self.channels.grid(row=0, column=0, sticky="ns", padx=(0, 18))
        self.channels.bind("<<TreeviewSelect>>", lambda event: self.show_story() if self.page == "production" else None)
        details = ScrollArea(content)
        details.grid(row=0, column=1, sticky="nsew")
        self.detail = details.body
        self.detail.configure(padding=(0, 0, 8, 6))
        self.load_editions()
        self.show_department("overview")
        root.bind("<Escape>", lambda event: self.show_department("overview"))
        self.root.after(120, self.poll)
        self.refresh_intake()
        if "--check" not in sys.argv:
            self.check_schedule()
            self.root.after(700, self.auto_collection)
            self.root.after_idle(lambda: threading.Thread(target=self.start_local_ai, daemon=True).start())

    @cached_property
    def copywriting(self):
        from studio_copywriting import CopyDesk

        return CopyDesk(self.writing_tab, self.start, embedded=True)

    @cached_property
    def clips(self):
        library = WorksLibrary(self.sections["clips"], NAMES, self.open_production, self.new_from_stage,
                               self.start, read_work=self.read_work, output_dir=OUTPUT_DIR,
                               open_writing=self.open_copywriting)
        library.pack(fill="both", expand=True)
        return library

    @cached_property
    def matrix(self):
        return MatrixPage(self.sections["accounts"], NAMES, self.open_accounts,
                          self.edit_douyin_account, self.connect_douyin_account)

    @cached_property
    def team(self):
        from studio_team import TeamDesk

        return TeamDesk(self.sections["team"], NAMES, DATA_DIR, self.start, self.read_work,
                        self.open_review_release)

    @cached_property
    def operations(self):
        from studio_operations import OperationsDesk

        return OperationsDesk(self.sections["operations"], NAMES, DATA_DIR, self.start,
                              self.open_operations_release)

    @staticmethod
    def start_local_ai():
        from core.local_ai import start_portable_ai

        try:
            start_portable_ai()
        except OSError as error:
            logging.warning("本机 AI 启动失败：%s", error)

    def open_production(self, platform):
        if self.channels.exists(platform):
            self.channels.selection_set(platform)
        self.show_department("production")
        self.production_tabs.select(self.story_tab)

    def open_trends(self, platform):
        self.intake.show_trends(platform, query="")
        self.show_department("trends")

    def search_hotspots(self, query):
        self.intake.show_trends(query=query)
        self.show_department("trends")

    def open_hotspot_writing(self, platform, topic, count=None):
        self.show_department("copywriting")
        self.copywriting.use_hotspot(platform, topic, count=count)

    def start_editorial(self, anchor, *, platform=None, checked=(), target_seconds=None, brief=None):
        if platform is None:
            self.select_work_date(dt.date.today().isoformat())
        platform = platform or anchor.get("platform") or (self.channels.selection() or ("xiaohongshu",))[0]
        day = self.date.get()
        token = object()
        self.picker_request = token
        self.picker_day = day
        self.picker.clear("正在关联本机采集的报道…")
        self.show_department("brief")

        def ready(result, error):
            if self.picker_request is not token or self.date.get() != day:
                return
            if error:
                self.picker.message.set(f"无法打开选题 · {error}")
                return
            self.picker.load(result, platform, checked, target_seconds, brief)

        self.start(lambda: run_worker("editorial_sources", day, OUTPUT_DIR, platform, anchor), ready)

    def read_editorial_link(self, url, ready):
        day = self.date.get()
        self.start(lambda: run_worker("editorial_link", day, OUTPUT_DIR, payload={"url": url}), ready)

    def close_picker(self):
        self.picker_request = None
        self.picker_day = None
        self.picker.clear()
        if self.page == "trends":
            self.topic_tabs.select(self.topic_views["trends"])

    def open_team_summary(self, summary):
        self.show_department("operations")
        self.operations.stage_summary(summary, kind="storyboard")

    def confirm_editorial(self, platform, payload):
        day = self.date.get()
        key = ("editorial_create", day, platform)
        if key in self.active:
            return
        self.active.add(key)
        self.picker.busy = True
        self.picker.controls_state()
        self.picker.message.set("正在保存本次选用的资料…")
        request = self.picker_request

        def ready(item, error):
            self.active.discard(key)
            if self.picker_request is request:
                self.picker.busy = False
                self.picker.controls_state()
            if error:
                if self.picker_request is request:
                    self.picker.message.set(f"资料未确认 · {error}")
                return
            if self.picker_request is request:
                self.close_picker()
            self.load_editions()
            if self.date.get() == day and not self.picker_request:
                self.open_production(platform)
            self.launch("editorial_preview", platform, {"id": item["id"], "direct": True}, day=day)

        self.start(lambda: run_worker("editorial_create", day, OUTPUT_DIR, platform, payload), ready)

    def refresh_production_tab(self, _event=None):
        tab = self.production_tabs.select()
        if tab == str(self.assets):
            self.assets.set_data(self.edition, self.date.get(), self.release.rows)
        elif tab == str(self.writing_tab):
            self.copywriting

    def select_work_date(self, day):
        values = self.date.cget("values")
        if day not in values:
            self.date.configure(values=sorted((*values, day), reverse=True))
        self.date.set(day)
        self.show_edition()

    def new_from_stage(self):
        self.show_department("production")
        self.production_tabs.select(self.story_tab)
        platform = (self.channels.selection() or ("xiaohongshu",))[0]
        if platform not in self.edition["items"]:
            self.new_story(platform)
        elif not self.edition["items"][platform].get("editorial"):
            self.launch("draft", platform)

    def import_from_library(self):
        platform = (self.channels.selection() or ("xiaohongshu",))[0]
        self.open_production(platform)
        if platform not in self.edition["items"]:
            messagebox.showinfo("素材归入当前选题", "请先为这个平台创建或选择一份草稿，再导入素材。", parent=self.root)
            return
        self.import_media(platform)

    def review_asset(self, row, state):
        self.launch("review_media", row.key, {"id": row.issue_id, "page_url": row.asset_id, "state": state})

    def play_asset(self, row):
        day = self.date.get()
        self.status.configure(text="准备素材预览…")

        def finished(video, error):
            if error:
                self.status.configure(text="素材预览未打开")
                messagebox.showerror("素材预览未打开", str(error), parent=self.root)
            else:
                self.status.configure(text="素材审看")
                self.read_work({"video": video}, row.title, "素材审看", row.preview, autoplay=True)

        self.start(lambda: run_worker("preview_asset", day, OUTPUT_DIR, row.key,
                                     {"id": row.issue_id, "page_url": row.asset_id}), finished)

    def show_department(self, key):
        if key in {"assets", "copywriting"}:
            self.show_department("production")
            self.production_tabs.select(self.assets if key == "assets" else self.writing_tab)
            return
        if key in self.topic_views:
            self.topic_tabs.select(self.topic_views[key])
            key = "trends"
        self.page = key
        for name, frame in self.sections.items():
            if name == key:
                frame.grid()
            else:
                frame.grid_remove()
        self.shell.show(key)
        if key == "release":
            self.update_release()
        elif key == "team":
            self.team.refresh()
        elif key == "operations":
            self.operations.refresh()
        elif key == "accounts":
            self.matrix.update(self.release.rows, self.douyin_accounts)
        elif key == "clips":
            self.clips.set_data(self.edition, self.date.get(), self.release.rows)
        elif key == "production":
            self.show_story()
            self.refresh_production_tab()

    def update_dashboard(self):
        self.dashboard.set_data(self.intake.snapshot, self.douyin_accounts, self.release.rows)

    def start(self, action, completed):
        def worker():
            try:
                result = action()
            except Exception as error:
                self.jobs.put((completed, None, error))
            else:
                self.jobs.put((completed, result, None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while not self.jobs.empty():
            callback, value, error = self.jobs.get_nowait()
            try:
                callback(value, error)
            except (ValueError, RuntimeError, OSError, KeyError, tk.TclError) as failure:
                self.status.configure(text=f"操作未完成 · {failure}")
        now = time.monotonic()
        if self.page == "production" and now >= self.progress_check:
            self.progress_check = now + .8
            platform = (self.channels.selection() or (None,))[0]
            if platform and self.is_active("editorial_preview", platform):
                try:
                    item = read_edition(edition_path(OUTPUT_DIR, self.date.get()))["items"].get(platform, {})
                except (OSError, ValueError):
                    item = {}
                current = self.edition["items"].get(platform, {})
                if (item.get("id") == current.get("id") and item.get("preview")
                        and item.get("preview_revision") != current.get("preview_revision")):
                    self.edition["items"][platform] = item
                    self.show_story()
                if progress := item.get("editorial_progress"):
                    self.status.configure(text=progress)
        self.root.after(120, self.poll)

    def check_schedule(self):
        self.start(lambda: schedule_command("/Query"), self.show_schedule)

    def show_schedule(self, result, error):
        self.task_enabled = not error and result.returncode == 0
        self.schedule.configure(
            text="关闭每日 08:30" if self.task_enabled else "开启每日 08:30",
            state="normal" if not error else "disabled",
        )

    def toggle_schedule(self):
        if self.task_enabled:
            args = ("/Delete", "/F")
        else:
            if not messagebox.askyesno("开启每日制作", "每天电脑本地时间 08:30 按平台热点制作视频，成片进入审片队列。"):
                return
            args = ("/Create", "/SC", "DAILY", "/ST", "08:30", "/IT", "/TR", scheduled_action("--daily"))
        self.schedule.configure(state="disabled")

        def finished(result, error):
            if error or result.returncode:
                messagebox.showerror("计划未更新", str(error or (result.stderr or result.stdout).strip()))
            self.check_schedule()

        self.start(lambda: schedule_command(*args), finished)

    def auto_collection(self):
        self.root.after(REFRESH_MINUTES * 60 * 1000, self.auto_collection)
        self.refresh_intake(collect=True)

    def refresh_intake(self, *, collect=False):
        if collect:
            if self.collecting:
                return
            self.collecting = True
            self.collection_done.clear()
        self.intake_request += 1
        request = self.intake_request

        def load():
            try:
                snapshot = read_intake(OUTPUT_DIR)
                if collect and time.time() - (snapshot["checked"] or 0) >= REFRESH_MINUTES * 60:
                    snapshot = run_worker("intake", dt.date.today(), OUTPUT_DIR)
            finally:
                if collect:
                    self.collection_done.set()
            return prepare_intake_view(snapshot)

        def finished(result, error):
            if collect:
                self.collecting = False
            if request < self.intake_applied:
                return
            if error:
                logging.warning("Background intake did not finish: %s", error)
                if not self.intake_applied:
                    self.intake.collection_date.configure(text="暂时无法读取采集库")
                return
            self.intake_applied = request
            self.intake.update(*result)
            self.update_dashboard()
            if collect and not self.active:
                self.start(lambda: run_worker("refresh_writing_library", dt.date.today(), OUTPUT_DIR),
                           self.refresh_open_cases)

        self.start(load, finished)

    def refresh_open_cases(self, _result, error):
        if not error and "copywriting" in self.__dict__:
            self.copywriting.refresh_cases()

    def load_editions(self):
        today = dt.date.today().isoformat()
        current = self.date.get() or today
        dates = {today, current, *(path.parent.name for path in (OUTPUT_DIR / "daily").glob("*/edition.json"))}
        self.date.configure(values=sorted(dates, reverse=True))
        self.date.set(current if current in dates else today)
        self.show_edition()

    def show_edition(self):
        if self.picker_day and self.picker_day != self.date.get():
            self.close_picker()
        path = edition_path(OUTPUT_DIR, self.date.get())
        self.edition = read_edition(path)
        selected = (self.channels.selection() or ("xiaohongshu",))[0]
        self.channels.delete(*self.channels.get_children())
        for platform, name in NAMES.items():
            item = self.edition.get("items", {}).get(platform, {})
            state = STATUS.get(item.get("status"), "暂无选题")
            if item.get("edit_revision") != item.get("package_revision"):
                state = "草稿待剪辑"
            if item.get("package") and platform in PUBLISHERS:
                directory = release_directory(path, platform, item)
                if (directory / "submitted.txt").is_file():
                    state = "已提交审核"
                elif (directory / "submitting.txt").is_file():
                    state = "提交待核对"
            self.channels.insert("", "end", iid=platform, text=name, values=(state,))
        self.channels.selection_set(selected)
        self.update_release()
        if self.page == "clips":
            self.clips.set_data(self.edition, self.date.get(), self.release.rows)
        elif self.page == "production":
            self.show_story()
            self.refresh_production_tab()

    def update_release(self):
        pending_review = any(stage == "hold" and day == self.date.get() for stage, day, _ in self.active)
        self.release.update(self.edition, self.date.get(), OUTPUT_DIR, self.publishing or pending_review)
        if self.page == "release":
            selected = dt.date.fromisoformat(self.date.get())
            monday = selected - dt.timedelta(days=selected.weekday())
            editions = recent_editions(OUTPUT_DIR, (monday + dt.timedelta(days=i) for i in range(7)))
            self.release.show_week(editions, self.date.get())
        if self.page == "accounts":
            self.matrix.update(self.release.rows, self.douyin_accounts)
        self.update_dashboard()

    def show_story(self):
        for child in self.detail.winfo_children():
            child.destroy()
        if not self.channels.selection():
            return
        platform = self.channels.selection()[0]
        profile = PROFILES[platform]
        item = self.edition.get("items", {}).get(platform)
        wrap = max(360, min(710, self.root.winfo_width() - 490))
        series = ("今日热点合辑" if item and item.get("chapters") else
                  "自拟选题" if item and not item["story"].get("url") else profile["series"])
        ttk.Label(self.detail, text=series, style="Muted.TLabel").pack(anchor="w")
        if not item:
            ttk.Label(self.detail, text="当前平台暂无选题", style="Headline.TLabel").pack(anchor="w", pady=12)
            ttk.Label(self.detail, text=profile["audience"], wraplength=wrap).pack(anchor="w")
            ttk.Button(self.detail, text="新建自拟选题", style="Accent.TButton",
                       state="disabled" if self.is_active("create", platform) else "normal",
                       command=lambda: self.new_story(platform)).pack(anchor="w", pady=(14, 0))
            return
        story = item["story"]
        ttk.Label(self.detail, text=story["title"], style="Headline.TLabel", wraplength=wrap).pack(anchor="w", pady=(4, 6))
        ttk.Label(
            self.detail, text=f"{story['category']}  /  {story['source']}  /  {profile['audience']}", wraplength=wrap,
        ).pack(anchor="w")
        if item.get("editorial"):
            self.show_editorial(item, platform, wrap)
            return
        if item.get("chapters"):
            markers = (item.get("edit") or item.get("suggestion") or {}).get("chapter_starts") or [
                {"title": chapter["story"]["title"]} for chapter in item["chapters"]]
            outline = "\n".join(f"{i + 1:02d}  {chapter['title']}" for i, chapter in enumerate(markers[:3]))
            if len(item["chapters"]) > 3:
                outline += f"\n其余 {len(item['chapters']) - 3} 条资讯可在编辑分镜中查看。"
            seconds = item.get("target_seconds", 720)
            duration = f"约 {seconds // 60} 分钟" if platform == "douyin" and seconds <= 180 else "10–15 分钟"
            ttk.Label(self.detail, text=f"目标 {duration} · {len(item['chapters'])} 条资讯\n{outline}",
                      wraplength=wrap, style="Muted.TLabel").pack(anchor="w", pady=(8, 4))
        if item.get("hotspot"):
            hotspot = item["hotspot"]
            ttk.Label(self.detail, text=f"平台热点 #{hotspot['rank']}  {hotspot['title']}",
                      style="Muted.TLabel", wraplength=wrap).pack(anchor="w", pady=(4, 0))
        actions = ttk.Frame(self.detail)
        actions.pack(fill="x", pady=(9, 6))
        if story.get("url"):
            ttk.Button(actions, text="打开报道", command=lambda: webbrowser.open(story["url"])).pack(side="left")
        if item.get("selected_hotspots"):
            ttk.Button(actions, text="核对报道", command=lambda: self.open_episode_sources(item)).pack(side="left")
        self.stage_button(actions, "1  准备素材" if item.get("selected_hotspots") else "采集素材", "media", platform)
        if item.get("chapters"):
            ttk.Label(actions, text=f"{len(item.get('media', []))} 项素材", style="Muted.TLabel").pack(side="left", padx=6)
        ttk.Button(actions, text="导入本地素材", command=lambda: self.import_media(platform),
                   state="disabled" if self.is_active("import_media", platform) else "normal").pack(side="left", padx=(6, 0))
        actions = ttk.Frame(self.detail)
        actions.pack(fill="x", pady=(0, 8))
        self.stage_button(actions, "编辑分镜", "draft", platform)
        self.stage_button(actions, "2  AI 编排分镜" if item.get("selected_hotspots") else "生成 AI 草稿", "ai", platform)
        if item.get("suggestion"):
            ttk.Button(actions, text="审看 AI 分镜", command=lambda: self.edit_cut(platform, item["suggestion"])).pack(
                side="left", padx=(6, 0),
            )
        if item.get("selected_hotspots"):
            self.stage_button(actions, "3  生成开场试剪", "preview", platform, enabled=bool(item.get("edit")))
            self.stage_button(actions, "4  分批渲染成片", "render", platform, enabled=bool(item.get("edit")))
            preview = item.get("preview")
            if preview and item.get("preview_revision") == item.get("edit_revision"):
                ttk.Button(self.detail, text="播放开场试剪", command=lambda: self.read_work(
                    preview, f"{story['title']} · 开场试剪", NAMES[platform], preview.get("cover"))).pack(anchor="w", pady=(0, 8))
        else:
            self.stage_button(actions, "剪辑成片", "render", platform)
        draft = item.get("edit") or item.get("suggestion") or {}
        if plan := draft.get("editorial_plan"):
            ttk.Label(self.detail, text=f"全片主线  ·  {plan['throughline']}",
                      wraplength=wrap, style="Muted.TLabel").pack(anchor="w", pady=(2, 6))
        scenes = draft.get("scenes", ())
        if scenes:
            border, track = panel(self.detail, padding=(12, 9))
            border.pack(fill="x", pady=(4, 8))
            ttk.Label(track, text=f"分镜时间线   ·   {len(scenes)} 镜", style="PanelTitle.TLabel").pack(anchor="w")
            beats = ttk.Frame(track, style="Panel.TFrame")
            beats.pack(fill="x", pady=(7, 0))
            for index, scene in enumerate(scenes[:3]):
                ttk.Button(beats, text=f"{index+1:02d}  {scene['title'][:6]}",
                           command=lambda shot=index: self.edit_cut(platform, draft, initial_scene=shot)).pack(side="left", padx=(0, 5))
        package = item.get("package")
        if not package:
            if item.get("error"):
                ttk.Label(self.detail, text=item["error"], wraplength=wrap).pack(anchor="w", pady=12)
            return

        video = Path(package["video"])
        cover = Path(package["cover"]) if package["cover"] else video.parent / "slide_00.png"
        border, preview = panel(self.detail, padding=(12, 10))
        border.pack(fill="both", expand=True, pady=(8, 0))
        actions = ttk.Frame(preview, style="Panel.TFrame")
        actions.pack(fill="x", pady=(0, 10))
        duration = int(package.get("duration") or 0)
        ratio = PLATFORM_PRESETS[platform]["aspect_ratio"]
        ttk.Label(actions, text=f"成片预览   ·   {duration//60:02d}:{duration%60:02d}   ·   {ratio}",
                  style="PanelTitle.TLabel").pack(side="left")
        ttk.Button(actions, text="翻阅成片", command=lambda: self.read_work(package, story["title"], NAMES[platform], str(cover)),
                   state="normal" if video.is_file() else "disabled").pack(side="right")
        ttk.Button(actions, text="发布包", command=lambda: os.startfile(video.parent),
                   state="normal" if video.parent.is_dir() else "disabled").pack(side="right", padx=(0, 5))
        body = ttk.Frame(preview, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        monitor = tk.Canvas(body, bg=FIELD, width=218, height=248, highlightthickness=0)
        monitor.pack(side="left", anchor="n", padx=(0, 12))
        for candidate in (cover, SCREENING / "viewfinder.png"):
            try:
                with Image.open(candidate) as image:
                    image.thumbnail((190, 220))
                    monitor.picture = ImageTk.PhotoImage(image, master=monitor)
                monitor.create_image(109, 124, image=monitor.picture)
                break
            except OSError:
                continue
        for x, dx in ((10, 11), (208, -11)):
            for y, dy in ((10, 11), (238, -11)):
                monitor.create_line(x, y+dy, x, y, x+dx, y, fill=AMBER)
        copy = ttk.Frame(body, style="Panel.TFrame")
        copy.pack(side="left", fill="both", expand=True)
        ttk.Button(copy, text="复制发布文案", command=lambda: self.copy_caption(package["caption"])).pack(anchor="w")
        ttk.Label(
            copy, text=f"{len(item.get('media', []))} 项可用素材", style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=5)
        text = tk.Text(copy, wrap="word", relief="flat", bg=FIELD, fg=TEXT, padx=10, pady=8, height=6)
        scroll = ttk.Scrollbar(copy, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", package["caption"])
        text.configure(state="disabled")
        pages = package.get("reading_pages", ())
        waveform = package.get("speech_waveform")
        if pages or waveform:
            rail = ttk.Frame(preview, style="Panel.TFrame")
            rail.pack(fill="x", pady=(10, 0))
            if pages:
                ttk.Label(rail, text="画面", style="PanelMuted.TLabel").pack(side="left", padx=(0, 7))
                for index, page in enumerate(pages[:3], 1):
                    seconds = page["start_ms"] // 1000
                    ttk.Button(rail, text=f"{seconds//60:02d}:{seconds%60:02d}  {page['title'][:4]}",
                               command=lambda chapter=index: self.read_work(package, story["title"], NAMES[platform],
                                                                            str(cover), chapter)).pack(side="left", padx=(0, 4))
            if waveform:
                try:
                    with Image.open(video.with_name(waveform)) as wave:
                        wave.thumbnail((min(600, wrap-45), 32))
                        track = ttk.Label(preview, text="口播  ", compound="right", style="PanelMuted.TLabel")
                        track.picture = ImageTk.PhotoImage(wave, master=track)
                    track.configure(image=track.picture)
                    track.pack(anchor="w", pady=(6, 0))
                except (OSError, ValueError):
                    pass
        if item.get("warnings"):
            ttk.Label(self.detail, text=" · ".join(item["warnings"]), wraplength=585, style="Muted.TLabel").pack(anchor="w")
        if item.get("error"):
            ttk.Label(self.detail, text=f"上次剪辑失败，仍可预览已有成片。{item['error']}",
                      wraplength=585, style="Muted.TLabel").pack(anchor="w")

    def is_active(self, stage, platform):
        return (stage, self.date.get(), platform) in self.active

    def show_editorial(self, item, platform, wrap):
        editorial = item["editorial"]
        draft = item.get("edit") or item.get("suggestion")
        busy = any(self.is_active(step, platform) for step in
                   ("editorial_create", "editorial_preview", "visual_direct", "editorial_export", "save", "import_media", "media"))
        state = "disabled" if busy else "normal"
        border, body = panel(self.detail, padding=(12, 10))
        border.pack(fill="x", pady=(10, 8))
        seconds = item.get("target_seconds", 720)
        duration_label = f"约 {seconds // 60} 分钟" if seconds <= 180 else "10–15 分钟"
        ttk.Label(body, text=f"本期资料 · {len(editorial['reports'])} 篇   /   目标 {duration_label}",
                  style="PanelTitle.TLabel").pack(anchor="w")
        if brief := editorial.get("brief"):
            inputs = {f"report:{row['id']}": row["title"] for row in editorial["reports"]}
            inputs.update({f"note:{index}": note for index, note in enumerate(brief["notes"])})
            inputs.update({f"asset:{index}": Path(name).name for index, name in enumerate(brief["asset_paths"])})
            focus = brief["focus_id"]
            focus = focus if focus.startswith("note:") else f"report:{focus}"
            if focus in inputs:
                ttk.Label(body, text=f"讲述重点 · {brief.get('focus_quote') or inputs[focus]}", style="PanelAccent.TLabel",
                          wraplength=wrap).pack(anchor="w", pady=(3, 1))
            ordered = [inputs[key].splitlines()[0][:76] for key in brief["sequence"] if key in inputs]
            if ordered:
                ttk.Label(body, text="本次输入  " + "  →  ".join(ordered),
                          wraplength=wrap, style="PanelMuted.TLabel").pack(anchor="w", pady=(3, 2))
        for report in editorial["reports"]:
            row = ttk.Frame(body, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Button(row, text="原文 ↗", command=lambda url=report["url"]: webbrowser.open(url)).pack(side="right")
            ttk.Label(row, text=f"{report['source']} · {report['title']}", wraplength=max(270, wrap-110),
                      style="PanelMuted.TLabel").pack(side="left", fill="x")
        tools = ttk.Frame(self.detail)
        tools.pack(fill="x", pady=(0, 8))
        ttk.Button(tools, text="调整素材组装", state=state, command=lambda: self.start_editorial(
            editorial["anchor"], platform=platform, checked=[row["id"] for row in editorial["reports"]],
            target_seconds=seconds, brief=editorial.get("brief"))).pack(side="left")
        if item.get("chapters"):
            ttk.Button(tools, text="搜片头素材", state=state, command=lambda: self.launch(
                "media", platform, on_success=lambda: self.show_department("assets"))).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="导入片头素材", state=state, command=lambda: self.import_media(
            platform, opening=True)).pack(side="left", padx=6)
        ttk.Button(tools, text="生成开场试剪" if draft else "继续生成试剪", state=state, style="Accent.TButton",
                   command=lambda: self.launch("editorial_preview", platform,
                                               {"id": item["id"], "direct": True})).pack(side="left")
        if draft:
            monitor = tk.Frame(self.detail, background="#192853", padx=8, pady=6)
            monitor.pack(fill="x", pady=(1, 5))
            preview = item.get("preview") or {}
            valid_preview = item.get("preview_revision") in {
                item.get("edit_revision"), item.get("suggestion_revision"), item.get("opening_revision")
            }
            picture = load_thumbnail(preview.get("cover"), (218, 110)) if valid_preview else None
            if picture:
                frame = tk.Label(monitor, background="#192853")
                frame.image = ImageTk.PhotoImage(picture, master=frame)
                frame.configure(image=frame.image)
                frame.pack(side="left")
            tk.Label(monitor, text="试剪画面" if picture else "分镜已生成 · 等待试剪画面",
                     background="#192853", foreground="#e5eaff", padx=7).pack(side="left")
            timeline = StoryboardTimeline(
                self.detail, draft["scenes"], seconds,
                (lambda index: self.edit_cut(platform, draft, initial_scene=index)) if not busy else None,
            )
            timeline.pack(fill="x", pady=(0, 7))
            ttk.Button(tools, text="审看编辑分镜", state=state, command=lambda: self.edit_cut(platform, draft)).pack(
                side="left", padx=6)
            day = self.date.get()

            def review_rewrite():
                current = self.edition.get("items", {}).get(platform, {})
                if self.date.get() == day and current.get("id") == item["id"] and current.get("suggestion"):
                    self.edit_cut(platform, current["suggestion"], day=day)

            ttk.Button(self.detail, text="按案例重写分镜", state=state, command=lambda: self.launch(
                "editorial_preview", platform, {"id": item["id"], "rewrite": True, "direct": True}, day=day,
                on_success=review_rewrite)).pack(anchor="w", pady=(0, 5))
            ttk.Button(self.detail, text="视觉设计与看图选镜", state=state, command=lambda: self.launch(
                "visual_direct", platform, {"id": item["id"]}, day=day,
                on_success=review_rewrite)).pack(anchor="w", pady=(0, 5))
            length = sum(len(shot["text"]) for shot in draft["scenes"])
            ttk.Label(self.detail, text=f"口播约 {length:,} 字 · 导出前按实际配音校验时长",
                      style="Muted.TLabel").pack(anchor="w", pady=(2, 4))
            ttk.Label(self.detail, text="片头会自动插入通过审核的相关照片和视频。审核后可重新生成试剪。",
                      style="Muted.TLabel", wraplength=wrap).pack(anchor="w", pady=(0, 4))
            if direction := draft.get("editorial_plan", {}).get("throughline"):
                ttk.Label(self.detail, text=direction, wraplength=wrap, style="Muted.TLabel").pack(anchor="w", pady=(2, 4))
            for index, marker in enumerate(draft.get("chapter_starts", []), 1):
                ttk.Label(self.detail, text=f"{index:02d}  {marker['title']}", style="Muted.TLabel",
                          wraplength=wrap).pack(anchor="w", pady=2)
                if reference := marker.get("style_reference"):
                    ttk.Button(self.detail, text=f"写法案例 ↗  {reference['title']}",
                               command=lambda url=reference["url"]: webbrowser.open(url)).pack(anchor="w", pady=(0, 4))
                elif "style_reference" in marker:
                    ttk.Label(self.detail, text="未匹配到同平台的相关作品", style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        revision = item.get("edit_revision") or item.get("suggestion_revision") or item.get("opening_revision")
        review = ttk.Frame(self.detail)
        review.pack(fill="x", pady=8)
        preview = item.get("preview")
        if preview and item.get("preview_revision") == revision:
            ttk.Button(review, text="播放开场试剪", command=lambda: self.read_work(
                preview, f"{item['story']['title']} · 开场试剪", NAMES[platform], preview.get("cover"))).pack(side="left")
        elif preview and item.get("preview_revision") == item.get("suggestion_revision"):
            ttk.Button(review, text="播放候选试剪", command=lambda: self.read_work(
                preview, f"{item['story']['title']} · 候选试剪", NAMES[platform], preview.get("cover"))).pack(side="left")
        elif preview:
            ttk.Label(review, text="试剪已过期", style="Muted.TLabel").pack(side="left")
        ttk.Button(review, text="确认并导出视频", style="Accent.TButton",
                   state=state if item.get("edit") else "disabled",
                   command=lambda: self.launch("editorial_export", platform, {
                       "id": item["id"], "revision": item["edit_revision"]})).pack(side="left", padx=6)
        if package := item.get("package"):
            ttk.Button(review, text="翻阅成片", command=lambda: self.read_work(
                package, item["story"]["title"], NAMES[platform], package.get("cover"))).pack(side="left")
        if progress := item.get("editorial_progress"):
            ttk.Label(self.detail, text=progress, style="Muted.TLabel", wraplength=wrap).pack(anchor="w")
        if item.get("warnings"):
            ttk.Label(self.detail, text=" · ".join(item["warnings"]), style="Muted.TLabel", wraplength=wrap).pack(anchor="w")
        if error := item.get("error"):
            ttk.Label(self.detail, text=error, style="Muted.TLabel", wraplength=wrap).pack(anchor="w")

    def open_episode_sources(self, item):
        dialog = tk.Toplevel(self.root)
        dialog.title("核对热点与报道")
        dialog.transient(self.root)
        dialog.geometry("870x540")
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        heading(body, "核对报道", "热榜话题用于选题。口播事实来自右侧对应的报道。")
        chapters = item["chapters"]
        table = ttk.Treeview(body, columns=("topic", "report", "source"), show="headings", height=len(chapters),
                             selectmode="browse")
        for key, title, width in (("topic", "所选热点", 240), ("report", "报道标题", 370), ("source", "出处", 110)):
            table.heading(key, text=title)
            table.column(key, width=width)
        for index, chapter in enumerate(chapters):
            story = chapter["story"]
            table.insert("", "end", iid=str(index), values=(chapter["hotspot"]["title"], story["title"], story["source"]))
        table.pack(fill="x")
        excerpt = tk.Text(body, height=7, wrap="word", padx=8, pady=8, state="disabled")
        excerpt.pack(fill="both", expand=True, pady=10)

        def selected():
            return chapters[int(table.selection()[0])]["story"]

        def show_excerpt(_event=None):
            if table.selection():
                excerpt.configure(state="normal")
                excerpt.delete("1.0", "end")
                excerpt.insert("1.0", selected()["summary"])
                excerpt.configure(state="disabled")

        table.selection_set("0")
        table.bind("<<TreeviewSelect>>", show_excerpt)
        show_excerpt()
        ttk.Button(body, text="查看所选报道 ↗",
                   command=lambda: webbrowser.open(selected()["url"]) if table.selection() else None).pack(anchor="e")

    def stage_button(self, parent, label, stage, platform, *, enabled=True):
        ttk.Button(
            parent, text=label, state="normal" if enabled and not self.is_active(stage, platform) else "disabled",
            command=lambda: self.launch(stage, platform),
        ).pack(side="left", padx=(6, 0))

    def launch(self, stage, platform=None, payload=None, *, day=None, on_success=None, on_error=None):
        day = day or self.date.get()
        key = (stage, day, platform)
        if key in self.active:
            return
        self.active.add(key)
        labels = {"collect": "采集资讯", "media": "采集素材", "draft": "载入分镜", "save": "保存分镜", "create": "新建选题",
                  "select_episode": "核对所选热点的报道", "preview": "渲染开场试剪",
                  "editorial_preview": "自动生成试剪", "editorial_export": "导出视频", "visual_direct": "视觉设计与选镜",
                  "import_media": "导入本地素材", "ai": "生成 AI 草稿", "render": "剪辑成片", "hold": "更新审片状态",
                  "review_media": "审核素材"}
        label = labels[stage]
        self.show_story()
        self.update_release()
        self.status.configure(text=f"{label} · 正在运行")

        def finished(result, error):
            self.active.discard(key)
            self.load_editions()
            if error:
                self.status.configure(text=f"{label}失败 · {error}")
                if on_error:
                    on_error(error)
                elif stage == "review_media":
                    messagebox.showerror("素材审核未完成", str(error), parent=self.root)
                return
            self.status.configure(text=f"{label}完成")
            if stage == "draft" and self.date.get() == day:
                self.edit_cut(platform, result["draft"], version=result)
            if on_success:
                on_success()

        if stage == "draft":
            payload = {"with_revision": True}
        self.start(lambda: run_worker(stage, day, OUTPUT_DIR, platform, payload), finished)

    def import_media(self, platform, *, opening=False):
        day = self.date.get()
        issue_id = self.edition.get("items", {}).get(platform, {}).get("id")
        filename = filedialog.askopenfilename(
            title="选择片头素材" if opening else "选择可用于发布的本地素材", parent=self.root,
            filetypes=(("照片和视频", ("*.jpg", "*.jpeg", "*.png", "*.mp4", "*.mov", "*.webm")),),
        )
        if filename:
            payload = {"id": issue_id, "filename": filename} if opening else filename
            self.launch("import_media", platform, payload, day=day)

    def new_story(self, platform):
        day = self.date.get()
        title = simpledialog.askstring("新建自拟选题", f"输入标题（1–{MANUAL_TITLE_LIMIT} 字）", parent=self.root)
        if title is None:
            return
        title = title.strip()
        if not 1 <= len(title) <= MANUAL_TITLE_LIMIT:
            messagebox.showerror("标题无效", f"请填写 1–{MANUAL_TITLE_LIMIT} 字标题。", parent=self.root)
            return
        draft = {"scenes": [{"title": heading, "text": "", "media": "", "zoom": False} for heading in ("开场", "正文")]}
        self.edit_cut(platform, draft, day=day, new_title=title)

    def edit_cut(self, platform, draft, *, day=None, new_title=None, initial_scene=0, version=None):
        from studio_editor import CutEditor

        day = day or self.date.get()
        item = {} if new_title else self.edition["items"][platform]
        media = usable_media(item)
        version = version or {"episode_id": item.get("id"), "edit_revision": item.get("edit_revision"),
                              "suggestion_revision": item.get("suggestion_revision")}

        def failed(error):
            if editor.window.winfo_exists():
                messagebox.showerror("分镜未保存", str(error), parent=editor.window)

        def save(value, saved):
            if new_title:
                self.launch("create", platform, {"title": new_title, "cut": value}, day=day,
                            on_success=saved, on_error=failed)
            else:
                payload = {**value, "episode_id": version["episode_id"], "expected_revision": version["edit_revision"],
                           "expected_suggestion_revision": version["suggestion_revision"],
                           "draft_source": "suggestion" if draft == item.get("suggestion") else "edit"}
                self.launch("save", platform, payload, day=day, on_success=saved, on_error=failed)

        editor = CutEditor(self.root, NAMES[platform], draft, media, save, initial_scene=initial_scene)

    def open_accounts(self, platform="douyin"):
        from studio_accounts import AccountDesk

        dialog = AccountDesk(self.root, NAMES, self.start, platform)
        dialog.window.bind("<Destroy>", lambda event: self.root.after_idle(self.update_release) if event.widget == dialog.window else None)

    def edit_douyin_account(self, profile_id=None):
        if self.publishing:
            return
        label = simpledialog.askstring(
            "抖音账号备注", "给这个账号起个方便辨认的名字。",
            initialvalue=self.douyin_accounts.get(profile_id, ""), parent=self.root,
        )
        if label is None:
            return
        try:
            saved = save_profile(DATA_DIR, label, profile_id=profile_id)
            self.douyin_accounts = douyin_profiles(DATA_DIR)
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror("账号备注未保存", str(error), parent=self.root)
            return
        self.release.set_douyin_profiles(self.douyin_accounts)
        self.update_release()
        self.matrix.douyin.selection_set(saved)
        self.matrix.douyin.see(saved)
        if profile_id is None:
            self.connect_douyin_account(saved)

    def connect_douyin_account(self, profile_id):
        if self.publishing or (self.post_panel and self.post_panel.poll() is None):
            messagebox.showinfo("抖音面板已打开", "请先关闭当前抖音面板。", parent=self.root)
            return
        try:
            self.open_douyin_panel(self.root.winfo_id(), profile_id, "login")
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror("无法打开抖音账号", str(error), parent=self.root)

    def read_work(self, package, title, platform, cover, chapter=0, *, autoplay=False):
        video = package.get("video")
        if not video or not Path(video).is_file():
            messagebox.showinfo("成片不可用", "请检查本地成片文件。", parent=self.root)
            return
        issue = {"title": title, "platform": platform, "video": video, "cover": cover,
                 "duration": package.get("duration"), "reading_pages": package.get("reading_pages", []),
                 "chapter": chapter, "autoplay": autoplay}
        try:
            self.readers = [reader for reader in self.readers if reader.poll() is None]
            self.readers.append(subprocess.Popen(
                [*desktop_command(), "--reader", json.dumps(issue, ensure_ascii=False)], creationflags=NO_WINDOW,
            ))
        except OSError as error:
            messagebox.showerror("阅读器未打开", str(error), parent=self.root)

    def close_readers(self):
        for reader in [*self.readers, self.post_panel]:
            if reader is None:
                continue
            if reader.poll() is None:
                reader.terminate()
                reader.wait(timeout=2)

    def open_copywriting(self):
        self.show_department("copywriting")

    def copy_caption(self, caption):
        self.root.clipboard_clear()
        self.root.clipboard_append(caption)
        self.status.configure(text="已复制发布文案")

    def run_today(self):
        if self.busy:
            return
        self.busy = True
        self.produce_episode.configure(state="disabled")
        self.show_department("production")
        self.status.configure(text="准备今日版")

        def progress(message):
            if message.startswith("制作部 ·") and not message.endswith("采集素材"):
                self.jobs.put((lambda text, error: self.status.configure(text=text), message, None))

        def produce():
            self.collection_done.wait(420)
            return run_hot_daily(progress=progress, invoke=run_worker)

        def done(result, error):
            self.busy = False
            self.produce_episode.configure(state="normal")
            self.refresh_intake()
            self.date.set(dt.date.today().isoformat())
            self.load_editions()
            if error:
                self.status.configure(text=f"今日制作失败 · {error}")
                return
            edition, errors = result
            items = [item for item in edition["items"].values() if item.get("chapters")]
            ready = sum(bool(item.get("package")) for item in items)
            message = f"资讯合辑 · {ready}/{len(items)} 集已成片" if items else "每个平台至少需要 3 条有正文的热点，采集库会自动补充。"
            self.status.configure(text="  ·  ".join([message, *errors]))

        self.start(produce, done)

    def prepare_selected_post(self):
        selected = self.release.selected_browser_post()
        if not selected:
            return
        platform, video = selected
        day = self.date.get()
        open_panel = None
        linked_task = None
        if platform == "douyin":
            if self.post_panel and self.post_panel.poll() is None:
                self.release.result.set("抖音 · 请在已打开的投稿面板继续操作。")
                return
            owner = self.root.winfo_id()
            profile_id = self.release.selected_douyin_profile()
            open_panel = lambda url, **draft: self.open_douyin_panel(owner, profile_id, url)
            if self.operations_task:
                client, task = self.operations_task
                if (client is self.operations.client and task["platform"] == platform
                        and task["account_label"] == self.douyin_accounts.get(profile_id)
                        and task["title"] == self.release.rows[platform]["title"]):
                    linked_task = self.operations_task
        self.publishing = True
        self.update_release()
        waiting = "如需登录，请扫码。进入投稿页后会自动上传。" if open_panel else "正在把成片交给浏览器投稿页…"
        self.release.result.set(f"{NAMES[platform]} · {waiting}")

        def finished(result, error):
            self.publishing = False
            self.load_editions()
            messages = {
                "ready": "视频已交给投稿页，标题和文案已填好。请核对个人账号、上传进度和发布设置。",
                "partial": "视频已交给投稿页。请补齐未填写的项目，核对账号后发布。",
                "no_form": "没有找到空白投稿表单。请在创作者后台登录或打开新投稿页后重试。",
                "no_connection": ("投稿面板未连接或扫码超时。请关闭面板后重试。" if open_panel else
                                  "投稿助手未连接。请在默认浏览器启用扩展，登录创作者后台后重试。"),
                "transfer_error": "成片传输未完成。请检查浏览器和投稿助手扩展。",
                "needs_check": "请先检查当前投稿页的上传和填稿情况，再决定是否重试。",
                "closed": "已关闭投稿面板，成片未传入。",
            }
            message = str(error) if error else messages.get(result, "请在创作者后台核对投稿页。")
            self.release.result.set(f"{NAMES[platform]} · {message}")
            if (not error and result == "ready" and linked_task and linked_task is self.operations_task
                    and linked_task[0] is self.operations.client):
                self.operations.record_prepared(linked_task[1])

        self.start(lambda: run_stage("browser_post", day, OUTPUT_DIR, platform, {"video": video},
                                     open_post_panel=open_panel), finished)

    def open_review_release(self, review=None):
        self.show_department("release")
        if review is not None:
            self.release.set_review_base(review)

    def open_operations_release(self, publication=None):
        self.show_department("release")
        self.operations_task = (self.operations.client, publication) if publication else None
        self.release.set_operations_task(publication, self.clear_operations_task)
        if publication and self.release.tree.exists(publication["platform"]):
            self.release.tree.selection_set(publication["platform"])
            self.release.select()

    def clear_operations_task(self):
        self.operations_task = None
        self.release.set_operations_task(None)

    def share_for_review(self):
        selected = self.release.selected_team_review()
        if not selected:
            return
        platform, video = selected
        current = read_edition(edition_path(OUTPUT_DIR, self.date.get()))["items"].get(platform, {})
        package = current.get("package") or {}
        if (package.get("video") != video or current.get("edit_revision") != current.get("package_revision")
                or current.get("held_video") == video):
            self.release.result.set("成片版本已更新，请刷新队列后再送审。")
            return
        previous = self.release.previous_review

        def clear_review_base():
            if self.release.previous_review is previous:
                self.release.set_review_base(None)

        self.show_department("team")
        self.team.share(platform, package, previous_id=previous["id"] if previous else None,
                        on_success=clear_review_base if previous else None)

    def open_douyin_panel(self, owner, profile_id, request):
        frozen = getattr(sys, "frozen", False)
        helper = (BASE_DIR if frozen else BASE_DIR / "build") / "post_panel" / "PostingPanel.exe"
        extension = (Path(sys.executable).parent if frozen else BASE_DIR) / "browser_extension"
        if not helper.is_file() or not (extension / "manifest.json").is_file():
            raise RuntimeError("找不到抖音投稿面板或投稿助手，请重新打包工作台。")
        profile = profile_directory(DATA_DIR, profile_id)
        process = subprocess.Popen(
            [str(helper), str(owner), str(profile), str(extension), self.douyin_accounts[profile_id]],
            stdin=subprocess.PIPE, creationflags=NO_WINDOW,
        )
        self.post_panel = process
        with process.stdin as channel:
            channel.write(request.encode("ascii") + b"\n")
        return process

    def publish_batch(self):
        if self.publishing or self.release.busy:
            return
        day = self.date.get()
        targets = self.release.eligible()
        if not targets:
            return
        details = "\n".join(f"{NAMES[key]} · {row['account']} · 《{row['title']}》" for key, row in targets.items())
        if not messagebox.askyesno("确认提交平台审核", f"将以下成片直接提交到对应账号。\n\n{details}", parent=self.root):
            return
        self.publishing = True
        self.update_release()
        self.release.result.set(f"{day} · 正在投稿")

        def submit():
            results = []
            for platform, row in targets.items():
                try:
                    receipt = run_worker("publish", day, OUTPUT_DIR, platform,
                                         {"account": row["identity"], "video": row["video"]})
                except Exception as error:
                    results.append(f"{NAMES[platform]} · {error}")
                else:
                    results.append(f"{NAMES[platform]} · 已提交审核（{receipt}）")
            return results

        def finished(results, error):
            self.publishing = False
            self.load_editions()
            self.release.result.set(f"{day} · " + (str(error) if error else "\n".join(results)))

        self.start(submit, finished)

def main():
    root = tk.Tk()
    desk = Desk(root)
    if "--check" in sys.argv:
        pages = iter((*desk.sections, "overview"))
        errors = []

        def navigate(current=None, deadline=None):
            try:
                if current and not desk.sections[current].winfo_viewable():
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"桌面页面未显示：{current}")
                    root.after(75, lambda: navigate(current, deadline))
                    return
                if next_page := next(pages, None):
                    desk.show_department(next_page)
                    root.after_idle(lambda: root.after(75, lambda: navigate(next_page, time.monotonic()+12)))
                    return
                # Tk may unmap the report table while it sits below the canvas viewport.
                if not desk.dashboard.report_tree.winfo_manager() or desk.picker.records or desk.picker.selected:
                    raise RuntimeError("今日面板或素材组装初始状态异常。")
            except Exception as error:
                errors.append(error)
            root.quit()

        root.after_idle(navigate)
        root.mainloop()
        if errors:
            root.destroy()
            raise errors[0]
        subprocess.run([get_ffmpeg(), "-version"], check=True, capture_output=True, creationflags=NO_WINDOW)
        (DATA_DIR / "desktop-check.txt").write_text(
            f"Tk UI, {len(desk.sections)} views, today dashboard and bundled FFmpeg OK\n", encoding="utf-8",
        )
        if len(sys.argv) == 3 and sys.argv[1] == "--check":
            issue = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
            root.title("热采放映室 · 工作台自检")
            desk.read_work(issue, "联动阅读器自检", issue["platform"], issue.get("cover"))
            (DATA_DIR / "reader-launch.txt").write_text(str(time.time()), encoding="utf-8")
            try:
                root.mainloop()
            finally:
                desk.close_readers()
        else:
            root.destroy()
        return 0
    try:
        root.mainloop()
    finally:
        desk.close_readers()
    return 0


if __name__ == "__main__":
    from studio_entry import run_command

    result = run_command()
    sys.exit(main() if result is None else result)
