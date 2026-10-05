"""规划页：某个模型需要多大显存、哪些显卡能跑。"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from ..context import choose_save_path, show_info
from ..engine import fit_label, fmt_bytes, fmt_params
from ..export import plan_csv, plan_markdown
from ..widgets import Card, ChoiceField, Column, DetailPane, SortedTree, SuggestEntry
from . import BaseView

PLAN_QUANTS = (
    ("", "默认 Q4_K_M"),
    ("Q2_K", "Q2_K"),
    ("Q3_K_M", "Q3_K_M"),
    ("Q4_K_M", "Q4_K_M"),
    ("Q5_K_M", "Q5_K_M"),
    ("Q6_K", "Q6_K"),
    ("Q8_0", "Q8_0"),
    ("F16", "F16（半精度）"),
)


class PlanView(BaseView):
    key = "plan"
    title = "规划"

    def build(self) -> None:
        self._payload: dict | None = None
        self._suggestions_loaded = False

        form = Card(self, "为指定模型做运行规划", subtitle="对应命令：whichllm plan <模型>")
        form.pack(fill="x")

        grid = ttk.Frame(form.body, style="Surface.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="模型名称", style="CardKey.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        self._model_field = SuggestEntry(grid, (), on_submit=self.run_plan, width=44)
        self._model_field.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(
            grid,
            text="支持 HuggingFace 仓库 ID，例如 Qwen/Qwen3-8B；留空则用第一个带 GGUF 的模型",
            style="CardKey.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=1, column=1, sticky="w", pady=(0, 4))

        ttk.Label(grid, text="上下文长度", style="CardKey.TLabel").grid(row=2, column=0, sticky="w", pady=3)
        self._context_var = tk.StringVar(value="4096")
        ttk.Entry(grid, textvariable=self._context_var, width=14).grid(row=2, column=1, sticky="w", pady=3)

        ttk.Label(grid, text="目标量化", style="CardKey.TLabel").grid(row=3, column=0, sticky="w", pady=3)
        self._quant_field = ChoiceField(grid, PLAN_QUANTS, "", width=18)
        self._quant_field.grid(row=3, column=1, sticky="w", pady=3)

        actions = ttk.Frame(form.body, style="Surface.TFrame")
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="开始规划", style="Accent.TButton", command=self.run_plan).pack(
            side="left"
        )
        ttk.Button(actions, text="导出 Markdown", style="Ghost.TButton", command=self._export_md).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="导出 CSV", style="Ghost.TButton", command=self._export_csv).pack(
            side="left"
        )
        self._status = ttk.Label(actions, text="", style="CardKey.TLabel")
        self._status.pack(side="left", padx=10)

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, pady=(10, 0))

        # 量化档位
        quant_tab = ttk.Frame(self._notebook, style="TFrame", padding=8)
        self._notebook.add(quant_tab, text="量化档位与显存")
        self.quant_table = SortedTree(
            quant_tab,
            [
                Column("quant", "量化", 110, "center", sort_key=lambda row: row["quant"]),
                Column("vram", "需要显存", 120, "e", sort_key=lambda row: row["_vram"]),
                Column("loss", "质量损失", 100, "e", sort_key=lambda row: row["_loss"]),
                Column("fit", "相对你的硬件", 160, "center", sort_key=lambda row: row["fit"]),
                Column("target", "目标档位", 90, "center", sort_key=lambda row: row["target"]),
            ],
            fonts=self.fonts,
            height=10,
        )
        self.quant_table.pack(fill="both", expand=True)

        # 显卡兼容
        gpu_tab = ttk.Frame(self._notebook, style="TFrame", padding=8)
        self._notebook.add(gpu_tab, text="显卡兼容性")
        self.gpu_table = SortedTree(
            gpu_tab,
            [
                Column("gpu", "显卡", 200, "w", stretch=True, sort_key=lambda row: row["gpu"].lower()),
                Column("vram", "显存", 90, "e", sort_key=lambda row: row["_vram"]),
                Column("fit", "适配", 110, "center", sort_key=lambda row: row["fit"]),
                Column("speed", "预估速度 tok/s", 130, "e", sort_key=lambda row: row["_speed"]),
            ],
            fonts=self.fonts,
            height=12,
        )
        self.gpu_table.pack(fill="both", expand=True)

        # 原始 JSON
        json_tab = ttk.Frame(self._notebook, style="TFrame", padding=8)
        self._notebook.add(json_tab, text="原始数据（JSON）")
        self.json_pane = DetailPane(json_tab, self.fonts, mono=True, height=20)
        self.json_pane.pack(fill="both", expand=True)
        ttk.Button(
            json_tab, text="复制 JSON", style="Ghost.TButton", command=self._copy_json
        ).pack(anchor="e", pady=(6, 0))

        self._summary = ttk.Label(
            self, text="", style="Dim.TLabel", wraplength=1000, justify="left"
        )
        self._summary.pack(fill="x", pady=(8, 0))

    # -- 生命周期 ---------------------------------------------------------- #

    def first_load(self) -> None:
        self._load_suggestions()

    def refresh(self) -> None:
        self.run_plan()

    def prefill(self, **payload: Any) -> None:
        model = payload.get("model")
        if model:
            self._model_field.set_value(model)
        quant = payload.get("quant")
        if quant:
            self._quant_field.set_value(quant)
        self._load_suggestions()
        self.run_plan()

    def _load_suggestions(self) -> None:
        if self._suggestions_loaded:
            return
        self._suggestions_loaded = True

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

    def run_plan(self) -> None:
        model = self._model_field.value()
        context = self._context_var.get().strip() or "4096"
        quant = self._quant_field.value()

        def work(progress):
            return self.engine.plan(
                model, context_length=context, quant=quant, progress=progress
            )

        def done(payload: dict) -> None:
            self._payload = payload
            self._fill(payload)

        self.run_task("运行规划", work, on_done=done)

    def _fill(self, payload: dict) -> None:
        model = payload.get("model") or {}
        target_quant = (payload.get("target_quant") or "").upper()
        context = payload.get("context_length")
        self._status.configure(text=f"已计算：{model.get('id', '')}")

        self._summary.configure(
            text=(
                f"模型：{model.get('id', '-')}　参数量：{fmt_params(model.get('parameter_count'))}"
                f"　架构：{model.get('architecture') or '-'}　许可证：{model.get('license') or '-'}"
                f"　上下文：{context} tokens　目标量化：{target_quant}"
            )
        )

        available = self._available_vram()
        rows = []
        for quant, entry in (payload.get("vram_by_quant") or {}).items():
            vram = entry.get("vram_bytes") or 0
            loss = entry.get("quality_loss") or 0.0
            if available:
                if vram <= available:
                    fit = "可以完全放进显存"
                    tags = ["fit:full_gpu"]
                elif vram <= available * 2.5:
                    fit = "需要部分卸载到内存"
                    tags = ["fit:partial_offload"]
                else:
                    fit = "显存不足"
                    tags = ["fit:too_small"]
            else:
                fit = "仅 CPU"
                tags = ["fit:cpu_only"]
            rows.append(
                {
                    "quant": quant,
                    "vram": fmt_bytes(vram),
                    "loss": f"{loss * 100:.1f}%",
                    "fit": fit,
                    "target": "✔" if quant == target_quant else "",
                    "_vram": vram,
                    "_loss": loss,
                    "_tags": tags,
                }
            )
        self.quant_table.set_rows(rows)

        gpu_rows = []
        for gpu in payload.get("gpu_compatibility") or []:
            speed = gpu.get("estimated_tok_per_sec")
            gpu_rows.append(
                {
                    "gpu": gpu.get("name") or "-",
                    "vram": f"{gpu.get('vram_gb', 0)} GB",
                    "fit": fit_label(gpu.get("fit_type")),
                    "speed": f"{speed:.1f}" if speed else "-",
                    "_vram": gpu.get("vram_gb", 0),
                    "_speed": speed or 0,
                    "_tags": [f"fit:{gpu.get('fit_type', '')}"],
                }
            )
        self.gpu_table.set_rows(gpu_rows)

        self.json_pane.set_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def _available_vram(self) -> float:
        hardware = self.ctx.state.get("hardware")
        if hardware is None:
            return 0.0
        gpus = list(getattr(hardware, "gpus", []) or [])
        if not gpus:
            return 0.0
        best = 0
        for gpu in gpus:
            usable = getattr(gpu, "usable_vram_bytes", None) or getattr(gpu, "vram_bytes", 0)
            best = max(best, usable or 0)
        return float(best)

    # -- 导出 -------------------------------------------------------------- #

    def _copy_json(self) -> None:
        text = self.json_pane.get_text()
        if not text:
            self.ctx.toast("还没有可复制的内容。", "warn")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.ctx.toast("已复制 JSON。", "ok")

    def _export_md(self) -> None:
        if not self._payload:
            self.ctx.toast("请先执行一次规划。", "warn")
            return
        path = choose_save_path(self, default_name="whichllm-plan.md", filetypes=[("Markdown", "*.md")])
        if not path:
            return
        Path(path).write_text(plan_markdown(self._payload), encoding="utf-8")
        show_info(self, f"已导出到：\n{path}")

    def _export_csv(self) -> None:
        if not self._payload:
            self.ctx.toast("请先执行一次规划。", "warn")
            return
        path = choose_save_path(self, default_name="whichllm-plan.csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        Path(path).write_text(plan_csv(self._payload), encoding="utf-8-sig")
        show_info(self, f"已导出到：\n{path}")
