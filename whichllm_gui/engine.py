"""whichllm GUI 的数据层。

设计要点
--------
1. **进程内复用 whichllm 的排序管线**：``whichllm --json`` 每次都要重新检测硬件
   （本机约 7.5 秒），而检测结果在 GUI 生命周期内是稳定的。这里把
   「硬件 / 模型列表 / 基准数据」缓存起来，于是每次调整筛选条件只需重新排序
   （实测约 0.05 秒），界面才能真正做到即时响应。
2. **零逻辑重复**：JSON / Markdown 导出直接把 whichllm 自己的 rich 控制台
   重定向到内存缓冲区再取回，因此导出结果与 ``whichllm --json`` /
   ``whichllm --markdown`` 完全一致。
3. **优雅降级**：若某个 whichllm 版本移除了内部辅助函数（本程序基于 0.5.20 编写），
   自动回退到调用 ``python -m whichllm`` 子进程并解析其 JSON 输出。
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

GiB = 1024**3
MiB = 1024**2

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

Progress = Callable[[str], None]


class EngineError(RuntimeError):
    """面向用户的错误：消息可以直接显示在界面上。"""


def _noop(_message: str) -> None:
    """默认的进度回调。"""


# --------------------------------------------------------------------------- #
# 控制台捕获：借用 whichllm 自己的输出函数，保证导出内容与 CLI 完全一致
# --------------------------------------------------------------------------- #


def clean_console_text(text: str) -> str:
    """去掉 ANSI 颜色码与多余空白，便于在界面里显示。"""
    plain = ANSI_RE.sub("", text or "")
    lines = [line.rstrip() for line in plain.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(line for line in lines if line.strip())


@contextlib.contextmanager
def capture_console(width: int = 4000) -> Iterator[io.StringIO]:
    """把 whichllm 的 rich 控制台临时指向内存缓冲区。

    whichllm 内部有两个独立的 ``Console`` 实例：``output._console.console``
    负责 JSON / Markdown / 表格，``cli_shared.console`` 负责进度条与错误信息，
    两者都要接管，否则错误原因会丢失。
    """
    consoles = []
    with contextlib.suppress(Exception):
        from whichllm.output import _console

        consoles.append(_console.console)
    with contextlib.suppress(Exception):
        from whichllm.cli_shared import console as shared_console

        if all(shared_console is not item for item in consoles):
            consoles.append(shared_console)

    buffer = io.StringIO()
    previous = [(item.file, item.width, item.no_color) for item in consoles]
    for item in consoles:
        item.file = buffer
        item.width = width
        item.no_color = True
    try:
        yield buffer
    finally:
        for item, (old_file, old_width, old_no_color) in zip(consoles, previous):
            with contextlib.suppress(Exception):
                item.file = old_file
            with contextlib.suppress(Exception):
                item.width = old_width
            with contextlib.suppress(Exception):
                item.no_color = old_no_color


def _message_from_exception(exc: BaseException, buffer: io.StringIO) -> str:
    message = clean_console_text(buffer.getvalue())
    if message and message.strip() not in {"Exit:", "Exit", "Aborted!"}:
        return message
    if message:
        return "whichllm 拒绝了这次请求，请检查输入参数。"
    if isinstance(exc, SystemExit):
        return f"whichllm 中止了本次调用（退出码 {exc.code}）。"
    return f"{type(exc).__name__}: {exc}"


def guarded(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """调用 whichllm 内部函数，把 typer.Exit / SystemExit 转成 EngineError。

    whichllm 的校验函数在出错时会 ``console.print("[red]Error:[/] ...")`` 后
    抛 ``typer.Exit``，这里顺带把那条消息捕获下来当作异常文本。
    """
    with capture_console() as buffer:
        try:
            return func(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001 - 需要兜住 SystemExit
            raise EngineError(_message_from_exception(exc, buffer)) from exc


def captured_json(func: Callable[[], Any]) -> dict:
    """执行会打印 JSON 的 whichllm 函数，并把那段 JSON 解析成 dict。"""
    with capture_console() as buffer:
        try:
            func()
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001
            raise EngineError(_message_from_exception(exc, buffer)) from exc
    raw = ANSI_RE.sub("", buffer.getvalue())
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise EngineError("whichllm 没有返回可解析的 JSON 输出。")
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise EngineError(f"解析 whichllm JSON 输出失败：{exc}") from exc


# --------------------------------------------------------------------------- #
# 数值/文本解析
# --------------------------------------------------------------------------- #


def parse_optional_float(text: str, label: str) -> float | None:
    """把界面输入框里的文本转成 float；空串表示「未设置」。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise EngineError(f"{label} 需要是数字，当前为 {raw!r}。") from exc


def parse_optional_int(text: str, label: str) -> int | None:
    value = parse_optional_float(text, label)
    if value is None:
        return None
    if value != int(value):
        raise EngineError(f"{label} 需要是整数，当前为 {text!r}。")
    return int(value)


def parse_context_length(text: str) -> int:
    """上下文长度支持 ``4096`` / ``64k`` / ``128k`` 写法。"""
    raw = (text or "").strip().lower().replace("_", "")
    if not raw:
        return 4096
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([km])?", raw)
    if not match:
        raise EngineError(f"上下文长度格式不正确：{text!r}（示例：4096、64k、128k）。")
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "k":
        value *= 1024
    elif unit == "m":
        value *= 1024 * 1024
    if value < 1:
        raise EngineError("上下文长度必须大于 0。")
    return int(value)


# --------------------------------------------------------------------------- #
# 显示格式化
# --------------------------------------------------------------------------- #


