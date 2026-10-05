"""导出：JSON / Markdown / CSV。

JSON 与 Markdown 直接调用 whichllm 自己的输出函数并捕获结果，因此导出内容与
``whichllm --json`` / ``whichllm --markdown`` 逐字节一致；CSV 是本程序额外提供的
表格格式（带 BOM，Excel 直接双击即可正确显示中文）。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Sequence

from .engine import (
    ANSI_RE,
    SPEED_CONFIDENCE_LABELS,
    Engine,
    EngineError,
    capture_console,
    evidence_label,
    fit_label,
    fmt_params,
)


def _plain(text: str) -> str:
    """去掉 ANSI 码、行尾空白，但保留空行（Markdown 需要空行）。"""
    lines = [line.rstrip() for line in ANSI_RE.sub("", text or "").splitlines()]
    return "\n".join(lines).strip("\n")


def results_json(engine: Engine, hardware: Any, results: Sequence[Any]) -> dict:
    """把推荐结果序列化成与 ``whichllm --json`` 相同的结构。"""
    display_json = engine._api.get("display_json")  # type: ignore[attr-defined]
    if display_json is None or not results:
        return _manual_json(hardware, results)
    with capture_console() as buffer:
        try:
            display_json(list(results), hardware)
        except BaseException as exc:  # noqa: BLE001
            raise EngineError(f"导出 JSON 失败：{exc}") from exc
    raw = ANSI_RE.sub("", buffer.getvalue())
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise EngineError("导出 JSON 失败：输出无法解析。")
    return json.loads(raw[start : end + 1])


def _manual_json(hardware: Any, results: Sequence[Any]) -> dict:
    """无法调用 whichllm 渲染器时的兜底实现（字段与 CLI 一致）。"""

    def gpu_payload(gpu: Any) -> dict:
        return {
            "name": getattr(gpu, "name", ""),
            "vendor": getattr(gpu, "vendor", ""),
            "vram_bytes": getattr(gpu, "vram_bytes", 0),
            "usable_vram_bytes": getattr(gpu, "usable_vram_bytes", None),
            "memory_bandwidth_gbps": getattr(gpu, "memory_bandwidth_gbps", None),
            "shared_memory": getattr(gpu, "shared_memory", False),
        }

    models = []
    for index, result in enumerate(results, 1):
        model = result.model
        variant = getattr(result, "gguf_variant", None)
        artifact = getattr(result, "artifact_model", None)
        models.append(
            {
                "rank": index,
                "model_id": model.id,
                "artifact_repo_id": getattr(artifact, "id", None),
                "artifact_filename": getattr(variant, "filename", None),
                "local_match": getattr(result, "local_path", None) is not None,
                "local_path": getattr(result, "local_path", None),
                "parameter_count": getattr(model, "parameter_count", 0),
                "published_at": getattr(model, "published_at", None),
                "downloads": getattr(model, "downloads", 0),
                "quant_type": getattr(variant, "quant_type", ""),
                "file_size_bytes": getattr(variant, "file_size_bytes", None),
                "vram_required_bytes": getattr(result, "vram_required_bytes", 0),
                "vram_available_bytes": getattr(result, "vram_available_bytes", 0),
                "estimated_tok_per_sec": getattr(result, "estimated_tok_per_sec", None),
                "quality_score": getattr(result, "quality_score", None),
                "fit_type": getattr(result, "fit_type", ""),
                "license": getattr(model, "license", None),
            }
        )

    return {
        "hardware": {
            "gpus": [gpu_payload(g) for g in getattr(hardware, "gpus", [])],
            "cpu": getattr(hardware, "cpu_name", ""),
            "cpu_cores": getattr(hardware, "cpu_cores", 0),
            "ram_bytes": getattr(hardware, "ram_bytes", 0),
            "ram_budget_bytes": getattr(hardware, "ram_budget_bytes", None),
            "budget_notes": list(getattr(hardware, "budget_notes", []) or []),
            "os": getattr(hardware, "os", ""),
        },
        "models": models,
    }


def results_markdown(
    engine: Engine,
    hardware: Any,
    results: Sequence[Any],
    *,
    empty_message: str | None = None,
) -> str:
    """生成与 ``whichllm --markdown`` 相同的 GitHub 风格 Markdown 表格。"""
    from whichllm.output.markdown import display_markdown

    with capture_console() as buffer:
        try:
            display_markdown(
                list(results), hardware, show_status=True, empty_message=empty_message
            )
        except BaseException as exc:  # noqa: BLE001
            raise EngineError(f"导出 Markdown 失败：{exc}") from exc
    return _plain(buffer.getvalue())


CSV_HEADERS = [
    ("rank", "排名"),
    ("model_id", "模型"),
    ("parameters", "参数量"),
    ("quant", "量化"),
    ("file_size", "文件体积"),
    ("vram_required", "显存需求"),
    ("fit", "运行适配"),
    ("tok_per_sec", "速度 tok/s"),
    ("speed_confidence", "速度置信度"),
    ("quality", "质量分"),
    ("evidence", "基准证据"),
    ("downloads", "下载量"),
    ("published", "发布日期"),
    ("license", "许可证"),
    ("artifact_repo", "GGUF 仓库"),
    ("artifact_file", "GGUF 文件"),
    ("local_path", "本地路径"),
    ("warnings", "提示"),
]


def results_csv(results: Sequence[Any]) -> str:
    """推荐结果的 CSV（utf-8-sig，Excel 友好）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in CSV_HEADERS])
    for index, result in enumerate(results, 1):
        model = result.model
        variant = getattr(result, "gguf_variant", None)
        artifact = getattr(result, "artifact_model", None)
        speed = getattr(result, "estimated_tok_per_sec", None)
        quality = getattr(result, "quality_score", None)
        writer.writerow(
            [
                index,
                model.id,
                fmt_params(getattr(model, "parameter_count", 0)),
                getattr(variant, "quant_type", "") or "",
                getattr(variant, "file_size_bytes", None) or "",
                getattr(result, "vram_required_bytes", None) or "",
                fit_label(getattr(result, "fit_type", "")),
                f"{speed:.1f}" if speed else "",
                getattr(result, "speed_confidence", "") or "",
                f"{quality:.2f}" if quality is not None else "",
                evidence_label(getattr(result, "benchmark_status", "")),
                getattr(model, "downloads", 0) or 0,
                (getattr(model, "published_at", None) or "")[:10],
                getattr(model, "license", None) or "",
                getattr(artifact, "id", None) or "",
                getattr(variant, "filename", None) or "",
                getattr(result, "local_path", None) or "",
                " / ".join(getattr(result, "warnings", []) or []),
            ]
        )
    return buffer.getvalue()


