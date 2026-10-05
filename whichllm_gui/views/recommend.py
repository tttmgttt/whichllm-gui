"""推荐页：硬件摘要 + 筛选条件 + 排序表格 + 详情面板。"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk
from typing import Any

from ..context import choose_directory, choose_save_path, show_info
from ..engine import (
    EngineError,
    RecommendFilters,
    evidence_label,
    fit_label,
    fmt_bytes,
    fmt_downloads,
    fmt_params_with_active,
    fmt_published,
    fmt_quality,
    fmt_speed,
    gpu_name_suggestions,
    parse_context_length,
)
from ..export import (
    details_text,
    results_csv,
    results_json,
    results_markdown,
)
from ..widgets import (
    Card,
    ChoiceField,
    Column,
    DetailPane,
    ItemList,
    ScrollFrame,
    SortedTree,
    SuggestEntry,
)
from . import BaseView, hardware_summary

QUANT_CHOICES = (
    "",
    "Q4_K_M",
    "Q4_K_S",
    "Q5_K_M",
    "Q6_K",
    "Q8_0",
    "Q3_K_M",
    "Q3_K_S",
    "Q2_K",
    "IQ4_XS",
    "IQ3_M",
    "NVFP4",
    "MXFP4",
    "F16",
    "BF16",
)

PROFILE_OPTIONS = (
    ("general", "通用 general"),
    ("coding", "代码 coding"),
    ("vision", "视觉 vision"),
    ("math", "数学 math"),
    ("any", "全部 any"),
)

FIT_OPTIONS = (
    ("any", "不限"),
    ("gpu", "能用 GPU 跑"),
    ("full-gpu", "完全装进显存"),
)

SPEED_OPTIONS = (
    ("any", "不限"),
    ("usable", "可用（≥10 tok/s）"),
    ("fast", "快速（≥30 tok/s）"),
)

EVIDENCE_OPTIONS = (
    ("any", "不限"),
    ("base", "直接测量 + 基础数据"),
    ("strict", "仅直接测量"),
)

HEADROOM_OPTIONS = (
    ("auto", "自动"),
    ("none", "不预留"),
    ("1GB", "预留 1 GB"),
    ("2GB", "预留 2 GB"),
    ("10%", "预留 10%"),
)

RAM_BUDGET_SUGGESTIONS = ("available", "8GB", "16GB", "24GB", "50%", "70%")


class RecommendView(BaseView):
    key = "recommend"
    title = "推荐"

    # -- 构建界面 ---------------------------------------------------------- #

    def build(self) -> None:
        self._last_result: Any = None
        self._filters_cache: RecommendFilters | None = self._load_filters()

        self._build_header()

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(10, 0))

        self._filter_scroll = ScrollFrame(body, width=330)
        body.add(self._filter_scroll, weight=0)
        self._build_filters(self._filter_scroll.inner)

        right = ttk.Panedwindow(body, orient="vertical")
        body.add(right, weight=1)
        self._build_results(right)

        self._apply_filters_to_widgets(self._filters_cache)

    # 顶部：硬件摘要（放在 Header 区）
    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Surface.TFrame", padding=12)
        header.pack(fill="x")

        top = ttk.Frame(header, style="Surface.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="为我推荐能跑的模型", style="Title.TLabel").pack(side="left")
        ttk.Button(
            top, text="重新检测硬件", style="Ghost.TButton", command=self._refresh_hardware
        ).pack(side="right")
        ttk.Button(
            top, text="开始推荐", style="Accent.TButton", command=self.run_recommend
        ).pack(side="right", padx=6)

        self._hardware_label = ttk.Label(
            header,
            text="正在准备…",
            style="SurfaceDim.TLabel",
            wraplength=1000,
            justify="left",
        )
        self._hardware_label.pack(fill="x", pady=(6, 0))

        self._result_label = ttk.Label(
            header,
            text="",
            style="SurfaceDim.TLabel",
            wraplength=1000,
            justify="left",
        )
        self._result_label.pack(fill="x", pady=(2, 0))

    # 左侧筛选面板
    def _build_filters(self, parent: tk.Misc) -> None:
        parent.columnconfigure(0, weight=1)

        # 排序与筛选
        card = Card(parent, "排序与筛选")
        card.pack(fill="x", pady=(0, 10))
        grid = ttk.Frame(card.body, style="Surface.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(grid, text="显示数量", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._top_var = tk.IntVar(value=10)
        ttk.Spinbox(grid, from_=1, to=200, textvariable=self._top_var, width=8).grid(
            row=row, column=1, sticky="w", pady=3
        )
        row += 1

        ttk.Label(grid, text="上下文长度", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._context_var = tk.StringVar(value="4096")
        ttk.Entry(grid, textvariable=self._context_var, width=12).grid(row=row, column=1, sticky="w", pady=3)
        ttk.Label(grid, text="如 4096 / 64k", style="CardKey.TLabel").grid(row=row, column=2, sticky="w", padx=6)
        row += 1

        ttk.Label(grid, text="量化类型", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._quant_field = SuggestEntry(grid, QUANT_CHOICES, width=14)
        self._quant_field.grid(row=row, column=1, sticky="ew", pady=3)
        row += 1

        ttk.Label(grid, text="任务侧重", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._profile_field = ChoiceField(grid, PROFILE_OPTIONS, "general", width=18)
        self._profile_field.grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        ttk.Label(grid, text="运行适配", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._fit_field = ChoiceField(grid, FIT_OPTIONS, "any", width=18)
        self._fit_field.grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        ttk.Label(grid, text="速度预设", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._speed_field = ChoiceField(grid, SPEED_OPTIONS, "any", width=18)
        self._speed_field.grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        ttk.Label(grid, text="最低速度", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._min_speed_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self._min_speed_var, width=12).grid(row=row, column=1, sticky="w", pady=3)
        ttk.Label(grid, text="tok/s", style="CardKey.TLabel").grid(row=row, column=2, sticky="w", padx=6)
        row += 1

        ttk.Label(grid, text="最小参数量", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._min_params_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self._min_params_var, width=12).grid(row=row, column=1, sticky="w", pady=3)
        ttk.Label(grid, text="单位 B（十亿）", style="CardKey.TLabel").grid(row=row, column=2, sticky="w", padx=6)
        row += 1

        ttk.Label(grid, text="基准证据", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._evidence_field = ChoiceField(grid, EVIDENCE_OPTIONS, "any", width=18)
        self._evidence_field.grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        self._backfill_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            grid,
            text="补全缺失的发布日期（需要联网）",
            variable=self._backfill_var,
            style="Surface.TCheckbutton",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 2))

        # 硬件模拟
        card = Card(parent, "硬件模拟 / 覆盖", subtitle="默认使用检测到的硬件")
        card.pack(fill="x", pady=(0, 10))
        box = ttk.Frame(card.body, style="Surface.TFrame")
        box.pack(fill="x")

        self._cpu_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            box,
            text="仅 CPU 模式（忽略显卡）",
            variable=self._cpu_only_var,
            style="Surface.TCheckbutton",
        ).pack(anchor="w", pady=(0, 6))

        ttk.Label(box, text="模拟 GPU（可多选，支持 “2x RTX 4090” 写法）", style="CardKey.TLabel").pack(
            anchor="w"
        )
        self._gpu_list = ItemList(
            box,
            self.fonts,
            values=gpu_name_suggestions,
            placeholder="（未模拟，使用检测到的显卡）",
            height=4,
        )
        self._gpu_list.pack(fill="x", pady=(2, 8))

        grid = ttk.Frame(box, style="Surface.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        row = 0
        for label, attr, hint in (
            ("显存上限", "_vram_var", "GB"),
            ("内存带宽", "_bandwidth_var", "GB/s"),
            ("GPU 序号", "_gpu_index_var", "多卡时指定"),
        ):
            ttk.Label(grid, text=label, style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar()
            setattr(self, attr, var)
            ttk.Entry(grid, textvariable=var, width=10).grid(row=row, column=1, sticky="w", pady=3)
            ttk.Label(grid, text=hint, style="CardKey.TLabel").grid(row=row, column=2, sticky="w", padx=6)
            row += 1

        ttk.Label(grid, text="显存预留", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._headroom_field = ChoiceField(grid, HEADROOM_OPTIONS, "auto", width=14)
        self._headroom_field.grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        ttk.Label(grid, text="内存预算", style="CardKey.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        self._ram_budget_field = SuggestEntry(grid, RAM_BUDGET_SUGGESTIONS, width=14)
        self._ram_budget_field.grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        # LM Studio
        card = Card(parent, "本地模型库（LM Studio）")
        card.pack(fill="x", pady=(0, 10))
        ttk.Label(
            card.body,
            text="添加 LM Studio 的模型目录后，已在本地下载的模型会被标记出来。",
            style="CardKey.TLabel",
            wraplength=280,
            justify="left",
        ).pack(anchor="w", pady=(0, 4))
        self._lm_list = ItemList(card.body, self.fonts, height=3)
        self._lm_list.pack(fill="x")
        ttk.Button(
            card.body, text="浏览目录并添加…", style="Ghost.TButton", command=self._add_lm_path
        ).pack(anchor="w", pady=(6, 0))

        # 操作
        actions = ttk.Frame(parent, style="TFrame")
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="重置筛选", style="Ghost.TButton", command=self.reset_filters).pack(
            fill="x"
        )
        ttk.Button(
            actions, text="重新抓取模型数据", style="Ghost.TButton", command=self.refresh_models
        ).pack(fill="x", pady=4)
        ttk.Button(
            actions, text="打开缓存目录", style="Ghost.TButton", command=self._open_cache
        ).pack(fill="x")

    # 右侧：结果表 + 详情
    def _build_results(self, parent: ttk.Panedwindow) -> None:
        table_frame = ttk.Frame(parent, style="TFrame")
        parent.add(table_frame, weight=3)

        toolbar = ttk.Frame(table_frame, style="TFrame")
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="搜索", style="Dim.TLabel").pack(side="left")
        self._search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self._search_var, width=28)
        search.pack(side="left", padx=6)
        self._search_var.trace_add("write", lambda *_: self._apply_search())
        self._count_label = ttk.Label(toolbar, text="0 个结果", style="Dim.TLabel")
        self._count_label.pack(side="left", padx=8)
        ttk.Button(toolbar, text="CSV", style="Ghost.TButton", command=lambda: self.export("csv")).pack(
            side="right"
        )
        ttk.Button(
            toolbar, text="Markdown", style="Ghost.TButton", command=lambda: self.export("markdown")
        ).pack(side="right", padx=4)
        ttk.Button(toolbar, text="JSON", style="Ghost.TButton", command=lambda: self.export("json")).pack(
            side="right"
        )

        columns = [
            Column("rank", "#", 44, "center", sort_key=lambda row: row["_rank"]),
            Column("model", "模型", 250, "w", stretch=True, sort_key=lambda row: row["model"].lower()),
            Column("params", "参数量", 92, "e", sort_key=lambda row: row["_params"]),
            Column("quant", "量化", 72, "center", sort_key=lambda row: row["quant"]),
            Column("size", "体积", 80, "e", sort_key=lambda row: row["_size"]),
            Column("vram", "显存需求", 90, "e", sort_key=lambda row: row["_vram"]),
            Column("speed", "速度 tok/s", 88, "e", sort_key=lambda row: row["_speed"]),
            Column("quality", "质量分", 80, "e", sort_key=lambda row: row["_quality"]),
            Column("fit", "适配", 82, "center", sort_key=lambda row: row["fit"]),
            Column("evidence", "证据", 84, "center", sort_key=lambda row: row["evidence"]),
            Column("downloads", "下载量", 78, "e", sort_key=lambda row: row["_downloads"]),
            Column("published", "发布", 84, "center", sort_key=lambda row: row["published"]),
        ]
        self.table = SortedTree(
            table_frame,
            columns,
            fonts=self.fonts,
            on_select=self._on_row_selected,
            on_activate=self._on_row_activate,
            height=14,
        )
        self.table.pack(fill="both", expand=True)

        detail_frame = ttk.Frame(parent, style="TFrame")
        parent.add(detail_frame, weight=2)

        buttons = ttk.Frame(detail_frame, style="TFrame")
        buttons.pack(fill="x", pady=(6, 4))
        for text, command in (
            ("复制模型名", self._copy_model),
            ("打开 HuggingFace", self._open_hf),
            ("去规划", lambda: self._goto("plan")),
            ("生成脚本", lambda: self._goto("snippet")),
            ("运行模型", lambda: self._goto("run")),
        ):
            ttk.Button(buttons, text=text, style="Ghost.TButton", command=command).pack(
                side="left", padx=(0, 6)
            )

        self.detail = DetailPane(detail_frame, self.fonts, height=12)
        self.detail.pack(fill="both", expand=True)
        self.detail.set_text("在上方选择一个模型查看详情。")

    # -- 筛选条件读写 ------------------------------------------------------ #

    def _load_filters(self) -> RecommendFilters:
        stored = self.settings.get("filters", {}) or {}
        try:
            return RecommendFilters.from_dict(stored)
        except Exception:  # noqa: BLE001
            return RecommendFilters()

    def _apply_filters_to_widgets(self, filters: RecommendFilters | None) -> None:
        filters = filters or RecommendFilters()
        self._top_var.set(filters.top)
        self._context_var.set(filters.context_length)
        self._quant_field.set_value(filters.quant)
        self._profile_field.set_value(filters.profile)
        self._fit_field.set_value(filters.fit)
        self._speed_field.set_value(filters.speed)
        self._min_speed_var.set(filters.min_speed)
        self._min_params_var.set(filters.min_params)
        self._evidence_field.set_value(filters.evidence)
        self._backfill_var.set(bool(filters.backfill_published))
        self._cpu_only_var.set(bool(filters.cpu_only))
        self._gpu_list.set_items(filters.simulate_gpus)
        self._vram_var.set(filters.vram)
        self._bandwidth_var.set(filters.bandwidth)
        self._gpu_index_var.set(filters.gpu_index)
        self._headroom_field.set_value(filters.vram_headroom)
        self._ram_budget_field.set_value(filters.ram_budget)
        self._lm_list.set_items(filters.lm_studio_paths)

    def current_filters(self) -> RecommendFilters:
        return RecommendFilters(
            top=int(self._top_var.get() or 10),
            context_length=self._context_var.get().strip() or "4096",
            quant=self._quant_field.value(),
            speed=self._speed_field.value(),
            min_speed=self._min_speed_var.get().strip(),
            fit=self._fit_field.value(),
            evidence=self._evidence_field.value(),
            min_params=self._min_params_var.get().strip(),
            profile=self._profile_field.value(),
            cpu_only=bool(self._cpu_only_var.get()),
            simulate_gpus=self._gpu_list.items(),
            vram=self._vram_var.get().strip(),
            bandwidth=self._bandwidth_var.get().strip(),
            gpu_index=self._gpu_index_var.get().strip(),
            vram_headroom=self._headroom_field.value(),
            ram_budget=self._ram_budget_field.value(),
            lm_studio_paths=self._lm_list.items(),
            backfill_published=bool(self._backfill_var.get()),
        )

    def reset_filters(self) -> None:
        self._apply_filters_to_widgets(RecommendFilters())
        self.ctx.toast("筛选条件已重置。", "info")

    def _add_lm_path(self) -> None:
        path = choose_directory(self)
        if path:
            self._lm_list.add(path)

    def _open_cache(self) -> None:
        try:
            self.engine.open_cache_dir()
        except EngineError as exc:
            self.ctx.report_error(str(exc))

    # -- 任务 -------------------------------------------------------------- #

    def first_load(self) -> None:
        self.run_recommend()

    def refresh(self) -> None:
        self.run_recommend()

    def _refresh_hardware(self) -> None:
        def work(progress):
            return self.engine.hardware(refresh=True, progress=progress)

        def done(hardware) -> None:
            self._hardware_label.configure(text=hardware_summary(hardware))
            self.ctx.state["hardware"] = hardware
            self.ctx.toast("硬件信息已重新检测。", "ok")
            self.run_recommend()

        self.run_task("检测硬件", work, on_done=done)

    def refresh_models(self) -> None:
        filters = self.current_filters()
        self.ctx.state["filters"] = filters

        def work(progress):
            self.engine.invalidate_models()
            return self.engine.recommend(filters, refresh=True, progress=progress)

        self._submit_recommend(work, label="重新抓取模型数据")

    def run_recommend(self) -> None:
        filters = self.current_filters()
        self.ctx.state["filters"] = filters
        self.settings.set("filters", filters.to_dict())
        self.settings.save()

        def work(progress):
            return self.engine.recommend(filters, progress=progress)

        self._submit_recommend(work, label="计算推荐")

    def _submit_recommend(self, work, *, label: str) -> None:
        def done(result) -> None:
            self._last_result = result
            self.ctx.state["hardware"] = result.hardware
            self.ctx.state["results"] = result.results
            self._hardware_label.configure(text=hardware_summary(result.hardware))
            self._fill_table(result)
            self._update_result_label(result)

        self.run_task(label, work, on_done=done)

    def _fill_table(self, result) -> None:
        rows = []
        for index, item in enumerate(result.results, 1):
            variant = getattr(item, "gguf_variant", None)
            tags = [f"fit:{getattr(item, 'fit_type', '')}"]
            if getattr(item, "local_path", None):
                tags.append("installed")
            if index == 1:
                tags.append("top1")
            file_size = getattr(variant, "file_size_bytes", 0) or 0
            rows.append(
                {
                    "rank": str(index),
                    "model": item.model.id,
                    "params": fmt_params_with_active(item.model),
                    "quant": getattr(variant, "quant_type", "") or "-",
                    "size": fmt_bytes(file_size) if file_size else "-",
                    "vram": fmt_bytes(getattr(item, "vram_required_bytes", None)),
                    "speed": fmt_speed(item),
                    "quality": fmt_quality(item),
                    "fit": fit_label(getattr(item, "fit_type", "")),
                    "evidence": evidence_label(getattr(item, "benchmark_status", "")),
                    "downloads": fmt_downloads(getattr(item.model, "downloads", 0)),
                    "published": fmt_published(getattr(item.model, "published_at", None)),
                    "_rank": index,
                    "_params": getattr(item.model, "parameter_count", 0) or 0,
                    "_size": file_size,
                    "_vram": getattr(item, "vram_required_bytes", 0) or 0,
                    "_speed": getattr(item, "estimated_tok_per_sec", 0) or 0,
                    "_quality": getattr(item, "quality_score", 0) or 0,
                    "_downloads": getattr(item.model, "downloads", 0) or 0,
                    "_tags": tags,
                    "_obj": item,
                    "_id": item.model.id,
                }
            )
        self.table.set_rows(rows)
        self.table.sort_by("rank", desc=False)
        self._apply_search()
        self.table.select_first()
        if not rows:
            message = result.empty_message or "没有符合条件的模型。"
            self.detail.set_text(message)

    def _apply_search(self) -> None:
        self.table.set_filter(self._search_var.get())
        visible = len(self.table.rows())
        total = len(self.table.all_rows())
        self._count_label.configure(
            text=f"{visible} / {total} 个结果" if visible != total else f"{total} 个结果"
        )

    def _update_result_label(self, result) -> None:
        parts = []
        if result.timings:
            parts.append(
                "耗时：" + "、".join(f"{k} {v:.2f}s" for k, v in result.timings.items())
            )
        if result.sources:
            parts.append("数据来源：" + "、".join(f"{k} {v}" for k, v in result.sources.items()))
        if result.notes:
            parts.extend(result.notes)
        if result.used_subprocess:
            parts.append("（降级模式：通过命令行调用）")
        self._result_label.configure(text="　|　".join(parts) if parts else "")

    # -- 选中与操作 -------------------------------------------------------- #

    def _selected_result(self) -> Any | None:
        row = self.table.get_selected()
        return row.get("_obj") if row else None

    def _on_row_selected(self, row: dict | None) -> None:
        if not row:
            return
        item = row.get("_obj")
        if item is not None:
            self.detail.set_text(details_text(item))

    def _on_row_activate(self, row: dict) -> None:
        item = row.get("_obj")
        if item is None:
            return
        self._goto("plan", model=item.model.id)

    def _copy_model(self) -> None:
        item = self._selected_result()
        if item is None:
            self.ctx.toast("请先选择一个模型。", "warn")
            return
        self.clipboard_clear()
        self.clipboard_append(item.model.id)
        self.ctx.toast(f"已复制：{item.model.id}", "ok")

    def _open_hf(self) -> None:
        item = self._selected_result()
        if item is None:
            self.ctx.toast("请先选择一个模型。", "warn")
            return
        artifact = getattr(item, "artifact_model", None)
        repo = getattr(artifact, "id", None) or item.model.id
        webbrowser.open(f"https://huggingface.co/{repo}")

    def _goto(self, target: str, **payload: Any) -> None:
        item = self._selected_result()
        if item is not None and "model" not in payload:
            payload["model"] = item.model.id
            variant = getattr(item, "gguf_variant", None)
            if variant is not None and getattr(variant, "quant_type", ""):
                payload["quant"] = variant.quant_type
        self.ctx.goto(target, **payload)

    # -- 导出 -------------------------------------------------------------- #

    def export(self, kind: str) -> None:
        result = self._last_result
        if result is None or not result.results:
            self.ctx.toast("还没有可导出的结果。", "warn")
            return

        stamp = "whichllm-recommend"
        try:
            if kind == "json":
                payload = results_json(self.engine, result.hardware, result.results)
                path = choose_save_path(
                    self, default_name=f"{stamp}.json", filetypes=[("JSON", "*.json")]
                )
                if not path:
                    return
                Path(path).write_text(
                    __import__("json").dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            elif kind == "markdown":
                text = results_markdown(
                    self.engine, result.hardware, result.results, empty_message=result.empty_message
                )
                path = choose_save_path(
                    self, default_name=f"{stamp}.md", filetypes=[("Markdown", "*.md")]
                )
                if not path:
                    return
                Path(path).write_text(text, encoding="utf-8")
            else:
                text = results_csv(result.results)
                path = choose_save_path(
                    self, default_name=f"{stamp}.csv", filetypes=[("CSV", "*.csv")]
                )
                if not path:
                    return
                Path(path).write_text(text, encoding="utf-8-sig")
        except EngineError as exc:
            self.ctx.report_error(str(exc))
            return
        except OSError as exc:
            self.ctx.report_error(f"写入文件失败：{exc}")
            return

        self.ctx.toast(f"已导出：{path}", "ok")
        show_info(self, f"已导出到：\n{path}")

    # -- 供其它页面跳转 ---------------------------------------------------- #

    def focus_model(self, model_id: str) -> None:
        for row in self.table.all_rows():
            if row.get("_id") == model_id:
                self.table.set_filter("")
                self._search_var.set(model_id)
                return

    def context_length_value(self) -> int:
        try:
            return parse_context_length(self._context_var.get())
        except EngineError:
            return 4096


__all__ = ["RecommendView"]
