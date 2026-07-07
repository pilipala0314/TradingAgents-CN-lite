"""P1-T7 报告导出服务 — Markdown 格式。

输入：signals 4 类（market / sector / approved / blocked） + 当日宏观 + committee decision trace
输出：结构化 Markdown 报告

- 不依赖外部 markdown 库
- 失败语义：LLM 不可用 → 段落用 _render_llm_section 渲染 'LLM 不可用: <reason>' 占位
- 数据缺失 → 段落用 '—' 占位，不静默
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any

logger = logging.getLogger(__name__)


# ── section renderers（每个返回一段 markdown 文本）────────


def _render_market_section(market_signals: list[dict[str, Any]]) -> str:
    if not market_signals:
        return "## 市场概览\n\n— 暂无市场信号\n"
    top = market_signals[:5]
    lines = ["## 市场概览\n"]
    for s in top:
        direction = s.get("direction", "neutral")
        conf = s.get("confidence", 0.0)
        evi = s.get("supporting_evidence") or []
        lines.append(
            f"- **{s.get('entity_id', '?')}** 方向=`{direction}` 置信度={conf:.2f}"
        )
        if evi:
            lines.append(f"  - 证据: {'; '.join(str(e) for e in evi[:3])}")
    return "\n".join(lines) + "\n"


def _render_sector_section(sector_signals: list[dict[str, Any]]) -> str:
    if not sector_signals:
        return "## 板块轮动\n\n— 暂无板块信号\n"
    top = sorted(
        sector_signals, key=lambda s: s.get("confidence", 0.0), reverse=True,
    )[:10]
    lines = ["## 板块轮动\n"]
    for i, s in enumerate(top, 1):
        lines.append(
            f"{i}. **{s.get('entity_id', '?')}**  "
            f"方向=`{s.get('direction', '?')}` 置信度={s.get('confidence', 0.0):.2f}"
        )
    return "\n".join(lines) + "\n"


def _render_approved_section(approved: list[dict[str, Any]]) -> str:
    if not approved:
        return "## 准入个股（Top 10）\n\n— 当日无准入信号\n"
    top = sorted(approved, key=lambda s: s.get("confidence", 0.0), reverse=True)[:10]
    lines = [
        "## 准入个股（Top 10）\n",
        "| 代码 | 方向 | 模型评分 | 模型版本 | 关键证据 |",
        "|------|------|----------|----------|----------|",
    ]
    for s in top:
        evi = s.get("supporting_evidence") or []
        evi_short = "; ".join(str(e) for e in evi[:2])[:80] if evi else "—"
        lines.append(
            f"| {s.get('entity_id', '?')} | {s.get('direction', '?')} "
            f"| {s.get('confidence', 0.0):.2f} "
            f"| {s.get('model_version') or '—'} | {evi_short} |"
        )
    return "\n".join(lines) + "\n"


def _render_blocked_section(blocked: list[dict[str, Any]]) -> str:
    if not blocked:
        return "## 拦截个股（Top 10）\n\n— 当日无拦截信号\n"
    top = sorted(blocked, key=lambda s: s.get("confidence", 0.0), reverse=True)[:10]
    lines = [
        "## 拦截个股（Top 10）\n",
        "| 代码 | 方向 | 模型评分 | 拦截原因 |",
        "|------|------|----------|----------|",
    ]
    for s in top:
        invalid = s.get("invalid_conditions") or []
        reason = "; ".join(str(c) for c in invalid[:2])[:80] if invalid else "—"
        lines.append(
            f"| {s.get('entity_id', '?')} | {s.get('direction', '?')} "
            f"| {s.get('confidence', 0.0):.2f} | {reason} |"
        )
    return "\n".join(lines) + "\n"


def _render_attribution_section(attribution: dict[str, Any] | None) -> str:
    """归因分析（α / β / Sharpe 等）。"""
    if not attribution:
        return "## 归因分析\n\n— 当日无归因数据\n"
    lines = ["## 归因分析\n"]
    for k, v in attribution.items():
        if isinstance(v, (int, float)):
            lines.append(f"- **{k}** = {v:.4f}")
        else:
            lines.append(f"- **{k}** = {v}")
    return "\n".join(lines) + "\n"


# ── LLM 增强（可选）────────────────────────


async def _llm_enhance_section(
    section_title: str,
    payload: dict[str, Any],
) -> str:
    """调 LLM 写一段 200-300 字解读，失败 → 占位。

    失败语义：LLM 不可用 → 返回 '> LLM 不可用: <reason>'
    """
    try:
        from core.llm.factory import create_llm_client
        from core.llm.structured import invoke_structured_or_text

        client = create_llm_client()
        sys_prompt = (
            "你是 A 股研究助手。基于用户给的当日数据写 200-300 字的中文解读，"
            "聚焦重点变化、隐含风险、建议关注的方向。不要重复罗列数据本身。"
        )
        user_prompt = (
            f"## {section_title}\n\n"
            f"```json\n{payload}\n```\n\n"
            f"请基于以上数据给出本节的中文解读。"
        )
        text = await invoke_structured_or_text(
            client, sys_prompt, user_prompt, model="gpt-4o-mini",
            schema=None, max_tokens=512,
        )
        if not text or not text.strip():
            return "> LLM 返回为空"
        return f"> {text.strip()}"
    except Exception as exc:
        logger.warning("[report_exporter] LLM 增强 %s 失败: %s", section_title, exc)
        return f"> LLM 不可用: {type(exc).__name__}: {str(exc)[:160]}"


# ── 主入口 ─────────────────────────


async def export_daily_report(
    *,
    business_date: _date | str | None,
    grouped: dict[str, list[dict[str, Any]]],
    attribution: dict[str, Any] | None = None,
    llm_enhance: bool = True,
) -> str:
    """拼装完整 Markdown 报告。

    Args:
        business_date: 业务日期
        grouped: signals 4 类分组 (market/sector/approved/blocked)
        attribution: 归因分析 dict（可空）
        llm_enhance: 是否调 LLM 写解读段落（默认 True）

    Returns:
        完整 Markdown 字符串
    """
    if isinstance(business_date, str):
        date_str = business_date
    elif business_date is not None:
        date_str = business_date.isoformat()
    else:
        date_str = "未知"

    market = grouped.get("market", [])
    sector = grouped.get("sector", [])
    approved = grouped.get("approved", [])
    blocked = grouped.get("blocked", [])

    parts: list[str] = []
    parts.append(f"# A 股研究日报 · {date_str}\n")
    parts.append(f"> 共 {len(market)} 条市场信号 / {len(sector)} 条板块 / "
                 f"{len(approved)} 条准入 / {len(blocked)} 条拦截\n")

    # ── 市场概览 + AI 解读 ──
    market_md = _render_market_section(market)
    parts.append(market_md)
    if llm_enhance and market:
        try:
            ai_market = await _llm_enhance_section(
                "市场概览",
                {
                    "signals": market[:5],
                    "approved_count": len(approved),
                    "blocked_count": len(blocked),
                },
            )
            parts.append(f"🤖 **AI 解读**\n\n{ai_market}\n")
        except Exception as exc:
            logger.warning("[report_exporter] AI 解读 市场概览 失败: %s", exc)
            parts.append(f"🤖 **AI 解读**\n\n> LLM 不可用: {type(exc).__name__}: {str(exc)[:160]}\n")

    # ── 板块轮动 + AI 解读 ──
    parts.append(_render_sector_section(sector))
    if llm_enhance and sector:
        try:
            ai_sector = await _llm_enhance_section(
                "板块轮动", {"sectors": sector[:10]},
            )
            parts.append(f"🤖 **AI 解读**\n\n{ai_sector}\n")
        except Exception as exc:
            logger.warning("[report_exporter] AI 解读 板块轮动 失败: %s", exc)
            parts.append(f"🤖 **AI 解读**\n\n> LLM 不可用: {type(exc).__name__}: {str(exc)[:160]}\n")

    # ── 准入 / 拦截 ──
    parts.append(_render_approved_section(approved))
    parts.append(_render_blocked_section(blocked))

    # ── 归因 ──
    parts.append(_render_attribution_section(attribution))
    if llm_enhance and attribution:
        try:
            ai_attr = await _llm_enhance_section(
                "归因分析", attribution,
            )
            parts.append(f"🤖 **AI 解读**\n\n{ai_attr}\n")
        except Exception as exc:
            logger.warning("[report_exporter] AI 解读 归因分析 失败: %s", exc)
            parts.append(f"🤖 **AI 解读**\n\n> LLM 不可用: {type(exc).__name__}: {str(exc)[:160]}\n")

    return "\n".join(parts)