def plan_markdown(plan: dict) -> str:
    """把 plan 的 JSON 结果整理成易读的 Markdown。"""
    model = plan.get("model") or {}
    lines = [
        f"# 运行规划：{model.get('id', '未知模型')}",
        "",
        f"- 目标量化：`{plan.get('target_quant', '-')}`",
        f"- 上下文长度：{plan.get('context_length', '-')} tokens",
        f"- 参数量：{fmt_params(model.get('parameter_count'))}",
        f"- 架构：{model.get('architecture') or '-'}",
        f"- 许可证：{model.get('license') or '-'}",
        "",
        "## 各量化档位所需显存",
        "",
        "| 量化 | 需要显存 | 质量损失 |",
        "| --- | --- | --- |",
    ]
    for quant, payload in (plan.get("vram_by_quant") or {}).items():
        gb = (payload.get("vram_bytes") or 0) / 1024**3
        loss = (payload.get("quality_loss") or 0) * 100
        lines.append(f"| {quant} | {gb:.1f} GB | {loss:.1f}% |")

    lines += [
        "",
        "## 显卡兼容性",
        "",
        "| 显卡 | 显存 | 适配 | 预估速度 |",
        "| --- | --- | --- | --- |",
    ]
    for gpu in plan.get("gpu_compatibility") or []:
        speed = gpu.get("estimated_tok_per_sec")
        lines.append(
            f"| {gpu.get('name')} | {gpu.get('vram_gb')} GB | "
            f"{fit_label(gpu.get('fit_type'))} | "
            f"{f'{speed:.1f} tok/s' if speed else '-'} |"
        )
    return "\n".join(lines) + "\n"


