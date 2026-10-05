"""应用级共享状态、配置持久化与通用弹窗。"""

from __future__ import annotations

import json
import os
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from .engine import Engine
from .theme import PALETTE, Fonts, style_text_widget
from .widgets import TaskRunner


def settings_path() -> Path:
    """配置文件位置（Windows 用 %APPDATA%，其他平台用 ~/.config）。"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "whichllm-gui" / "settings.json"


class Settings:
    """极简 JSON 配置存储。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings_path()
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except Exception:  # noqa: BLE001 - 配置损坏时回退到默认值
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data.get(key, default)
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 - 保存失败不应影响使用
            pass


@dataclass
class AppContext:
    """视图之间共享的上下文。"""

    root: tk.Misc
    engine: Engine
    fonts: Fonts
    settings: Settings
    state: dict[str, Any] = field(default_factory=dict)
    runner: TaskRunner | None = None
    set_status: Callable[[str], None] = lambda _message: None
    toast: Callable[[str, str], None] = lambda _message, _kind="info": None
    report_error: Callable[[str], None] = lambda _message: None
    goto: Callable[..., None] = lambda *_args, **_kwargs: None


# --------------------------------------------------------------------------- #
# 弹窗
# --------------------------------------------------------------------------- #


def choose_save_path(
    root: tk.Misc, *, default_name: str, filetypes: list[tuple[str, str]]
) -> str | None:
    return filedialog.asksaveasfilename(
        parent=root,
        title="导出为…",
        initialfile=default_name,
        defaultextension=filetypes[0][1],
        filetypes=filetypes + [("所有文件", "*.*")],
    )


def choose_directory(root: tk.Misc) -> str | None:
    return filedialog.askdirectory(parent=root, title="选择目录") or None


def show_error(root: tk.Misc, message: str, *, title: str = "出错了") -> None:
    messagebox.showerror(title, message, parent=root)


def show_info(root: tk.Misc, message: str, *, title: str = "提示") -> None:
    messagebox.showinfo(title, message, parent=root)


class TextDialog(tk.Toplevel):
    """用于展示/复制长文本（例如生成的脚本、报告）的窗口。"""

    def __init__(
        self,
        root: tk.Misc,
        fonts: Fonts,
        *,
        title: str,
        content: str,
        mono: bool = False,
        save_name: str | None = None,
    ) -> None:
        super().__init__(root)
        self.title(title)
        self.configure(bg=PALETTE["bg"])
        self.fonts = fonts
        self.save_name = save_name
        self.transient(root)
        self.geometry("880x640")

        toolbar = ttk.Frame(self, style="Surface.TFrame", padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="复制全部", command=self._copy).pack(side="left")
        if save_name:
            ttk.Button(toolbar, text="另存为…", command=self._save).pack(side="left", padx=6)
        ttk.Button(toolbar, text="关闭", style="Ghost.TButton", command=self.destroy).pack(
            side="right"
        )

        wrapper = ttk.Frame(self, style="TFrame")
        wrapper.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        text = tk.Text(wrapper)
        style_text_widget(text, fonts, mono=mono)
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        text.insert("1.0", content)
        text.configure(state="disabled")
        self._text = text
        self.bind("<Escape>", lambda _e: self.destroy())

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._text.get("1.0", "end-1c"))

    def _save(self) -> None:
        path = choose_save_path(
            self,
            default_name=self.save_name or "export.txt",
            filetypes=[("文本文件", "*.txt"), ("Markdown", "*.md")],
        )
        if not path:
            return
        Path(path).write_text(self._text.get("1.0", "end-1c"), encoding="utf-8")
        show_info(self, f"已保存到：\n{path}")
