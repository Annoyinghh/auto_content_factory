"""Desktop batch writing and source-backed case library."""

import datetime as dt
import os
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from config import OUTPUT_DIR
from core.copywriting import STYLES, add_case, last_hotspot_refresh, list_cases, queue_case_excerpt
from core.desktop_jobs import run_worker


class CopyDesk:
    def __init__(self, parent, start, *, embedded=False):
        self.start = start
        self.window = parent if embedded else tk.Toplevel(parent)
        if not embedded:
            self.window.title("内容工厂 · 批量文案与案例库")
            self.window.geometry("880x660")
            self.window.minsize(640, 530)
        self.names = {name: key for key, (name, _) in STYLES.items()}
        self.directory = None
        self.hotspot = None
        self.cases = []
        self.busy = False
        self.note_drafts = []
        self.picture_index = 0
        self.thumbnail = None
        shell = ttk.Frame(self.window, padding=18)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="批量文案与案例库", style="Headline.TLabel").pack(anchor="w", pady=(0, 10))
        tabs = ttk.Notebook(shell)
        tabs.pack(fill="both", expand=True)
        writing, library = ttk.Frame(tabs, padding=12), ttk.Frame(tabs, padding=12)
        tabs.add(writing, text="批量写作")
        tabs.add(library, text="案例库")
        self._writing(writing)
        self._library(library)

    def _writing(self, page):
        ttk.Label(page, text="选题").pack(anchor="w")
        self.topic = ttk.Entry(page)
        self.topic.pack(fill="x", pady=(3, 8))
        ttk.Label(page, text="已核实的事实与素材摘要（可选）").pack(anchor="w")
        self.facts = tk.Text(page, height=4, wrap="word", padx=6, pady=5)
        self.facts.pack(fill="x", pady=(3, 8))
        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="平台").pack(side="left")
        self.targets = ttk.Combobox(bar, values=("全部平台", *self.names), state="readonly", width=11)
        self.targets.current(0)
        self.targets.pack(side="left", padx=(5, 12))
        ttk.Label(bar, text="每平台").pack(side="left")
        self.count = ttk.Combobox(bar, values=("1", "2", "3"), state="readonly", width=3)
        self.count.current(1)
        self.count.pack(side="left", padx=5)
        ttk.Label(bar, text="版").pack(side="left")
        self.note_images = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="小红书热点配图", variable=self.note_images).pack(side="left", padx=12)
        self.generate = ttk.Button(bar, text="生成本批文案", style="Accent.TButton", command=self.generate_batch)
        self.generate.pack(side="right")
        self.collect = ttk.Button(bar, text="采集同题案例", command=self.collect_cases)
        self.collect.pack(side="right", padx=6)
        self.status = ttk.Label(page, text="本机 AI 会按平台检索写法参考。", style="Muted.TLabel", wraplength=595)
        self.status.pack(anchor="w", pady=(0, 6))
        self.result_area = ttk.Frame(page)
        self.result_area.pack(fill="both", expand=True)
        self.note_toolbar = ttk.Frame(self.result_area)
        self.note_choice = ttk.Combobox(self.note_toolbar, state="readonly", width=36)
        self.note_choice.pack(side="left")
        self.note_choice.bind("<<ComboboxSelected>>", lambda event: self.show_note(reset=True))
        self.note_source = ttk.Button(self.note_toolbar, text="打开本版参考", command=self.open_note_source)
        self.note_source.pack(side="right")
        results = ttk.Frame(self.result_area)
        results.pack(fill="both", expand=True)
        self.note_preview = ttk.Frame(results, padding=(0, 0, 12, 0))
        self.note_thumbnail = ttk.Label(self.note_preview)
        self.note_thumbnail.pack(anchor="center")
        controls = ttk.Frame(self.note_preview)
        controls.pack(fill="x", pady=(6, 0))
        self.picture_back = ttk.Button(controls, text="‹", width=3, command=lambda: self.move_picture(-1))
        self.picture_back.pack(side="left")
        self.picture_next = ttk.Button(controls, text="›", width=3, command=lambda: self.move_picture(1))
        self.picture_next.pack(side="right")
        self.picture_page = ttk.Label(controls, style="Muted.TLabel")
        self.picture_page.pack(expand=True)
        ttk.Button(self.note_preview, text="打开大图", command=self.open_picture).pack(anchor="center", pady=6)
        self.note_text_area = ttk.Frame(results)
        self.note_text_area.pack(side="left", fill="both", expand=True)
        self.result = tk.Text(self.note_text_area, wrap="word", padx=8, pady=6, state="disabled")
        scroll = ttk.Scrollbar(self.note_text_area, command=self.result.yview)
        self.result.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.result.pack(side="left", fill="both", expand=True)
        ttk.Button(page, text="打开批次目录", command=self.open_directory).pack(anchor="e", pady=(8, 0))

    def _library(self, page):
        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 8))
        self.case_platform = ttk.Combobox(bar, values=list(self.names), state="readonly", width=12)
        self.case_platform.current(0)
        self.case_platform.pack(side="left")
        self.case_platform.bind("<<ComboboxSelected>>", lambda event: self.refresh_cases())
        ttk.Button(bar, text="录入案例", command=self.new_case).pack(side="right")
        self.import_work = ttk.Button(bar, text="采集作品链接", command=self.import_public_case)
        self.import_work.pack(side="right", padx=6)
        self.case_list = ttk.Treeview(page, columns=("evidence",), height=7, selectmode="browse")
        self.case_list.heading("#0", text="案例")
        self.case_list.heading("evidence", text="热度依据")
        self.case_list.column("#0", width=235)
        self.case_list.column("evidence", width=340)
        self.case_list.pack(fill="both", expand=True)
        self.case_list.bind("<<TreeviewSelect>>", lambda event: self.describe_case())
        self.case_notes = ttk.Label(page, style="Muted.TLabel", wraplength=570, justify="left")
        self.case_notes.pack(fill="x", pady=8)
        ttk.Button(page, text="打开出处", command=self.open_source).pack(anchor="e")
        self.auto_note = ttk.Label(page, style="Muted.TLabel", wraplength=570)
        self.auto_note.pack(anchor="w", pady=(3, 0))
        self.refresh_cases()

    def refresh_cases(self):
        platform = self.names[self.case_platform.get()]
        self.cases = list_cases(OUTPUT_DIR, platform)
        self.auto_note.configure(text=last_hotspot_refresh(OUTPUT_DIR, platform) or "等待近期热点检查")
        self.case_list.delete(*self.case_list.get_children())
        for index, case in enumerate(self.cases):
            self.case_list.insert("", "end", iid=str(index), text=case["title"], values=(case["evidence"],))
        self.describe_case()

    def selected_case(self):
        chosen = self.case_list.selection()
        return self.cases[int(chosen[0])] if chosen else None

    def describe_case(self):
        case = self.selected_case()
        self.case_notes.configure(text=case["notes"] if case else "选择案例查看写法摘要。")

    def open_source(self):
        case = self.selected_case()
        if case:
            webbrowser.open(case["url"])

    def new_case(self):
        dialog = CaseDialog(self.window, self.names[self.case_platform.get()], self.start)
        if dialog.result:
            self.refresh_cases()
            if dialog.result == "queued":
                self.auto_note.configure(text="片段已入队，下一轮后台检查会提炼写法。")

    def use_hotspot(self, platform, topic, *, count=None):
        if self.busy:
            return
        self.hotspot = (platform, topic)
        self.topic.delete(0, "end")
        self.topic.insert(0, topic)
        self.facts.delete("1.0", "end")
        self.targets.set(STYLES[platform][0])
        self.case_platform.set(STYLES[platform][0])
        self.refresh_cases()
        self.status.configure(text="已选平台热点。写稿时会尝试带入采集库匹配的报道。")
        if platform == "xiaohongshu":
            if count in (2, 3):
                self.count.set(str(count))
            if self.count.get() not in {"2", "3"}:
                self.count.set("2")
            self.generate_batch()

    def import_public_case(self):
        link = simpledialog.askstring("采集作品", "粘贴公开作品链接或分享文字", parent=self.window)
        if link:
            self.collect_cases(link)

    def collect_cases(self, link=None):
        topic = self.topic.get().strip()
        if not topic and not link:
            self.status.configure(text="先填写选题，或从平台热点选择一条。")
            return
        platform = self.names[self.case_platform.get()] if link else self.names.get(self.targets.get())
        if not platform:
            self.status.configure(text="采集案例前请先选择一个平台。")
            return
        self.collect.state(["disabled"])
        self.import_work.state(["disabled"])
        self.status.configure(text=f"正在查找{STYLES[platform][0]}公开作品…")

        def done(result, error):
            if not self.window.winfo_exists():
                return
            self.collect.state(["!disabled"])
            self.import_work.state(["!disabled"])
            self.refresh_cases()
            if error:
                message = str(error)
            elif result["saved"]:
                message = "写法已入库 · " + "、".join(result["saved"])
            else:
                message = "暂无新的可读作品。可以粘贴作品链接，或录入自己可访问的参考片段。"
            self.status.configure(text=message)
            if link:
                self.case_notes.configure(text=message)

        payload = {"topic": topic, "url": link or ""}
        self.start(lambda: run_worker("collect_writing_cases", dt.date.today(), OUTPUT_DIR, platform, payload), done)

    def generate_batch(self):
        if self.busy:
            return
        topic = self.topic.get().strip()
        if not topic:
            self.status.configure(text="请先填写选题。")
            self.topic.focus_set()
            return
        selected = self.targets.get()
        platforms = list(STYLES) if selected == "全部平台" else [self.names[selected]]
        payload = {"topic": topic, "facts": self.facts.get("1.0", "end-1c"), "platforms": platforms,
                   "count": int(self.count.get())}
        if self.hotspot and self.hotspot[1] == topic:
            payload["hotspot_platform"] = self.hotspot[0]
        note_job = payload.get("hotspot_platform") == "xiaohongshu" and platforms == ["xiaohongshu"]
        text_only = note_job and not self.note_images.get()
        if note_job:
            payload["text_only"] = text_only
        if note_job and payload["count"] not in (2, 3):
            self.count.set("2")
            payload["count"] = 2
        self.busy = True
        self.generate.state(["disabled"])
        self.status.configure(text="正在查找同题案例并生成宣传文案…" if text_only else
                              "正在查找同题图文、提炼写法、出稿与出图…" if note_job else "正在检索案例并生成文案…")

        def done(batch, error):
            if not self.window.winfo_exists():
                return
            self.busy = False
            self.generate.state(["!disabled"])
            if error:
                self.status.configure(text=f"本批生成失败 · {error}")
                return
            self.directory = Path(batch["directory"])
            successes, errors = batch["results"], batch["errors"]
            self.note_drafts = (successes.get("xiaohongshu") or {}).get("drafts", []) if note_job else []
            if self.note_drafts:
                self.refresh_cases()
                learned = len(batch.get("case_collection", {}).get("saved", []))
                cited = sum(bool(draft["reference"]) for draft in self.note_drafts)
                photos = sum(bool(picture["photo"]) for draft in self.note_drafts for picture in draft["pictures"])
                detail = (f"{cited} 版有同题参考" if cited else "暂无可核实的同题案例")
                media = "纯文案" if text_only else f"{photos} 张授权照片"
                self.status.configure(text=f"已出 {len(self.note_drafts)} 版 · 新增 {learned} 条写法 · {detail} · {media}")
                self.note_choice.configure(values=[f"{i:02d}  {draft['title']}" for i, draft in enumerate(self.note_drafts, 1)])
                self.note_choice.current(0)
                if not self.note_toolbar.winfo_manager():
                    self.note_toolbar.pack(fill="x", pady=(0, 7), before=self.note_text_area.master)
                self.show_note(reset=True)
                return
            self.note_toolbar.pack_forget()
            self.note_preview.pack_forget()
            self.status.configure(text=f"已完成 {len(successes)} 个平台，{len(errors)} 个平台生成失败。")
            sections = [Path(result["file"]).read_text(encoding="utf-8") for result in successes.values()]
            sections.extend(f"{STYLES[key][0]} · {message}" for key, message in errors.items())
            self._show_text("\n\n".join(sections))

        stage = "xiaohongshu_notes" if note_job else "copywriting"
        self.start(lambda: run_worker(stage, dt.date.today(), OUTPUT_DIR, payload=payload), done)

    def _show_text(self, text):
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.configure(state="disabled")
        self.result.see("1.0")

    def show_note(self, *, reset=False):
        draft = self.note_drafts[self.note_choice.current()]
        if reset:
            self.picture_index = 0
            case = draft["reference"]
            trace = (f"本版参考 · {case['title']}\n借鉴 · {draft['borrowed']}\n\n{case['evidence']}\n{case['url']}"
                     if case else "本版未找到贴合正文的案例写法。")
            self._show_text(f"{draft['title']}\n\n{draft['script']}\n\n{draft['caption']}\n\n{trace}")
            self.note_source.state(["!disabled"] if case else ["disabled"])
        pictures = draft["pictures"]
        if not pictures:
            self.note_preview.pack_forget()
            return
        if not self.note_preview.winfo_manager():
            self.note_preview.pack(side="left", fill="y", before=self.note_text_area)
        with Image.open(pictures[self.picture_index]["file"]) as image:
            image.thumbnail((155, 207), Image.Resampling.LANCZOS)
            self.thumbnail = ImageTk.PhotoImage(image)
        self.note_thumbnail.configure(image=self.thumbnail)
        self.picture_page.configure(text=f"{self.picture_index + 1} / {len(pictures)}")
        self.picture_back.state(["disabled"] if self.picture_index == 0 else ["!disabled"])
        self.picture_next.state(["disabled"] if self.picture_index == len(pictures) - 1 else ["!disabled"])

    def move_picture(self, change):
        self.picture_index += change
        self.show_note()

    def open_note_source(self):
        case = self.note_drafts[self.note_choice.current()]["reference"]
        if case:
            webbrowser.open(case["url"])

    def open_picture(self):
        picture = self.note_drafts[self.note_choice.current()]["pictures"][self.picture_index]
        os.startfile(picture["file"])

    def open_directory(self):
        directory = self.directory or OUTPUT_DIR / "copywriting"
        if directory.is_dir():
            os.startfile(directory)


