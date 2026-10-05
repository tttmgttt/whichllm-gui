"""脚本页：为模型生成可直接运行的 Python 示例。"""

from __future__ import annotations

from pathlib import Path
from tkinter import ttk
from typing import Any

from ..context import choose_save_path, show_info
from ..engine import EngineError
from ..widgets import Card, ChoiceField, DetailPane, SuggestEntry
from . import BaseView

SNIPPET_QUANTS = (
    ("", "自动挑选"),
    ("Q3_K_M", "Q3_K_M"),
    ("Q4_K_M", "Q4_K_M"),
    ("Q4_K_S", "Q4_K_S"),
    ("Q5_K_M", "Q5_K_M"),
    ("Q6_K", "Q6_K"),
    ("Q8_0", "Q8_0"),
    ("F16", "F16"),
)


class SnippetView(BaseView):
    key = "snippet"
    title = "示例脚本"

    def build(self) -> None:
        self._loaded_suggestions = False
        self._model_id = ""
        self._deps: list[str] = []
        self._variant: str | None = None

        card = Card(self, "生成示例脚本", subtitle="对应命令：whichllm snippet <模型>")
        card.pack(fill="x")

        grid = ttk.Frame(card.body, style="Surface.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="模型名称", style="CardKey.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        self._model_field = SuggestEntry(grid, (), on_submit=self.run_snippet, width=44)
        self._model_field.grid(row=0, column=1, sticky="ew", pady=3)

        ttk.Label(grid, text="量化类型", style="CardKey.TLabel").grid(row=1, column=0, sticky="w", pady=3)
        self._quant_field = ChoiceField(grid, SNIPPET_QUANTS, "", width=18)
        self._quant_field.grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(
            grid,
            text="留空时按 whichllm 的偏好顺序挑选最合适的 GGUF 文件",
            style="CardKey.TLabel",
        ).grid(row=2, column=1, sticky="w", pady=(0, 4))

        actions = ttk.Frame(card.body, style="Surface.TFrame")
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="生成脚本", style="Accent.TButton", command=self.run_snippet).pack(
            side="left"
        )
        ttk.Button(actions, text="复制脚本", style="Ghost.TButton", command=self._copy_code).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="保存为 .py", style="Ghost.TButton", command=self._save_code).pack(
            side="left"
        )
        ttk.Button(
            actions, text="在新窗口运行", style="Ghost.TButton", command=self._run_model
        ).pack(side="left", padx=6)
        self._status = ttk.Label(actions, text="", style="CardKey.TLabel")
        self._status.pack(side="left", padx=8)

        self._hint = ttk.Label(
            self, text="", style="Dim.TLabel", wraplength=1000, justify="left"
        )
        self._hint.pack(fill="x", pady=(10, 4))

        self.code = DetailPane(self, self.fonts, mono=True, height=22)
        self.code.pack(fill="both", expand=True)
        self.code.set_text(
            "# 点击「生成脚本」后，这里会显示可直接运行的 Python 示例代码。\n"
            "# 带 GGUF 权重的模型会用 llama-cpp-python，其余用 transformers。"
        )

    # -- 生命周期 ---------------------------------------------------------- #

    def first_load(self) -> None:
        self._load_suggestions()

    def refresh(self) -> None:
        self.run_snippet()

    def prefill(self, **payload: Any) -> None:
        model = payload.get("model")
        if model:
            self._model_field.set_value(model)
        quant = payload.get("quant")
        if quant:
            self._quant_field.set_value(quant)
        self._load_suggestions()
        self.run_snippet()

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

    # -- 生成 -------------------------------------------------------------- #

    def run_snippet(self) -> None:
        model = self._model_field.value()
        quant = self._quant_field.value()

        def work(progress):
            return self.engine.snippet(model, quant=quant, progress=progress)

        def done(payload: dict) -> None:
            self._model_id = payload.get("model_id") or ""
            self._deps = list(payload.get("deps") or [])
            self._variant = payload.get("variant")
            self.code.set_text(payload.get("code") or "")
            dep_text = " ".join(f"--with {dep}" for dep in self._deps)
            hint = f"模型：{self._model_id}"
            if self._variant:
                hint += f"　GGUF 文件：{self._variant}"
            hint += "\n直接运行： whichllm run '" + self._model_id + "'"
            if dep_text:
                hint += f"\n手动运行： uv run --no-project {dep_text} script.py"
            self._hint.configure(text=hint)
            self._status.configure(text="已生成")

        self.run_task("生成脚本", work, on_done=done)

    # -- 操作 -------------------------------------------------------------- #

    def _copy_code(self) -> None:
        text = self.code.get_text()
        if not text.strip():
            self.ctx.toast("还没有可复制的脚本。", "warn")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.ctx.toast("脚本已复制到剪贴板。", "ok")

    def _save_code(self) -> None:
        text = self.code.get_text()
        if not text.strip():
            self.ctx.toast("还没有可保存的脚本。", "warn")
            return
        path = choose_save_path(self, default_name="run_model.py", filetypes=[("Python", "*.py")])
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")
        show_info(self, f"已保存到：\n{path}")

    def _run_model(self) -> None:
        model = self._model_field.value() or self._model_id
        quant = self._quant_field.value()
        try:
            command = self.engine.launch_run(model, quant=quant)
        except EngineError as exc:
            self.ctx.report_error(str(exc))
            return
        self.ctx.toast("已在新控制台窗口启动 whichllm run。", "ok")
        self._status.configure(text="已启动运行窗口")
        self._hint.configure(text=f"已启动命令：\n{command}")