def fmt_bytes(value: float | int | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    if size >= GiB:
        return f"{size / GiB:.1f} GB"
    if size >= MiB:
        return f"{size / MiB:.0f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size:.0f} B"


def fmt_gb(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{float(value) / GiB:.{digits}f} GB"


def fmt_params(count: int | None) -> str:
    if not count:
        return "-"
    if count >= 1e9:
        return f"{count / 1e9:.1f}B"
    return f"{count / 1e6:.0f}M"


def fmt_params_with_active(model: Any) -> str:
    text = fmt_params(getattr(model, "parameter_count", None))
    active = getattr(model, "parameter_count_active", None)
    if getattr(model, "is_moe", False) and active:
        text += f" ({fmt_params(active)}a)"
    return text


def fmt_downloads(value: int | None) -> str:
    if not value:
        return "-"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def fmt_published(value: str | None) -> str:
    if not value:
        return "-"
    return str(value)[:10]


def fmt_speed(result: Any) -> str:
    speed = getattr(result, "estimated_tok_per_sec", None)
    if not speed:
        return "-"
    marker = {"low": " ?", "medium": " ~"}.get(
        getattr(result, "speed_confidence", "") or "", ""
    )
    return f"{speed:.1f}{marker}"


def fmt_quality(result: Any) -> str:
    score = getattr(result, "quality_score", None)
    if score is None:
        return "-"
    status = getattr(result, "benchmark_status", "") or ""
    marker = {"none": " ?", "self_reported": " !sr", "estimated": " ~"}.get(status, "")
    return f"{score:.1f}{marker}"


FIT_LABELS = {
    "full_gpu": "全 GPU",
    "partial_offload": "部分卸载",
    "cpu_only": "纯 CPU",
    "too_small": "显存不足",
}

EVIDENCE_LABELS = {
    "direct": "直接测量",
    "self_reported": "模型方自报",
    "estimated": "推断估算",
    "none": "无数据",
}

SPEED_CONFIDENCE_LABELS = {"low": "低", "medium": "中", "high": "高"}


def fit_label(value: str | None) -> str:
    return FIT_LABELS.get(value or "", value or "-")


def evidence_label(value: str | None) -> str:
    return EVIDENCE_LABELS.get(value or "", value or "-")


# --------------------------------------------------------------------------- #
# GPU 名称建议（用于「模拟 GPU」输入框）
# --------------------------------------------------------------------------- #

CURATED_GPUS: tuple[str, ...] = (
    "RTX 3060 12GB",
    "RTX 3080",
    "RTX 3090",
    "RTX 4060",
    "RTX 4060 Ti 16GB",
    "RTX 4070",
    "RTX 4070 Ti SUPER",
    "RTX 4080",
    "RTX 4090",
    "RTX 5070",
    "RTX 5070 Ti",
    "RTX 5080",
    "RTX 5090",
    "2x RTX 4090",
    "2x RTX 5090",
    "RX 6800 XT",
    "RX 7800 XT",
    "RX 7900 XTX",
    "RX 9070 XT",
    "Arc A770",
    "Arc B580",
    "M1 Max",
    "M2 Ultra",
    "M3 Max",
    "M4 Max",
    "A100 40GB",
    "A100 80GB",
    "H100",
    "H200",
    "L40S",
)

_MODERN_GPU_RE = re.compile(
    r"(RTX|RX \d|Arc |Apple M\d|A100|H100|H200|L40|MI\d|V100|Tesla|Quadro|Titan|"
    r"GTX 16|Radeon Pro|Arc Pro)",
    re.IGNORECASE,
)

_gpu_suggestions_cache: list[str] | None = None


def gpu_name_suggestions() -> list[str]:
    """常用 GPU 快捷项 + dbgpu 数据库里较新的型号（共 2800+ 条，已过滤）。"""
    global _gpu_suggestions_cache
    if _gpu_suggestions_cache is not None:
        return _gpu_suggestions_cache

    names: list[str] = list(CURATED_GPUS)
    try:
        from dbgpu import GPUDatabase

        extras = [n for n in GPUDatabase.default().names if _MODERN_GPU_RE.search(n)]
        extras.sort()
        names.extend(extras)
    except Exception:  # noqa: BLE001 - 没有 dbgpu 时仅用快捷列表
        pass

    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    _gpu_suggestions_cache = unique
    return unique


# --------------------------------------------------------------------------- #
# 筛选条件
# --------------------------------------------------------------------------- #


@dataclass
class RecommendFilters:
    """推荐页的全部筛选条件（数值项用字符串保存，便于直接绑定输入框）。"""

    top: int = 10
    context_length: str = "4096"
    quant: str = ""
    speed: str = "any"
    min_speed: str = ""
    fit: str = "any"
    evidence: str = "any"
    min_params: str = ""
    profile: str = "general"
    cpu_only: bool = False
    simulate_gpus: tuple[str, ...] = ()
    vram: str = ""
    bandwidth: str = ""
    gpu_index: str = ""
    vram_headroom: str = "auto"
    ram_budget: str = ""
    lm_studio_paths: tuple[str, ...] = ()
    backfill_published: bool = False

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        data["simulate_gpus"] = list(self.simulate_gpus)
        data["lm_studio_paths"] = list(self.lm_studio_paths)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "RecommendFilters":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in known}
        for key in ("simulate_gpus", "lm_studio_paths"):
            if key in clean:
                clean[key] = tuple(clean[key] or ())
        return cls(**clean)


@dataclass
class EngineResult:
    """一次推荐请求的完整结果。"""

    hardware: Any
    results: list[Any] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    relaxed: bool = False
    empty_message: str | None = None
    used_subprocess: bool = False


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #


class Engine:
    """封装 whichllm：缓存昂贵的检测结果，并按需重新排序。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._booted = False
        self._api: dict[str, Any] = {}
        self.inprocess = True
        self.boot_error: str | None = None

        self._hardware: Any = None
        self._models: list[Any] | None = None
        self._benchmarks: dict[str, float] | None = None
        self._flat: list[Any] | None = None
        self._flat_source_id: int | None = None

        self.timings: dict[str, float] = {}
        self.sources: dict[str, str] = {}
        self.version = ""

    # -- 初始化 ------------------------------------------------------------ #

    def bootstrap(self) -> None:
        """导入 whichllm 内部接口（失败则降级为子进程模式）。"""
        with self._lock:
            if self._booted:
                return
            try:
                from whichllm.cli_hardware import (
                    _apply_gpu_overrides,
                    _apply_memory_budgets,
                    _auto_min_params_for_profile,
                )
                from whichllm.cli_metadata import (
                    _fill_missing_published_at,
                    _include_vision_candidates,
                )
                from whichllm.cli_models import (
                    _looks_like_hf_repo_id,
                    _pick_gguf_variant,
                    _resolve_model_deps,
                    _search_model,
                )
                from whichllm.cli_options import (
                    _resolve_evidence_mode,
                    _resolve_fit_filter,
                    _resolve_speed_filter,
                    _validate_gpu_flags,
                    _validate_profile,
                    _validate_ranking_flags,
                )
                from whichllm.cli_shared import _run_async
                from whichllm.engine.ranker import rank_models
                from whichllm.hardware.detector import detect_hardware
                from whichllm.models.artifacts import attach_resolved_artifacts
                from whichllm.models.benchmark import (
                    fetch_benchmark_scores,
                    load_benchmark_cache,
                    save_benchmark_cache,
                )
                from whichllm.models.cache import load_cache, save_cache
                from whichllm.models.fetcher import (
                    dicts_to_models,
                    fetch_model_published_at,
                    fetch_models,
                    models_to_dicts,
                )
                from whichllm.models.grouper import group_models
                from whichllm.models.hf import fetch_model_by_id
                from whichllm.models.lmstudio import (
                    attach_local_matches,
                    discover_lmstudio_ggufs,
                )
                from whichllm.output.json_output import (
                    display_json,
                    display_plan_json,
                    display_upgrade_json,
                )

                self._api = {
                    "_apply_gpu_overrides": _apply_gpu_overrides,
                    "_apply_memory_budgets": _apply_memory_budgets,
                    "_auto_min_params_for_profile": _auto_min_params_for_profile,
                    "_fill_missing_published_at": _fill_missing_published_at,
                    "_include_vision_candidates": _include_vision_candidates,
                    "_looks_like_hf_repo_id": _looks_like_hf_repo_id,
                    "_pick_gguf_variant": _pick_gguf_variant,
                    "_resolve_model_deps": _resolve_model_deps,
                    "_search_model": _search_model,
                    "_resolve_evidence_mode": _resolve_evidence_mode,
                    "_resolve_fit_filter": _resolve_fit_filter,
                    "_resolve_speed_filter": _resolve_speed_filter,
                    "_validate_gpu_flags": _validate_gpu_flags,
                    "_validate_profile": _validate_profile,
                    "_validate_ranking_flags": _validate_ranking_flags,
                    "_run_async": _run_async,
                    "rank_models": rank_models,
                    "detect_hardware": detect_hardware,
                    "attach_resolved_artifacts": attach_resolved_artifacts,
                    "fetch_benchmark_scores": fetch_benchmark_scores,
                    "load_benchmark_cache": load_benchmark_cache,
                    "save_benchmark_cache": save_benchmark_cache,
                    "load_cache": load_cache,
                    "save_cache": save_cache,
                    "dicts_to_models": dicts_to_models,
                    "fetch_model_published_at": fetch_model_published_at,
                    "fetch_models": fetch_models,
                    "models_to_dicts": models_to_dicts,
                    "group_models": group_models,
                    "fetch_model_by_id": fetch_model_by_id,
                    "attach_local_matches": attach_local_matches,
                    "discover_lmstudio_ggufs": discover_lmstudio_ggufs,
                    "display_json": display_json,
                    "display_plan_json": display_plan_json,
                    "display_upgrade_json": display_upgrade_json,
                }
                self.inprocess = True
            except BaseException as exc:  # noqa: BLE001
                self.inprocess = False
                self.boot_error = f"{type(exc).__name__}: {exc}"

            try:
                from importlib.metadata import version

                self.version = version("whichllm")
            except Exception:  # noqa: BLE001
                self.version = "未知"

            self._booted = True

    # -- 子进程通道 -------------------------------------------------------- #

    def _cli_command(self) -> list[str]:
        return [sys.executable, "-m", "whichllm"]

    def run_cli(
        self,
        args: Sequence[str],
        *,
        timeout: float = 1800,
        progress: Progress | None = None,
        columns: int = 2000,
    ) -> str:
        """运行 ``python -m whichllm ...`` 并返回标准输出文本。"""
        command = self._cli_command() + list(args)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["COLUMNS"] = str(columns)
        env.setdefault("TERM", "dumb")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                env=env,
                cwd=str(Path.home()),
            )
        except FileNotFoundError as exc:
            raise EngineError(f"找不到 Python 解释器：{exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise EngineError("whichllm 运行超时。") from exc

        stdout = completed.stdout.decode("utf-8", "replace")
        stderr = completed.stderr.decode("utf-8", "replace")
        if completed.returncode != 0:
            message = clean_console_text(stderr) or clean_console_text(stdout)
            raise EngineError(message or f"whichllm 退出码 {completed.returncode}。")
        return stdout

    def cli_json(
        self, args: Sequence[str], *, timeout: float = 1800, progress: Progress | None = None
    ) -> dict:
        """调用 CLI 的 ``--json`` 输出并解析。"""
        raw = ANSI_RE.sub("", self.run_cli(list(args) + ["--json"], timeout=timeout))
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise EngineError("whichllm 没有返回可解析的 JSON 输出。")
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise EngineError(f"解析 whichllm JSON 输出失败：{exc}") from exc

    def _require_inprocess(self, what: str) -> dict[str, Any]:
        self.bootstrap()
        if not self.inprocess:
            raise EngineError(
                f"无法使用进程内接口（{what}），原因：{self.boot_error}"
            )
        return self._api

    # -- 硬件 -------------------------------------------------------------- #

    def hardware(self, *, refresh: bool = False, progress: Progress | None = None) -> Any:
        api = self._require_inprocess("硬件检测")
        report = progress or _noop
        with self._lock:
            if self._hardware is None or refresh:
                report("正在检测硬件（首次约需数秒）…")
                started = time.perf_counter()
                self._hardware = guarded(api["detect_hardware"])
                self.timings["硬件检测"] = time.perf_counter() - started
            return self._hardware

    def hardware_text(self) -> str:
        """降级模式下用 CLI 的文本面板展示硬件信息。"""
        raw = self.run_cli(["hardware"], columns=96)
        return clean_console_text(raw)

    def apply_overrides(self, filters: RecommendFilters, progress: Progress | None = None) -> Any:
        """在检测结果副本上应用「模拟 GPU / 显存上限 / 内存预算」等覆盖。"""
        api = self._require_inprocess("硬件覆盖")
        raw = self.hardware(progress=progress)
        hardware = copy.deepcopy(raw)

        gpus = [g.strip() for g in filters.simulate_gpus if g and g.strip()]
        vram = parse_optional_float(filters.vram, "显存上限 (GB)")
        bandwidth = parse_optional_float(filters.bandwidth, "带宽 (GB/s)")
        gpu_index = parse_optional_int(filters.gpu_index, "GPU 序号")
        if vram is not None and vram <= 0:
            raise EngineError("显存上限必须大于 0。")
        if bandwidth is not None and bandwidth <= 0:
            raise EngineError("带宽必须大于 0。")
        if gpu_index is not None and gpu_index < 0:
            raise EngineError("GPU 序号必须 ≥ 0。")
        if filters.cpu_only and gpus:
            raise EngineError("「仅 CPU 模式」与「模拟 GPU」不能同时使用。")
        if gpu_index is not None and not gpus and filters.cpu_only:
            raise EngineError("「仅 CPU 模式」不能与 GPU 覆盖参数同时使用。")
        if gpu_index is not None and not gpus and gpu_index > 0 and vram is None and bandwidth is None:
            raise EngineError("GPU 序号需要配合显存上限或带宽一起使用。")

        guarded(
            api["_validate_gpu_flags"],
            filters.cpu_only,
            gpus or None,
            vram,
            bandwidth,
            gpu_index,
        )
        guarded(
            api["_apply_gpu_overrides"],
            hardware,
            filters.cpu_only,
            gpus or None,
            vram,
            bandwidth,
            gpu_index,
        )
        guarded(
            api["_apply_memory_budgets"],
            hardware,
            vram_headroom=(filters.vram_headroom or "auto").strip(),
            ram_budget=(filters.ram_budget or "").strip() or None,
        )
        return hardware

    # -- 模型与基准 -------------------------------------------------------- #

    def models(
        self,
        *,
        refresh: bool = False,
        profile: str = "general",
        progress: Progress | None = None,
    ) -> list[Any]:
        api = self._require_inprocess("模型列表")
        report = progress or _noop
        with self._lock:
            if self._models is not None and not refresh:
                return self._models

            cached = None if refresh else guarded(api["load_cache"])
            if cached is not None:
                self._models = api["dicts_to_models"](cached)
                self.sources["模型数据"] = f"本地缓存（{len(self._models)} 个）"
                return self._models

            report("正在从 HuggingFace 抓取模型列表…")
            started = time.perf_counter()
            include_vision = api["_include_vision_candidates"](profile)
            try:
                models = guarded(
                    api["_run_async"], api["fetch_models"](include_vision=include_vision)
                )
            except EngineError as exc:
                raise EngineError(f"抓取模型列表失败：{exc}") from exc
            self._models = models
            self.timings["模型抓取"] = time.perf_counter() - started
            self.sources["模型数据"] = f"在线抓取（{len(models)} 个）"
            with contextlib.suppress(Exception):
                guarded(api["save_cache"], api["models_to_dicts"](models))
            return self._models

    def benchmarks(
        self, *, refresh: bool = False, progress: Progress | None = None
    ) -> dict[str, float]:
        api = self._require_inprocess("基准数据")
        report = progress or _noop
        with self._lock:
            if self._benchmarks is not None and not refresh:
                return self._benchmarks

            cached = None if refresh else guarded(api["load_benchmark_cache"])
            if cached is None:
                report("正在获取基准测试数据…")
                started = time.perf_counter()
                try:
                    cached = guarded(api["_run_async"], api["fetch_benchmark_scores"]())
                except EngineError:
                    cached = {}
                else:
                    with contextlib.suppress(Exception):
                        guarded(api["save_benchmark_cache"], cached)
                self.timings["基准数据"] = time.perf_counter() - started
                self.sources["基准数据"] = f"在线抓取（{len(cached or {})} 条）"
            else:
                self.sources["基准数据"] = f"本地缓存（{len(cached)} 条）"
            self._benchmarks = cached or {}
            return self._benchmarks

    def flat_models(self, progress: Progress | None = None) -> list[Any]:
        """把「家族」展开成待排序的模型列表（等价于 CLI 的处理）。"""
        api = self._require_inprocess("模型分组")
        models = self.models(progress=progress)
        with self._lock:
            if self._flat is None or self._flat_source_id != id(models):
                flat: list[Any] = []
                for family in api["group_models"](models):
                    flat.append(family.base_model)
                    flat.extend(family.variants)
                self._flat = flat
                self._flat_source_id = id(models)
            return self._flat

    def invalidate_models(self) -> None:
        """模型列表变化后清空派生缓存。"""
        with self._lock:
            self._flat = None
            self._flat_source_id = None

    # -- 推荐 -------------------------------------------------------------- #

    def recommend(
        self,
        filters: RecommendFilters,
        *,
        refresh: bool = False,
        progress: Progress | None = None,
    ) -> EngineResult:
        """执行一次推荐；等价于 ``whichllm``（主命令）。"""
        report = progress or _noop
        self.bootstrap()

        if not self.inprocess:
            return self._recommend_subprocess(filters, refresh=refresh, progress=report)

        api = self._api
        if filters.top < 1:
            raise EngineError("显示数量必须 ≥ 1。")
        min_speed = parse_optional_float(filters.min_speed, "最低速度 (tok/s)")
        min_params = parse_optional_float(filters.min_params, "最小参数量 (B)")
        if min_speed is not None and min_speed < 0:
            raise EngineError("最低速度不能为负数。")
        if min_params is not None and min_params < 0:
            raise EngineError("最小参数量不能为负数。")

        context_length = parse_context_length(filters.context_length)
        profile = guarded(api["_validate_profile"], filters.profile)
        evidence_mode = guarded(api["_resolve_evidence_mode"], filters.evidence, False)
        fit_filter = guarded(api["_resolve_fit_filter"], filters.fit, False)
        speed_filter = guarded(api["_resolve_speed_filter"], filters.speed, min_speed)

        timings: dict[str, float] = {}
        started = time.perf_counter()
        report("正在应用硬件设置…")
        hardware = self.apply_overrides(filters, progress=report)
        timings["硬件设置"] = time.perf_counter() - started

        started = time.perf_counter()
        models = self.models(refresh=refresh, profile=profile, progress=report)
        if refresh:
            self.invalidate_models()
        all_models = self.flat_models(progress=report)
        timings["模型分组"] = time.perf_counter() - started

        started = time.perf_counter()
        benchmarks = self.benchmarks(refresh=refresh, progress=report)
        timings["基准数据"] = time.perf_counter() - started

        quant_filter = (filters.quant or "").strip() or None
        auto_min_params = (
            guarded(api["_auto_min_params_for_profile"], hardware, profile)
            if min_params is None
            else min_params
        )

        report(f"正在为 {len(all_models)} 个模型排序…")
        started = time.perf_counter()
        results = guarded(
            api["rank_models"],
            all_models,
            hardware,
            context_length=context_length,
            top_n=filters.top,
            quant_filter=quant_filter,
            min_speed=speed_filter,
            benchmark_scores=benchmarks,
            task_profile=profile,
            require_direct_top=True,
            min_params_b=auto_min_params,
            evidence_filter=evidence_mode,
            fit_filter=fit_filter,
        )
        relaxed = False
        if not results and auto_min_params is not None and min_params is None:
            # 与 CLI 一致：自动阈值导致空结果时放宽一次
            results = guarded(
                api["rank_models"],
                all_models,
                hardware,
                context_length=context_length,
                top_n=filters.top,
                quant_filter=quant_filter,
                min_speed=speed_filter,
                benchmark_scores=benchmarks,
                task_profile=profile,
                require_direct_top=True,
                min_params_b=None,
                evidence_filter=evidence_mode,
                fit_filter=fit_filter,
            )
            relaxed = True
        timings["排序"] = time.perf_counter() - started

        if results:
            started = time.perf_counter()
            with contextlib.suppress(Exception):
                guarded(
                    api["attach_resolved_artifacts"],
                    results,
                    all_models,
                    quant_filter=quant_filter,
                )
            paths = tuple(p for p in filters.lm_studio_paths if p)
            try:
                local_models = guarded(api["discover_lmstudio_ggufs"], paths)
            except EngineError as exc:
                raise EngineError(f"LM Studio 模型目录无效：{exc}") from exc
            guarded(api["attach_local_matches"], results, local_models)
            timings["本地模型匹配"] = time.perf_counter() - started

            if filters.backfill_published:
                started = time.perf_counter()
                with contextlib.suppress(Exception):
                    guarded(
                        api["_fill_missing_published_at"],
                        all_models,
                        results,
                        api["fetch_model_published_at"],
                    )
                timings["补全发布日期"] = time.perf_counter() - started

        empty_message = None
        if fit_filter == "full_gpu":
            empty_message = (
                "当前硬件没有能完全放进显存（全 GPU）的模型。"
                "请把「运行适配」改为「不限」以包含部分卸载与 CPU 方案。"
            )

        notes = list(getattr(hardware, "budget_notes", []) or [])
        if relaxed:
            notes.append("自动参数量阈值导致无结果，已自动放宽重新排序。")

        return EngineResult(
            hardware=hardware,
            results=results,
            timings=timings,
            notes=notes,
            sources=dict(self.sources),
            relaxed=relaxed,
            empty_message=empty_message,
        )

    def _recommend_subprocess(
        self, filters: RecommendFilters, *, refresh: bool, progress: Progress
    ) -> EngineResult:
        """降级模式：直接调用 ``whichllm --json``。"""
        args: list[str] = ["-n", str(filters.top), "-c", str(parse_context_length(filters.context_length))]
        if filters.quant.strip():
            args += ["-q", filters.quant.strip()]
        if filters.min_speed.strip():
            args += ["--min-speed", filters.min_speed.strip()]
        elif filters.speed != "any":
            args += ["--speed", filters.speed]
        if filters.fit != "any":
            args += ["--fit", filters.fit]
        if filters.evidence != "any":
            args += ["--evidence", filters.evidence]
        if filters.min_params.strip():
            args += ["--min-params", filters.min_params.strip()]
        if filters.profile != "general":
            args += ["--profile", filters.profile]
        if filters.cpu_only:
            args += ["--cpu-only"]
        for gpu in filters.simulate_gpus:
            if gpu.strip():
                args += ["--gpu", gpu.strip()]
        if filters.vram.strip():
            args += ["--vram", filters.vram.strip()]
        if filters.bandwidth.strip():
            args += ["--bandwidth", filters.bandwidth.strip()]
        if filters.gpu_index.strip():
            args += ["--gpu-index", filters.gpu_index.strip()]
        if filters.vram_headroom.strip() and filters.vram_headroom.strip() != "auto":
            args += ["--vram-headroom", filters.vram_headroom.strip()]
        if filters.ram_budget.strip():
            args += ["--ram-budget", filters.ram_budget.strip()]
        for path in filters.lm_studio_paths:
            if path:
                args += ["--lm-studio-path", path]
        if refresh:
            args.append("--refresh")

        progress("正在调用 whichllm 命令行（降级模式）…")
        data = self.cli_json(args)
        return self._result_from_json(data, used_subprocess=True)

    def _result_from_json(self, data: dict, *, used_subprocess: bool) -> EngineResult:
        """把 CLI 的 JSON 结果适配成 EngineResult（降级模式用）。"""
        hardware = _HardwareProxy(data.get("hardware") or {})
        results = [_ModelProxy(item) for item in data.get("models") or []]
        empty_message = None
        if not results:
            empty_message = "没有找到可用模型，请放宽筛选条件。"
        return EngineResult(
            hardware=hardware,
            results=results,
            timings={},
            notes=list(hardware.budget_notes),
            sources={"模型数据": "命令行 --json"},
            empty_message=empty_message,
            used_subprocess=used_subprocess,
        )

    # -- 规划 / 升级 / 脚本 ------------------------------------------------ #

    def plan(
        self,
        model_name: str,
        *,
        context_length: str = "4096",
        quant: str = "",
        refresh: bool = False,
        progress: Progress | None = None,
    ) -> dict:
        """等价于 ``whichllm plan <模型> --json``。"""
        report = progress or _noop
        query = (model_name or "").strip()
        if not query:
            raise EngineError("请输入模型名称或 HuggingFace 仓库 ID。")
        ctx = parse_context_length(context_length)
        target_quant = (quant or "").strip().upper() or "Q4_K_M"

        self.bootstrap()
        if not self.inprocess:
            args = ["plan", query, "-c", str(ctx), "-q", target_quant]
            if refresh:
                args.append("--refresh")
            report("正在调用 whichllm plan（降级模式）…")
            return self.cli_json(args)

        api = self._api
        report("正在查找模型…")
        models = self.models(refresh=refresh, profile="vision", progress=report)
        model = next((m for m in models if m.id.lower() == query.lower()), None)
        if model is None and api["_looks_like_hf_repo_id"](query):
            report("正在从 HuggingFace 获取仓库信息…")
            try:
                model = guarded(api["_run_async"], api["fetch_model_by_id"](query))
            except EngineError as exc:
                raise EngineError(f"获取仓库 {query} 失败：{exc}") from exc
            if model is None:
                raise EngineError(
                    f"HuggingFace 仓库 {query!r} 没有暴露足够的模型元数据，无法估算显存。"
                )
        if model is None:
            model = guarded(api["_search_model"], models, query)

        report("正在估算各量化档位的显存占用…")
        return captured_json(
            lambda: api["display_plan_json"](model, ctx, target_quant)
        )

    def upgrade(
        self,
        target_gpus: Sequence[str],
        *,
        context_length: str = "8192",
        top: int = 3,
        profile: str = "general",
        cpu_only: bool = False,
        refresh: bool = False,
        progress: Progress | None = None,
    ) -> dict:
        """等价于 ``whichllm upgrade <GPU...> --json``。"""
        report = progress or _noop
        targets = [g.strip() for g in target_gpus if g and g.strip()]
        if not targets:
            raise EngineError("请至少填写一个用于对比的 GPU。")
        if top < 1:
            raise EngineError("每个 GPU 对比的模型数量必须 ≥ 1。")
        ctx = parse_context_length(context_length)

        self.bootstrap()
        if not self.inprocess:
            args = ["upgrade", *targets, "-c", str(ctx), "-n", str(top)]
            if profile != "general":
                args += ["--profile", profile]
            if cpu_only:
                args.append("--cpu-only")
            if refresh:
                args.append("--refresh")
            report("正在调用 whichllm upgrade（降级模式）…")
            return self.cli_json(args, timeout=3600)

        api = self._api
        from whichllm.hardware.gpu_simulator import create_synthetic_gpu
        from whichllm.hardware.types import HardwareInfo

        profile = guarded(api["_validate_profile"], profile)
        guarded(api["_validate_ranking_flags"], top, None, None)

        current = copy.deepcopy(self.hardware(progress=report))
        if cpu_only:
            current.gpus = []
        all_models = self.flat_models(progress=report)
        benchmarks = self.benchmarks(refresh=refresh, progress=report)

        def rank_for(hardware: Any) -> list[Any]:
            min_params = guarded(api["_auto_min_params_for_profile"], hardware, profile)
            results = guarded(
                api["rank_models"],
                all_models,
                hardware,
                context_length=ctx,
                top_n=top,
                benchmark_scores=benchmarks,
                task_profile=profile,
                require_direct_top=True,
                min_params_b=min_params,
            )
            if not results and min_params is not None:
                results = guarded(
                    api["rank_models"],
                    all_models,
                    hardware,
                    context_length=ctx,
                    top_n=top,
                    benchmark_scores=benchmarks,
                    task_profile=profile,
                    require_direct_top=True,
                    min_params_b=None,
                )
            return results

        report("正在评估当前硬件…")
        current_results = rank_for(current)

        target_results: list[tuple[str, Any, list[Any]]] = []
        for raw_name in targets:
            report(f"正在模拟 {raw_name}…")
            try:
                synthetic = create_synthetic_gpu(raw_name)
            except ValueError as exc:
                raise EngineError(f"无法识别的 GPU「{raw_name}」：{exc}") from exc
            simulated = HardwareInfo(
                gpus=[synthetic],
                cpu_name=current.cpu_name,
                cpu_cores=current.cpu_cores,
                has_avx2=current.has_avx2,
                has_avx512=current.has_avx512,
                ram_bytes=current.ram_bytes,
                disk_free_bytes=current.disk_free_bytes,
                os=current.os,
            )
            target_results.append((raw_name, simulated, rank_for(simulated)))

        report("正在生成对比结果…")
        return captured_json(
            lambda: api["display_upgrade_json"](current, current_results, target_results)
        )

    def snippet(
        self,
        model_name: str,
        *,
        quant: str = "",
        refresh: bool = False,
        progress: Progress | None = None,
    ) -> dict:
        """生成可直接运行的示例脚本（等价于 ``whichllm snippet``）。"""
        report = progress or _noop
        query = (model_name or "").strip()

        self.bootstrap()
        if not self.inprocess:
            args = ["snippet"]
            if query:
                args.append(query)
            if quant.strip():
                args += ["-q", quant.strip()]
            if refresh:
                args.append("--refresh")
            report("正在调用 whichllm snippet（降级模式）…")
            text = clean_console_text(self.run_cli(args))
            return {"model_id": query or "(自动选择)", "code": text, "deps": [], "variant": None}

        api = self._api
        report("正在加载模型列表…")
        models = self.models(refresh=refresh, profile="vision", progress=report)
        if query:
            model = guarded(api["_search_model"], models, query)
        else:
            gguf_models = [m for m in models if m.gguf_variants]
            if not gguf_models:
                raise EngineError("模型库里没有带 GGUF 权重的模型。")
            gguf_models.sort(key=lambda m: m.downloads, reverse=True)
            model = gguf_models[0]

        variant = guarded(api["_pick_gguf_variant"], model, quant.strip() or None)
        deps, _ = guarded(api["_resolve_model_deps"], model, variant)
        code = _snippet_code(model.id, variant)
        return {
            "model_id": model.id,
            "code": code,
            "deps": list(deps),
            "variant": getattr(variant, "filename", None),
        }

    # -- 运行（在独立控制台窗口里聊天） ------------------------------------ #

    def run_command_preview(
        self, model_name: str, *, quant: str = "", context_length: str = "4096", cpu_only: bool = False
    ) -> list[str]:
        args = [sys.executable, "-m", "whichllm", "run"]
        if model_name.strip():
            args.append(model_name.strip())
        args += ["-c", str(parse_context_length(context_length))]
        if quant.strip():
            args += ["-q", quant.strip()]
        if cpu_only:
            args.append("--cpu-only")
        return args

    def launch_run(
        self, model_name: str, *, quant: str = "", context_length: str = "4096", cpu_only: bool = False
    ) -> str:
        """在独立控制台窗口里启动 ``whichllm run``（交互式聊天需要终端）。"""
        args = self.run_command_preview(
            model_name, quant=quant, context_length=context_length, cpu_only=cpu_only
        )
        if os.name == "nt":
            inner = subprocess.list2cmdline(args)
            subprocess.Popen(
                f'start "whichllm run" cmd /k "{inner}"',
                shell=True,
                cwd=str(Path.home()),
            )
        else:
            subprocess.Popen(args, cwd=str(Path.home()))
        return subprocess.list2cmdline(args)

    # -- 缓存信息 ---------------------------------------------------------- #

    def cache_info(self) -> dict:
        try:
            from whichllm.utils import _cache_dir

            directory = Path(_cache_dir())
        except Exception:  # noqa: BLE001
            return {"dir": "未知", "exists": False, "files": []}
        files = []
        for name in ("models.json", "benchmark.json"):
            path = directory / name
            if path.is_file():
                stat = path.stat()
                files.append(
                    {"name": name, "size": stat.st_size, "mtime": stat.st_mtime}
                )
        return {"dir": str(directory), "exists": directory.is_dir(), "files": files}

    def open_cache_dir(self) -> None:
        info = self.cache_info()
        directory = info.get("dir") or ""
        if not directory or not Path(directory).is_dir():
            raise EngineError("缓存目录不存在。")
        if os.name == "nt":
            os.startfile(directory)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", directory])
        else:
            subprocess.Popen(["xdg-open", directory])

    def model_id_suggestions(self, limit: int = 800) -> list[str]:
        """给「规划 / 脚本 / 运行」页的模型输入框做候选。"""
        self.bootstrap()
        ids: list[str] = []
        with contextlib.suppress(Exception):
            api = self._api
            if self.inprocess:
                models = self.models()
                ordered = sorted(models, key=lambda m: m.downloads, reverse=True)
                ids = [m.id for m in ordered[:limit]]
        return ids


# --------------------------------------------------------------------------- #
# 降级模式下的轻量代理对象：让视图代码无需区分数据来自进程内还是子进程
# --------------------------------------------------------------------------- #


class _GPUProxy:
    def __init__(self, data: dict) -> None:
        self.name = data.get("name") or "未知 GPU"
        self.vendor = data.get("vendor") or ""
        self.vram_bytes = int(data.get("vram_bytes") or 0)
        self.usable_vram_bytes = data.get("usable_vram_bytes")
        self.memory_bandwidth_gbps = data.get("memory_bandwidth_gbps")
        self.shared_memory = bool(data.get("shared_memory"))
        self.compute_capability = None
        self.cuda_version = None
        self.rocm_version = None
        self.vram_overridden = False


class _HardwareProxy:
    def __init__(self, data: dict) -> None:
        self.gpus = [_GPUProxy(item) for item in data.get("gpus") or []]
        self.cpu_name = data.get("cpu") or "未知"
        self.cpu_cores = int(data.get("cpu_cores") or 0)
        self.ram_bytes = int(data.get("ram_bytes") or 0)
        self.ram_budget_bytes = data.get("ram_budget_bytes")
        self.disk_free_bytes = 0
        self.os = data.get("os") or ""
        self.has_avx2 = False
        self.has_avx512 = False
        self.budget_notes = list(data.get("budget_notes") or [])


class _ModelRef:
    def __init__(self, data: dict) -> None:
        self.id = data.get("model_id") or "未知模型"
        self.parameter_count = int(data.get("parameter_count") or 0)
        self.parameter_count_active = None
        self.is_moe = False
        self.license = data.get("license")
        self.downloads = int(data.get("downloads") or 0)
        self.published_at = data.get("published_at")
        self.architecture = ""
        self.context_length = None
        self.gguf_variants: list[Any] = []
        self.benchmark_scores: dict[str, float] = {}
        self.base_model = None
        self.tags: tuple[str, ...] = ()


class _VariantRef:
    def __init__(self, data: dict) -> None:
        self.filename = data.get("artifact_filename") or ""
        self.quant_type = data.get("quant_type") or ""
        self.file_size_bytes = int(data.get("file_size_bytes") or 0)
        self.is_estimated = True


class _ModelProxy:
    """把 CLI JSON 里的一条模型记录包装成 CompatibilityResult 形状。"""

    def __init__(self, data: dict) -> None:
        self.model = _ModelRef(data)
        filename = data.get("artifact_filename")
        self.gguf_variant = _VariantRef(data) if filename else None
        self.artifact_variant = self.gguf_variant
        self.artifact_model = (
            _ModelRef({"model_id": data.get("artifact_repo_id")})
            if data.get("artifact_repo_id")
            else None
        )
        self.local_path = data.get("local_path")
        self.vram_required_bytes = int(data.get("vram_required_bytes") or 0)
        self.vram_available_bytes = int(data.get("vram_available_bytes") or 0)
        self.uses_multi_gpu = bool(data.get("uses_multi_gpu"))
        self.multi_gpu_effective_vram_bytes = data.get("multi_gpu_effective_vram_bytes")
        self.estimated_tok_per_sec = data.get("estimated_tok_per_sec")
        self.speed_confidence = data.get("speed_confidence")
        self.speed_range_tok_per_sec = data.get("speed_range_tok_per_sec")
        self.speed_notes = list(data.get("speed_notes") or [])
        self.quality_score = data.get("quality_score")
        self.benchmark_status = data.get("benchmark_status") or "none"
        self.benchmark_source = data.get("benchmark_source")
        self.benchmark_confidence = data.get("benchmark_confidence") or 0.0
        self.fit_type = data.get("fit_type") or ""
        self.can_run = bool(data.get("can_run"))
        self.warnings = list(data.get("warnings") or [])
        self.offload_ratio = None
        self.context_fits = None


def _snippet_code(model_id: str, variant: Any) -> str:
    """与 whichllm 0.5.20 的 ``whichllm snippet`` 输出保持一致的示例脚本。"""
    if variant is not None:
        return f"""\
from llama_cpp import Llama

llm = Llama.from_pretrained(
    repo_id={model_id!r},
    filename={variant.filename!r},
    n_ctx=4096,
    n_gpu_layers=-1,  # -1 = 所有层放到 GPU，0 = 仅 CPU
    verbose=False,
)

output = llm.create_chat_completion(
    messages=[{{"role": "user", "content": "Hello!"}}],
)
print(output["choices"][0]["message"]["content"])
"""
    return f"""\
from transformers import AutoModelForCausalLM, AutoTokenizer

model_id = {model_id!r}
tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id, device_map="auto", torch_dtype="auto", trust_remote_code=True,
)

inputs = tokenizer("Hello!", return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=256)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
"""
