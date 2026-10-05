"""运行页：下载模型并在终端里与它对话。"""

from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk
from typing import Any

from ..engine import EngineError
from ..widgets import Card, ChoiceField, DetailPane, SuggestEntry
from . import BaseView

RUN_QUANTS = (
    ("", "自动挑选"),
    ("Q3_K_M", "Q3_K_M"),
    ("Q4_K_M", "Q4_K_M"),
    ("Q5_K_M", "Q5_K_M"),
    ("Q6_K", "Q6_K"),
    ("Q8_0", "Q8_0"),
)


class RunView(BaseView):
    key = "run"
    title = "运行"

    def build(self) -> None:
        self._loaded_suggestions = False

        card = Card(self, "下载并对话", subtitle="对应命令：whichllm run <模型>")
        card.pack(fill="x")
        ttk.Label(
            card.body,
            text=(
                "whichllm 的对话是交互式的，需要真正的终端窗口。点击下面的按钮会在新的\n"
                "控制台窗口里启动它：首次运行会下载 GGUF 权重，请留意磁盘空间与网速。"
            ),
            style="CardKey.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        grid = ttk.Frame(card.body, style="Surface.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="模型名称", style="CardKey.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        self._model_field = SuggestEntry(grid, (), on_submit=self._launch, width=44)
        self._model_field.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(
            grid,
            text="留空则让 whichllm 自动挑选当前硬件上最合适的模型",
            style="CardKey.TLabel",
        ).grid(row=1, column=1, sticky="w", pady=(0, 4))

        ttk.Label(grid, text="上下文长度", style="CardKey.TLabel").grid(row=2, column=0, sticky="w", pady=3)
        self._context_var = tk.StringVar(value="4096")
        ttk.Entry(grid, textvariable=self._context_var, width=14).grid(row=2, column=1, sticky="w", pady=3)

        ttk.Label(grid, text="量化类型", style="CardKey.TLabel").grid(row=3, column=0, sticky="w", pady=3)
        self._quant_field = ChoiceField(grid, RUN_QUANTS, "", width=18)
        self._quant_field.grid(row=3, column=1, sticky="w", pady=3)

        self._cpu_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            grid,
            text="仅 CPU 模式",
            variable=self._cpu_only_var,
            style="Surface.TCheckbutton",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        actions = ttk.Frame(card.body, style="Surface.TFrame")
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="在新控制台窗口运行", style="Accent.TButton", command=self._launch).pack(
            side="left"
        )
        ttk.Button(actions, text="复制命令", style="Ghost.TButton", command=self._copy_command).pack(
            side="left", padx=6
        )
        self._status = ttk.Label(actions, text="", style="CardKey.TLabel")
        self._status.pack(side="left", padx=8)

        self.command_pane = DetailPane(self, self.fonts, mono=True, height=6, title="将要执行的命令")
        self.command_pane.pack(fill="both", expand=True, pady=(10, 0))

        self.tips = DetailPane(self, self.fonts, height=10, title="小贴士")
        self.tips.pack(fill="both", expand=True, pady=(10, 0))
        self.tips.set_text(
            "· 想先看推荐结果？到「推荐」页选好模型后点「运行模型」会自动带到这里。\n"
            "· 显存不够时可以在「推荐」页开启「部分卸载」，运行时 whichllm 会自动把部分层放到内存。\n"
            "· 模型文件默认下载到 HuggingFace 缓存目录（Windows 通常是 C:\\Users\\<你>\\.cache\\huggingface）。\n"
            "· 关掉那个控制台窗口即可结束对话。"
        )

    # -- 生命周期 ---------------------------------------------------------- #

    def first_load(self) -> None:
        self._load_suggestions()
        self._update_preview()

    def refresh(self) -> None:
        self._update_preview()

    def prefill(self, **payload: Any) -> None:
        model = payload.get("model")
        if model:
            self._model_field.set_value(model)
        quant = payload.get("quant")
        if quant:
            self._quant_field.set_value(quant)
        self._load_suggestions()
        self._update_preview()

    def _load_suggestions(self) -> None:
        if self._loaded_suggestions:
            return
        self._loaded_suggestions = True

        def work(_progress):
            return self.engine.model_id_suggestions()

        def done(ids) -> None:
            if ids:
                self._model_field.set_values(ids)

        self.run_task(
            "读取模型候选",
            work,
            on_done=done,
            on_error=lambda _exc: None,
            channel="suggestions",
        )

    # -- 执行 -------------------------------------------------------------- #

    def _update_preview(self) -> None:
        try:
            args = self.engine.run_command_preview(
                self._model_field.value(),
                quant=self._quant_field.value(),
                context_length=self._context_var.get().strip() or "4096",
                cpu_only=bool(self._cpu_only_var.get()),
            )
        except EngineError as exc:
            self.command_pane.set_text(f"参数有误：{exc}")
            return
        self.command_pane.set_text(subprocess.list2cmdline(args))

    def _copy_command(self) -> None:
        self._update_preview()
        self.clipboard_clear()
        self.clipboard_append(self.command_pane.get_text())
        self.ctx.toast("命令已复制到剪贴板。", "ok")

    def _launch(self) -> None:
        try:
            command = self.engine.launch_run(
                self._model_field.value(),
                quant=self._quant_field.value(),
                context_length=self._context_var.get().strip() or "4096",
                cpu_only=bool(self._cpu_only_var.get()),
            )
        except EngineError as exc:
            self.ctx.report_error(str(exc))
            return
        self.command_pane.set_text(command)
        self._status.configure(text="已启动新窗口")
        self.ctx.toast("已在新控制台窗口启动 whichllm run。", "ok")