class CaseDialog(simpledialog.Dialog):
    def __init__(self, parent, platform, start):
        self.platform = platform
        self.start = start
        super().__init__(parent, title=f"{STYLES[platform][0]} · 录入案例")

    def body(self, master):
        labels = ("案例名称", "HTTPS 出处", "热度依据（指标、时间或榜单）", "关键词", "可借鉴的写法摘要")
        self.inputs = []
        for label in labels:
            ttk.Label(master, text=label).pack(anchor="w", pady=(4, 0))
            entry = ttk.Entry(master, width=63)
            entry.pack(fill="x", pady=(2, 4))
            self.inputs.append(entry)
        ttk.Label(master, text="参考片段（可选，只保存提炼后的写法）").pack(anchor="w", pady=(6, 2))
        self.excerpt = tk.Text(master, width=63, height=5, wrap="word")
        self.excerpt.pack(fill="x")
        self.analyze = ttk.Button(master, text="AI 提炼写法", command=self.analyze_excerpt)
        self.analyze.pack(anchor="e", pady=5)
        ttk.Button(master, text="加入自动学习队列", command=self.queue_excerpt).pack(anchor="e", pady=(0, 5))
        return self.inputs[0]

    def queue_excerpt(self):
        title, url, evidence, keywords, _ = (entry.get() for entry in self.inputs)
        try:
            queue_case_excerpt(OUTPUT_DIR, self.platform, title, url, evidence, keywords,
                               self.excerpt.get("1.0", "end-1c"))
        except (ValueError, OSError) as error:
            messagebox.showerror("片段未入队", str(error), parent=self)
            return
        self.result = "queued"
        self.destroy()

    def analyze_excerpt(self):
        excerpt = self.excerpt.get("1.0", "end-1c").strip()
        if not 20 <= len(excerpt) <= 6000:
            messagebox.showinfo("参考片段", "请粘贴 20–6000 字的参考片段。", parent=self)
            return
        self.analyze.configure(text="正在提炼…", state="disabled")

        def done(result, error):
            if not self.winfo_exists():
                return
            self.analyze.configure(text="AI 提炼写法", state="normal")
            if error:
                messagebox.showerror("提炼未完成", str(error), parent=self)
                return
            for entry, key in zip(self.inputs[-2:], ("keywords", "notes")):
                entry.delete(0, "end")
                entry.insert(0, result[key])

        self.start(lambda: run_worker("analyze_case", dt.date.today(), OUTPUT_DIR, self.platform, excerpt), done)

    def validate(self):
        try:
            add_case(OUTPUT_DIR, self.platform, *(entry.get() for entry in self.inputs))
        except (ValueError, OSError) as error:
            messagebox.showerror("案例未保存", str(error), parent=self)
            return False
        self.result = True
        return True
