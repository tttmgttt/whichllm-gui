"""主窗口：菜单、标题栏、选项卡与状态栏。"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from typing import Any

from .context import AppContext, Settings, show_info
from .engine import Engine, EngineError
from . import __version__ as GUI_VERSION
from .theme import apply_theme
from .views.hardware import HardwareView
from .views.plan import PlanView
from .views.recommend import RecommendView
from .views.run import RunView
from .views.snippet import SnippetView
from .views.upgrade import UpgradeView
from .widgets import TaskRunner

ABOUT_TEXT = f"""whichllm 图形界面 v{GUI_VERSION}
基于 whichllm（Find the best LLM that runs on your hardware）

· 「推荐」页在进程内复用 whichllm 的排序管线：硬件只检测一次，
  之后每次调整筛选条件只需重新排序，因此响应是即时的。
· 导出的 JSON / Markdown 与 whichllm --json / --markdown 完全一致。
· 「运行」页会在新的控制台窗口里启动 whichllm run（交互式对话需要终端）。

快捷键：F5 / Ctrl+R 刷新当前页　Ctrl+1~6 切换页面　Ctrl+Q 退出
"""


class WhichLLMApp(tk.Tk):
    """应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.fonts = apply_theme(self)

        self.title("whichllm 图形界面 — 本地大模型选型助手")
        geometry = self.settings.get("window.geometry", "1360x860")
        self.geometry(geometry)
        self.minsize(1040, 640)

        self.engine = Engine()
        self.ctx = AppContext(
            root=self,
            engine=self.engine,
            fonts=self.fonts,
            settings=self.settings,
            set_status=self.set_status,
            toast=self.toast,
            report_error=self.report_error,
            goto=self.goto,
        )
        self.runner = TaskRunner(self, on_state=self._on_task_state)
        self.ctx.runner = self.runner

        self._views: dict[str, Any] = {}
        self._tab_order: list[str] = []

        self._build_menu()
        self._build_header()
        self._build_tabs()
        self._build_status()
        self._bind_shortcuts()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._load_runtime_info)
        self.after(200, self._show_initial_tab)

    # -- 界面搭建 ---------------------------------------------------------- #

    def _build_menu(self) -> None:
        menubar = tk.Menu(self, tearoff=False)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="重新检测硬件", command=self._reload_hardware)
        file_menu.add_command(label="重新抓取模型数据", command=self._refresh_models)
        file_menu.add_separator()
        file_menu.add_command(label="打开缓存目录", command=self._open_cache)
        file_menu.add_separator()
        file_menu.add_command(label="退出", accelerator="Ctrl+Q", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        for index in range(1, 7):
            view_menu.add_command(
                label=f"第 {index} 页", accelerator=f"Ctrl+{index}",
                command=lambda i=index: self._select_index(i - 1),
            )
        view_menu.add_separator()
        view_menu.add_command(label="刷新当前页", accelerator="F5", command=self._refresh_current)
        menubar.add_cascade(label="视图", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="关于", command=self._about)
        help_menu.add_command(
            label="打开 whichllm 项目主页",
            command=lambda: webbrowser.open("https://github.com/Andyyyy64/whichllm"),
        )
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.configure(menu=menubar)

    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Header.TFrame", padding=(14, 10))
        header.pack(fill="x")

        left = ttk.Frame(header, style="Header.TFrame")
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text=f"whichllm 图形界面 v{GUI_VERSION}", style="Title.TLabel").pack(anchor="w")
        self._runtime_label = ttk.Label(
            left,
            text="正在读取运行环境…",
            style="SurfaceDim.TLabel",
            wraplength=900,
            justify="left",
        )
        self._runtime_label.pack(anchor="w", pady=(2, 0))

        right = ttk.Frame(header, style="Header.TFrame")
        right.pack(side="right")
        ttk.Button(right, text="刷新当前页", style="Ghost.TButton", command=self._refresh_current).pack(
            side="right"
        )
        ttk.Button(right, text="关于", style="Ghost.TButton", command=self._about).pack(
            side="right", padx=6
        )

    def _build_tabs(self) -> None:
        container = ttk.Frame(self, style="TFrame", padding=(10, 8, 10, 0))
        container.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)

        for factory in (RecommendView, HardwareView, PlanView, UpgradeView, SnippetView, RunView):
            view = factory(self.notebook, self.ctx)
            self.notebook.add(view, text=view.title)
            self._views[view.key] = view
            self._tab_order.append(view.key)

        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._on_tab_changed())

    def _build_status(self) -> None:
        bar = ttk.Frame(self, style="Status.TFrame", padding=(12, 6))
        bar.pack(fill="x", side="bottom")

        self._progress = ttk.Progressbar(bar, mode="indeterminate", length=140)
        self._progress.pack(side="left", padx=(0, 10))

        self._status_label = ttk.Label(bar, text="就绪", style="SurfaceDim.TLabel")
        self._status_label.pack(side="left")

        self._mode_label = ttk.Label(bar, text="", style="SurfaceDim.TLabel")
        self._mode_label.pack(side="right")

    def _bind_shortcuts(self) -> None:
        self.bind("<F5>", lambda _e: self._refresh_current())
        self.bind("<Control-r>", lambda _e: self._refresh_current())
        self.bind("<Control-q>", lambda _e: self._on_close())
        for index in range(1, 7):
            self.bind(f"<Control-Key-{index}>", lambda _e, i=index: self._select_index(i - 1))

    # -- 状态栏 ------------------------------------------------------------ #

    def set_status(self, message: str) -> None:
        self._status_label.configure(text=message)

    def toast(self, message: str, kind: str = "info") -> None:
        prefix = {"ok": "✔ ", "warn": "! ", "danger": "✕ "}.get(kind, "· ")
        self.set_status(prefix + message)
        if kind in {"ok", "warn", "danger"}:
            self.after(6000, lambda: self.set_status("就绪"))

    def report_error(self, message: str) -> None:
        text = (message or "未知错误").strip()
        self.toast(text.splitlines()[0][:160], "danger")
        if len(text) > 600:
            text = text[:600] + "\n…（完整信息见下方）"
        messagebox.showerror("出错了", text, parent=self)

    def _on_task_state(self, busy: bool, label: str) -> None:
        if busy:
            self._progress.start(12)
            self.set_status(label or "正在处理…")
        else:
            self._progress.stop()
            if not self._status_label.cget("text").startswith(("✔", "!", "✕", "·")):
                # 保留 toast 提示，否则回到「就绪」
                self.set_status("就绪")

    # -- 启动信息 ---------------------------------------------------------- #

    def _load_runtime_info(self) -> None:
        def work(_progress):
            self.engine.bootstrap()
            return (
                self.engine.version,
                self.engine.inprocess,
                self.engine.boot_error,
                self.engine.cache_info(),
            )

        def done(payload) -> None:
            version, inprocess, boot_error, cache = payload
            self._mode_label.configure(
                text="运行模式：进程内直连" if inprocess else "运行模式：命令行降级"
            )
            files = cache.get("files") or []
            cache_text = (
                "缓存：" + "、".join(f"{item['name']}" for item in files) if files else "缓存：尚未生成"
            )
            self._runtime_label.configure(
                text=f"whichllm {version}　·　{cache_text}　·　{cache.get('dir', '')}"
            )
            if not inprocess:
                self.toast(f"内部接口不可用，已切换到命令行模式：{boot_error}", "warn")

        self.runner.submit("读取运行环境", work, on_done=done)

    def _show_initial_tab(self) -> None:
        self._on_tab_changed()

    def _on_tab_changed(self) -> None:
        key = self.current_key()
        view = self._views.get(key)
        if view is not None:
            view.on_show()

    # -- 页面切换 ---------------------------------------------------------- #

    def current_key(self) -> str:
        try:
            index = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return self._tab_order[0]
        return self._tab_order[index]

    def _select_index(self, index: int) -> None:
        if 0 <= index < len(self._tab_order):
            self.notebook.select(index)

    def goto(self, key: str, **payload: Any) -> None:
        if key not in self._views:
            return
        self.notebook.select(self._tab_order.index(key))
        view = self._views[key]
        if payload and hasattr(view, "prefill"):
            view.prefill(**payload)

    def _refresh_current(self) -> None:
        view = self._views.get(self.current_key())
        if view is not None:
            view.refresh()

    # -- 菜单动作 ---------------------------------------------------------- #

    def _reload_hardware(self) -> None:
        self.goto("hardware")
        view = self._views["hardware"]
        view.reload()

    def _refresh_models(self) -> None:
        self.goto("recommend")
        self._views["recommend"].refresh_models()

    def _open_cache(self) -> None:
        try:
            self.engine.open_cache_dir()
        except EngineError as exc:
            self.report_error(str(exc))

    def _about(self) -> None:
        cache = self.engine.cache_info()
        text = (
            f"{ABOUT_TEXT}\n\n"
            f"whichllm 版本：{self.engine.version or '未知'}\n"
            f"运行模式：{'进程内直连' if self.engine.inprocess else '命令行降级'}\n"
            f"缓存目录：{cache.get('dir', '未知')}\n"
            f"配置文件：{self.settings.path}\n"
        )
        show_info(self, text, title="关于")

    # -- 关闭 -------------------------------------------------------------- #

    def _on_close(self) -> None:
        try:
            self.settings.set("window.geometry", self.winfo_geometry())
            for key, view in self._views.items():
                if key == "recommend" and hasattr(view, "current_filters"):
                    self.settings.set("filters", view.current_filters().to_dict())
            self.settings.save()
        except Exception:  # noqa: BLE001
            pass
        self.runner.destroy()
        self.destroy()


def main() -> int:
    app = WhichLLMApp()
    app.mainloop()
    return 0
