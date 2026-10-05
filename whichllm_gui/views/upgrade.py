"""升级页：对比当前机器与潜在显卡升级方案。"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ..context import choose_save_path, show_info
from ..engine import fmt_gb, gpu_name_suggestions
from ..export import upgrade_csv, upgrade_markdown
from ..widgets import Card, ChoiceField, Column, DetailPane, ItemList, SortedTree
from . import BaseView

PROFILE_OPTIONS = (
    ("general", "通用 general"),
    ("coding", "代码 coding"),
    ("vision", "视觉 vision"),
    ("math", "数学 math"),
    ("any", "全部 any"),
)


class UpgradeView(BaseView):
    key = "upgrade"
    title = "升级对比"

    def build(self) -> None:
        self._payload: dict | None = None

        card = Card(
            self,
            "显卡升级值不值？",
            subtitle="对应命令：whichllm upgrade <显卡…>",
        )
        card.pack(fill="x")

        body = ttk.Frame(card.body, style="Surface.TFrame")
        body.pack(fill="x")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        left = ttk.Frame(body, style="Surface.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        ttk.Label(
            left,
            text="要对比的显卡（可多个，支持 “RTX 5090”“2x RTX 4090”）",
            style="CardKey.TLabel",
        ).pack(anchor="w")
        self._gpu_list = ItemList(
            left, self.fonts, values=gpu_name_suggestions, height=6
        )
        self._gpu_list.pack(fill="both", expand=True, pady=(4, 0))

        right = ttk.Frame(body, style="Surface.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        grid = ttk.Frame(right, style="Surface.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="每个显卡取前 N 名", style="CardKey.TLabel").grid(
            row=0, column=0, sticky="w", pady=3
        )
        self._top_var = tk.IntVar(value=3)
        ttk.Spinbox(grid, from_=1, to=20, textvariable=self._top_var, width=8).grid(
            row=0, column=1, sticky="w", pady=3
        )

        ttk.Label(grid, text="上下文长度", style="CardKey.TLabel").grid(
            row=1, column=0, sticky="w", pady=3
        )
        self._context_var = tk.StringVar(value="8192")
        ttk.Entry(grid, textvariable=self._context_var, width=12).grid(
            row=1, column=1, sticky="w", pady=3
        )

        ttk.Label(grid, text="任务侧重", style="CardKey.TLabel").grid(
            row=2, column=0, sticky="w", pady=3
        )
        self._profile_field = ChoiceField(grid, PROFILE_OPTIONS, "general", width=18)
        self._profile_field.grid(row=2, column=1, sticky="w", pady=3)

        self._cpu_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            grid,
            text="与「仅 CPU」基线比较",
            variable=self._cpu_only_var,
            style="Surface.TCheckbutton",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        actions = ttk.Frame(right, style="Surface.TFrame")
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="开始对比", style="Accent.TButton", command=self.run_upgrade).pack(
            side="left"
        )
        ttk.Button(actions, text="导出 Markdown", style="Ghost.TButton", command=self._export_md).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="导出 CSV", style="Ghost.TButton", command=self._export_csv).pack(
            side="left"
        )
        self._status = ttk.Label(right, text="", style="CardKey.TLabel", wraplength=420, justify="left")
        self._status.pack(anchor="w", pady=(8, 0))

        ttk.Label(
            self,
            text="说明：模拟时保留当前的 CPU 与内存，只替换显卡，因此结果反映的是「换显卡能多跑什么」。",
            style="Dim.TLabel",
            wraplength=1000,
            justify="left",
        ).pack(fill="x", pady=(10, 4))

        table_frame = ttk.Frame(self, style="TFrame")
        table_frame.pack(fill="both", expand=True)
        self.table = SortedTree(
            table_frame,
            [
                Column("name", "方案", 130, "w", sort_key=lambda row: row["name"].lower()),
                Column("gpu", "显卡", 220, "w", stretch=True, sort_key=lambda row: row["gpu"].lower()),
                Column("vram", "显存", 88, "e", sort_key=lambda row: row["_vram"]),
                Column("model", "最佳模型", 220, "w", sort_key=lambda row: row["model"].lower()),
                Column("quality", "质量分", 80, "e", sort_key=lambda row: row["_quality"]),
                Column("speed", "速度 tok/s", 96, "e", sort_key=lambda row: row["_speed"]),
                Column("delta_q", "质量提升", 90, "e", sort_key=lambda row: row["_delta_q"]),
                Column("delta_s", "速度变化", 90, "e", sort_key=lambda row: row["_delta_s"]),
                Column("fit", "适配", 92, "center", sort_key=lambda row: row["fit"]),
            ],
            fonts=self.fonts,
            on_select=self._on_select,
            height=10,
        )
        self.table.pack(fill="both", expand=True)

        self.detail = DetailPane(self, self.fonts, mono=True, height=9, title="原始数据（JSON）")
        self.detail.pack(fill="both", expand=True, pady=(8, 0))

    # -- 生命周期 ---------------------------------------------------------- #

    def first_load(self) -> None:
        if not self._gpu_list.items():
            self._gpu_list.add("RTX 4090")

    def refresh(self) -> None:
        self.run_upgrade()

    def run_upgrade(self) -> None:
        targets = list(self._gpu_list.items())
        if not targets:
            self.ctx.toast("请先添加至少一个要对比的显卡。", "warn")
            return
        context = self._context_var.get().strip() or "8192"
        top = int(self._top_var.get() or 3)
        profile = self._profile_field.value()
        cpu_only = bool(self._cpu_only_var.get())

        def work(progress):
            return self.engine.upgrade(
                targets,
                context_length=context,
                top=top,
                profile=profile,
                cpu_only=cpu_only,
                progress=progress,
            )

        def done(payload: dict) -> None:
            self._payload = payload
            self._fill(payload)
            self._status.configure(text=f"已完成 {len(payload.get('targets') or [])} 个方案对比。")

        self.run_task("对比显卡升级", work, on_done=done)

    def _fill(self, payload: dict) -> None:
        current = payload.get("current") or {}
        rows = [self._row(current, "当前硬件", is_current=True)]
        for target in payload.get("targets") or []:
            rows.append(self._row(target, target.get("name") or "模拟方案"))
        self.table.set_rows(rows)
        self.detail.set_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def _row(self, payload: dict, name: str, *, is_current: bool = False) -> dict:
        quality = payload.get("top_quality")
        speed = payload.get("top_tok_s")
        delta_q = payload.get("delta_quality")
        delta_s = payload.get("delta_tok_s")
        fit = payload.get("top_fit") or ""
        quant = payload.get("top_quant") or ""
        tags = ["installed"] if is_current else [f"fit:{fit}" if fit else ""]
        return {
            "name": name,
            "gpu": payload.get("gpu") or "CPU",
            "vram": fmt_gb(payload.get("vram_gb", 0) * 1024**3) if payload.get("vram_gb") else "-",
            "model": payload.get("top_model") or "-",
            "quality": f"{quality:.1f}" if quality is not None else "-",
            "speed": f"{speed:.1f}" if speed is not None else "-",
            "delta_q": f"{delta_q:+.1f}" if delta_q is not None else "—",
            "delta_s": f"{delta_s:+.1f}" if delta_s is not None else "—",
            "fit": (fit + (f" {quant}" if quant else "")) or "-",
            "_vram": payload.get("vram_gb") or 0,
            "_quality": quality or 0,
            "_speed": speed or 0,
            "_delta_q": delta_q if delta_q is not None else -10**9,
            "_delta_s": delta_s if delta_s is not None else -10**9,
            "_tags": [tag for tag in tags if tag],
            "_obj": payload,
            "_id": name,
        }

    def _on_select(self, row: dict | None) -> None:
        if not row:
            return
        payload = row.get("_obj") or {}
        lines = [
            f"方案：{payload.get('name')}",
            f"显卡：{payload.get('gpu')}（{payload.get('vram_gb', 0):.1f} GB）",
            f"最佳模型：{payload.get('top_model')}",
            f"质量分：{payload.get('top_quality')}",
            f"速度：{payload.get('top_tok_s')} tok/s（置信度 {payload.get('top_speed_confidence')}）",
            f"运行适配：{payload.get('top_fit')}　量化：{payload.get('top_quant')}",
        ]
        if payload.get("delta_quality") is not None:
            lines.append(f"相对当前：质量 {payload['delta_quality']:+.1f}，速度 {payload['delta_tok_s']:+.1f} tok/s")
        self.detail.set_text("\n".join(lines))

    # -- 导出 -------------------------------------------------------------- #

    def _export_md(self) -> None:
        if not self._payload:
            self.ctx.toast("请先执行一次对比。", "warn")
            return
        path = choose_save_path(self, default_name="whichllm-upgrade.md", filetypes=[("Markdown", "*.md")])
        if not path:
            return
        Path(path).write_text(upgrade_markdown(self._payload), encoding="utf-8")
        show_info(self, f"已导出到：\n{path}")

    def _export_csv(self) -> None:
        if not self._payload:
            self.ctx.toast("请先执行一次对比。", "warn")
            return
        path = choose_save_path(self, default_name="whichllm-upgrade.csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        Path(path).write_text(upgrade_csv(self._payload), encoding="utf-8-sig")
        show_info(self, f"已导出到：\n{path}")
