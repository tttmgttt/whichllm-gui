"""界面视图（每个选项卡一个页面）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from ..context import AppContext
from ..engine import fmt_bytes, fmt_gb


class BaseView(ttk.Frame):
    """视图基类：统一处理后台任务、错误提示与首次加载。"""

    key = ""
    title = ""

    def __init__(self, master: tk.Misc, ctx: AppContext) -> None:
        super().__init__(master, style="TFrame", padding=10)
        self.ctx = ctx
        self.fonts = ctx.fonts
        self.engine = ctx.engine
        self.settings = ctx.settings
        self._loaded = False
        self.build()

    # -- 生命周期 ---------------------------------------------------------- #

    def build(self) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def on_show(self) -> None:
        if not self._loaded:
            self._loaded = True
            self.first_load()

    def first_load(self) -> None:
        """第一次切换到本页时调用。"""

    def refresh(self) -> None:
        """F5 / Ctrl+R：重新获取本页数据。"""

    def prefill(self, **payload: Any) -> None:
        """其它页面跳转过来时带入的参数。"""

    # -- 工具 -------------------------------------------------------------- #

    def run_task(
        self,
        label: str,
        work: Callable[[Callable[[str], None]], Any],
        *,
        on_done: Callable[[Any], None],
        on_error: Callable[[BaseException], None] | None = None,
        channel: str | None = None,
    ) -> None:
        if self.ctx.runner is None:
            return

        def handle_error(exc: BaseException) -> None:
            if on_error is not None:
                on_error(exc)
            else:
                self.ctx.report_error(str(exc))

        self.ctx.runner.submit(
            label,
            work,
            on_done=on_done,
            on_error=handle_error,
            channel=channel or self.key,
        )


def hardware_summary(hardware: Any) -> str:
    """一行式硬件摘要。"""
    parts: list[str] = []
    gpus = list(getattr(hardware, "gpus", []) or [])
    if gpus:
        for index, gpu in enumerate(gpus):
            usable = getattr(gpu, "usable_vram_bytes", None) or getattr(gpu, "vram_bytes", 0)
            bandwidth = getattr(gpu, "memory_bandwidth_gbps", None)
            text = f"{getattr(gpu, 'name', '未知 GPU')}（{fmt_gb(usable)}"
            if bandwidth:
                text += f" / {bandwidth:.0f} GB/s"
            text += "）"
            prefix = f"GPU {index}" if len(gpus) > 1 else "GPU"
            parts.append(f"{prefix}：{text}")
    else:
        parts.append("GPU：无（仅 CPU 模式）")

    cpu = getattr(hardware, "cpu_name", "") or "未知 CPU"
    cores = getattr(hardware, "cpu_cores", 0) or 0
    parts.append(f"CPU：{cpu}（{cores} 核）")
    parts.append(f"内存：{fmt_gb(getattr(hardware, 'ram_bytes', 0))}")
    budget = getattr(hardware, "ram_budget_bytes", None)
    if budget:
        parts.append(f"内存预算：{fmt_gb(budget)}")
    parts.append(f"系统：{getattr(hardware, 'os', '') or '未知'}")
    return "　·　".join(parts)


def gpu_lines(hardware: Any) -> list[tuple[str, str]]:
    """硬件卡片用的键值对列表。"""
    rows: list[tuple[str, str]] = []
    for index, gpu in enumerate(getattr(hardware, "gpus", []) or []):
        raw_vram = getattr(gpu, "vram_bytes", 0)
        usable = getattr(gpu, "usable_vram_bytes", None)
        rows.append((f"GPU {index} 型号", getattr(gpu, "name", "未知")))
        rows.append(
            (
                f"GPU {index} 显存",
                f"{fmt_bytes(usable)} 可用 / {fmt_bytes(raw_vram)} 总量"
                if usable
                else fmt_bytes(raw_vram),
            )
        )
        bandwidth = getattr(gpu, "memory_bandwidth_gbps", None)
        rows.append((f"GPU {index} 带宽", f"{bandwidth:.0f} GB/s" if bandwidth else "未知"))
        vendor = getattr(gpu, "vendor", "") or ""
        detail = vendor
        compute = getattr(gpu, "compute_capability", None)
        if compute:
            detail += f"　算力 CC {compute[0]}.{compute[1]}"
        cuda = getattr(gpu, "cuda_version", None)
        if cuda:
            detail += f"　CUDA {cuda}"
        rocm = getattr(gpu, "rocm_version", None)
        if rocm:
            detail += f"　ROCm {rocm}"
        if getattr(gpu, "shared_memory", False):
            detail += "　共享内存"
        rows.append((f"GPU {index} 驱动", detail.strip() or "-"))
    return rows