def upgrade_markdown(upgrade: dict) -> str:
    """把 upgrade 的 JSON 结果整理成易读的 Markdown。"""
    current = upgrade.get("current") or {}
    lines = [
        "# 显卡升级对比",
        "",
        f"当前：{current.get('gpu') or 'CPU'}（{current.get('vram_gb', 0):.1f} GB 显存）",
        "",
        "| 方案 | 显存 | 最佳模型 | 质量分 | 速度 tok/s | 质量提升 | 速度变化 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    def row(payload: dict, name: str | None = None) -> str:
        quality = payload.get("top_quality")
        speed = payload.get("top_tok_s")
        delta_q = payload.get("delta_quality")
        delta_s = payload.get("delta_tok_s")
        return (
            f"| {name or payload.get('name')} | {payload.get('vram_gb', 0):.1f} GB | "
            f"{payload.get('top_model') or '-'} | "
            f"{f'{quality:.1f}' if quality is not None else '-'} | "
            f"{f'{speed:.1f}' if speed is not None else '-'} | "
            f"{f'{delta_q:+.1f}' if delta_q is not None else '-'} | "
            f"{f'{delta_s:+.1f}' if delta_s is not None else '-'} |"
        )

    lines.append(row(current, "当前硬件"))
    for target in upgrade.get("targets") or []:
        lines.append(row(target))
    return "\n".join(lines) + "\n"


def plan_csv(plan: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["量化", "需要显存(GB)", "质量损失(%)"])
    for quant, payload in (plan.get("vram_by_quant") or {}).items():
        writer.writerow(
            [
                quant,
                f"{(payload.get('vram_bytes') or 0) / 1024**3:.2f}",
                f"{(payload.get('quality_loss') or 0) * 100:.1f}",
            ]
        )
    writer.writerow([])
    writer.writerow(["显卡", "显存(GB)", "适配", "预估速度(tok/s)"])
    for gpu in plan.get("gpu_compatibility") or []:
        writer.writerow(
            [
                gpu.get("name"),
                gpu.get("vram_gb"),
                fit_label(gpu.get("fit_type")),
                gpu.get("estimated_tok_per_sec") or "",
            ]
        )
    return buffer.getvalue()


def upgrade_csv(upgrade: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["方案", "显卡", "显存(GB)", "最佳模型", "质量分", "速度(tok/s)", "质量提升", "速度变化"]
    )

    def write_row(payload: dict, name: str) -> None:
        writer.writerow(
            [
                name,
                payload.get("gpu"),
                payload.get("vram_gb"),
                payload.get("top_model"),
                payload.get("top_quality"),
                payload.get("top_tok_s"),
                payload.get("delta_quality", ""),
                payload.get("delta_tok_s", ""),
            ]
        )

    write_row(upgrade.get("current") or {}, "当前硬件")
    for target in upgrade.get("targets") or []:
        write_row(target, target.get("name") or "")
    return buffer.getvalue()


def details_text(result: Any) -> str:
    """推荐列表右侧的详情文本。"""
    model = result.model
    variant = getattr(result, "gguf_variant", None)
    artifact = getattr(result, "artifact_model", None)
    lines: list[str] = []
    lines.append(f"模型：{model.id}")
    lines.append(f"参数量：{fmt_params(getattr(model, 'parameter_count', 0))}"
                 + (
                     f"（MoE 激活 {fmt_params(getattr(model, 'parameter_count_active', None))}）"
                     if getattr(model, "is_moe", False) and getattr(model, "parameter_count_active", None)
                     else ""
                 ))
    lines.append(f"架构：{getattr(model, 'architecture', '') or '-'}")
    lines.append(f"许可证：{getattr(model, 'license', None) or '-'}")
    lines.append(f"发布时间：{(getattr(model, 'published_at', None) or '-')[:10]}")
    lines.append(f"下载量：{getattr(model, 'downloads', 0):,}")
    lines.append("")
    lines.append(f"运行适配：{fit_label(getattr(result, 'fit_type', ''))}")
    offload = getattr(result, "offload_ratio", None)
    if offload:
        lines.append(f"卸载比例：{offload * 100:.0f}% 的层会放到 CPU 内存")
    lines.append(f"量化类型：{getattr(variant, 'quant_type', '') or '-'}")
    lines.append(f"文件体积：{_bytes(getattr(variant, 'file_size_bytes', None))}")
    lines.append(f"显存需求：{_bytes(getattr(result, 'vram_required_bytes', None))}")
    lines.append(f"可用显存：{_bytes(getattr(result, 'vram_available_bytes', None))}")
    if getattr(result, "uses_multi_gpu", False):
        lines.append(f"多卡合计显存：{_bytes(getattr(result, 'multi_gpu_effective_vram_bytes', None))}")
    speed = getattr(result, "estimated_tok_per_sec", None)
    rng = getattr(result, "speed_range_tok_per_sec", None)
    if speed:
        text = f"预估速度：{speed:.1f} tok/s"
        if rng:
            text += f"（区间 {rng[0]:.1f} ~ {rng[1]:.1f}）"
        confidence = getattr(result, "speed_confidence", None)
        if confidence:
            text += f"　置信度：{SPEED_CONFIDENCE_LABELS.get(confidence, confidence)}"
        lines.append(text)
    quality = getattr(result, "quality_score", None)
    if quality is not None:
        lines.append(
            f"质量分：{quality:.2f}　证据：{evidence_label(getattr(result, 'benchmark_status', ''))}"
            f"（来源 {getattr(result, 'benchmark_source', None) or '-'}，"
            f"置信度 {getattr(result, 'benchmark_confidence', 0):.2f}）"
        )
    lines.append("")
    lines.append(f"GGUF 仓库：{getattr(artifact, 'id', None) or '（无官方 GGUF，体积为估算值）'}")
    lines.append(f"GGUF 文件：{getattr(variant, 'filename', None) or '-'}")
    local_path = getattr(result, "local_path", None)
    lines.append(f"本地文件：{local_path or '（未在本地模型库中找到）'}")
    lines.append("")
    warnings = getattr(result, "warnings", None) or []
    if warnings:
        lines.append("提示：")
        lines.extend(f"  · {w}" for w in warnings)
    else:
        lines.append("提示：无")
    notes = getattr(result, "speed_notes", None) or []
    if notes:
        lines.append("")
        lines.append("速度估算说明：")
        lines.extend(f"  · {note}" for note in notes)
    return "\n".join(lines)


def _bytes(value: Any) -> str:
    from .engine import fmt_bytes

    return fmt_bytes(value)
