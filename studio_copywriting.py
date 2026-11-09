"""Desktop batch writing and source-backed case library."""

import datetime as dt
import os
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from config import OUTPUT_DIR
from core.copywriting import STYLES, add_case, list_cases
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
        self.cases = []
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
        self.generate = ttk.Button(bar, text="生成本批文案", style="Accent.TButton", command=self.generate_batch)
        self.generate.pack(side="right")
        self.status = ttk.Label(page, text="本机 AI 会按平台检索写法参考。", style="Muted.TLabel", wraplength=595)
        self.status.pack(anchor="w", pady=(0, 6))
        results = ttk.Frame(page)
        results.pack(fill="both", expand=True)
        self.result = tk.Text(results, wrap="word", padx=8, pady=6, state="disabled")
        scroll = ttk.Scrollbar(results, command=self.result.yview)
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
        self.refresh_cases()

    def refresh_cases(self):
        self.cases = list_cases(OUTPUT_DIR, self.names[self.case_platform.get()])
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

    def generate_batch(self):
        topic = self.topic.get().strip()
        if not topic:
            self.status.configure(text="请先填写选题。")
            self.topic.focus_set()
            return
        selected = self.targets.get()
        platforms = list(STYLES) if selected == "全部平台" else [self.names[selected]]
        payload = {"topic": topic, "facts": self.facts.get("1.0", "end-1c"), "platforms": platforms,
                   "count": int(self.count.get())}
        self.generate.state(["disabled"])
        self.status.configure(text="正在检索案例并生成文案…")

        def done(batch, error):
            if not self.window.winfo_exists():
                return
            self.generate.state(["!disabled"])
            if error:
                self.status.configure(text=f"本批生成失败 · {error}")
                return
            self.directory = Path(batch["directory"])
            successes, errors = batch["results"], batch["errors"]
            self.status.configure(text=f"已完成 {len(successes)} 个平台，{len(errors)} 个平台生成失败。")
            sections = [Path(result["file"]).read_text(encoding="utf-8") for result in successes.values()]
            sections.extend(f"{STYLES[key][0]} · {message}" for key, message in errors.items())
            self.result.configure(state="normal")
            self.result.delete("1.0", "end")
            self.result.insert("1.0", "\n\n".join(sections))
            self.result.configure(state="disabled")

        self.start(lambda: run_worker("copywriting", dt.date.today(), OUTPUT_DIR, payload=payload), done)

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
        return self.inputs[0]

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
