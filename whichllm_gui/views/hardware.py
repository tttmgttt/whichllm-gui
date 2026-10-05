"""硬件页：检测结果、当前生效的硬件视图、缓存与版本信息。"""

from __future__ import annotations

import time
from tkinter import ttk
from typing import Any

from ..context import show_info
from ..engine import EngineError, fmt_bytes, fmt_gb
from ..widgets import Card, DetailPane, ScrollFrame
from . import BaseView, gpu_lines, hardware_summary


class HardwareView(BaseView):
    key = "hardware"
    title = "硬件"

    def build(self) -> None:
        header = ttk.Frame(self, style="Surface.TFrame", padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="硬件检测结果", style="Title.TLabel").pack(side="left")
        ttk.Button(
            header, text="重新检测", style="Accent.TButton", command=self.reload
        ).pack(side="right")
        self._summary = ttk.Label(
            header, text="正在准备…", style="SurfaceDim.TLabel", wraplength=1000, justify="left"
        )
        self._summary.pack(fill="x", pady=(6, 0))

        self._scroll = ScrollFrame(self)
        self._scroll.pack(fill="both", expand=True, pady=(10, 0))
        self._content = self._scroll.inner
        self._content.columnconfigure(0, weight=1)

        self._detected_card = Card(self._content, "检测到的硬件")
        self._detected_card.pack(fill="x", pady=(0, 10))
        self._detected_box = ttk.Frame(self._detected_card.body, style="Surface.TFrame")
        self._detected_box.pack(fill="x")

        self._effective_card = Card(
            self._content, "当前生效的硬件视图", subtitle="（含推荐页里的模拟/覆盖）"
        )
        self._effective_card.pack(fill="x", pady=(0, 10))
        self._effective_box = ttk.Frame(self._effective_card.body, style="Surface.TFrame")
        self._effective_box.pack(fill="x")

        info_card = Card(self._content, "运行环境与缓存")
        info_card.pack(fill="x", pady=(0, 10))
        self._info_box = ttk.Frame(info_card.body, style="Surface.TFrame")
        self._info_box.pack(fill="x")
        ttk.Button(
            info_card.body, text="打开缓存目录", style="Ghost.TButton", command=self._open_cache
        ).pack(anchor="w", pady=(8, 0))

        self._fallback = DetailPane(self._content, self.fonts, mono=True, height=14)
        self._fallback.pack(fill="both", expand=True, pady=(0, 10))

    # -- 生命周期 ---------------------------------------------------------- #

    def first_load(self) -> None:
        self.reload()

    def refresh(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.engine.bootstrap()

        def work(progress):
            hardware = self.engine.hardware(refresh=True, progress=progress)
            effective = None
            error = None
            if self.engine.inprocess:
                filters = self.ctx.state.get("filters")
                try:
                    effective = self.engine.apply_overrides(filters) if filters else None
                except EngineError as exc:
                    error = str(exc)
            return hardware, effective, error

        def done(payload) -> None:
            hardware, effective, error = payload
            self.ctx.state["hardware"] = hardware
            self._summary.configure(text=hardware_summary(hardware))
            self._fill_detected(hardware)
            self._fill_effective(hardware, effective, error)
            self._fill_info()
            if not self.engine.inprocess:
                self._fallback.set_text(self._safe_cli_text())
            else:
                self._fallback.set_text("")

        self.run_task("检测硬件", work, on_done=done)

    def _safe_cli_text(self) -> str:
        try:
            return self.engine.hardware_text()
        except EngineError as exc:
            return f"无法读取硬件信息：\n{exc}"

    # -- 渲染 -------------------------------------------------------------- #

    def _clear(self, frame: ttk.Frame) -> None:
        for child in frame.winfo_children():
            child.destroy()

    def _add_rows(self, frame: ttk.Frame, rows: list[tuple[str, str]]) -> None:
        for index, (key, value) in enumerate(rows):
            ttk.Label(frame, text=key, style="CardKey.TLabel", width=16, anchor="w").grid(
                row=index, column=0, sticky="w", pady=2
            )
            ttk.Label(
                frame, text=value or "-", style="CardValue.TLabel", wraplength=760, justify="left"
            ).grid(row=index, column=1, sticky="w", pady=2, padx=(6, 0))
        frame.columnconfigure(1, weight=1)

    def _fill_detected(self, hardware: Any) -> None:
        self._clear(self._detected_box)
        rows = gpu_lines(hardware)
        rows.append(("CPU", f"{getattr(hardware, 'cpu_name', '未知')}（{getattr(hardware, 'cpu_cores', 0)} 核）"))
        features = []
        if getattr(hardware, "has_avx2", False):
            features.append("AVX2")
        if getattr(hardware, "has_avx512", False):
            features.append("AVX512")
        rows.append(("CPU 指令集", "、".join(features) if features else "未检测到"))
        rows.append(("内存总量", fmt_gb(getattr(hardware, "ram_bytes", 0))))
        disk = getattr(hardware, "disk_free_bytes", 0)
        if disk:
            rows.append(("磁盘剩余", fmt_gb(disk)))
        rows.append(("操作系统", getattr(hardware, "os", "") or "未知"))
        self._add_rows(self._detected_box, rows)

    def _fill_effective(self, detected: Any, effective: Any, error: str | None) -> None:
        self._clear(self._effective_box)
        if error:
            ttk.Label(
                self._effective_box,
                text=f"应用覆盖设置失败：{error}",
                style="CardValue.TLabel",
                wraplength=760,
                justify="left",
            ).pack(anchor="w")
            return
        if effective is None:
            ttk.Label(
                self._effective_box,
                text="尚未在「推荐」页执行过排序，这里显示的是检测到的硬件。",
                style="CardKey.TLabel",
            ).pack(anchor="w")
            effective = detected

        rows = gpu_lines(effective)
        rows.append(("内存总量", fmt_gb(getattr(effective, "ram_bytes", 0))))
        budget = getattr(effective, "ram_budget_bytes", None)
        rows.append(("内存预算", fmt_gb(budget) if budget else "未设置"))
        self._add_rows(self._effective_box, rows)

        notes = list(getattr(effective, "budget_notes", []) or [])
        if notes:
            ttk.Label(
                self._effective_box,
                text="预算说明：" + "；".join(notes),
                style="CardKey.TLabel",
                wraplength=760,
                justify="left",
            ).grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _fill_info(self) -> None:
        self._clear(self._info_box)
        info = self.engine.cache_info()
        rows = [
            ("whichllm 版本", self.engine.version or "未知"),
            ("运行模式", "进程内直连" if self.engine.inprocess else f"命令行降级（{self.engine.boot_error}）"),
            ("缓存目录", info.get("dir", "未知")),
        ]
        for item in info.get("files", []):
            age = time.time() - float(item["mtime"])
            rows.append(
                (
                    f"缓存 {item['name']}",
                    f"{fmt_bytes(item['size'])}，{_human_age(age)}前更新",
                )
            )
        if not info.get("files"):
            rows.append(("缓存文件", "还没有缓存（首次运行会在线抓取）"))
        self._add_rows(self._info_box, rows)

    def _open_cache(self) -> None:
        try:
            self.engine.open_cache_dir()
        except EngineError as exc:
            show_info(self, str(exc), title="无法打开缓存目录")


def _human_age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    if seconds < 3600:
        return f"{seconds / 60:.0f} 分钟"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时"
    return f"{seconds / 86400:.1f} 天"
