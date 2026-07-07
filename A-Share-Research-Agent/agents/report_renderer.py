"""ReportRenderer — 把 Committee 决策 + 三层 outlook 渲染成 markdown 报告。

输入: market_outlook + sector_outlooks + stock_outlooks + committee_decision
输出: markdown 文本
"""
from __future__ import annotations

import logging
from datetime import datetime

from agents.base import AgentOutputEnvelope

logger = logging.getLogger(__name__)


class ReportRenderer:
    """把 agent 输出渲染成结构化 markdown 报告。"""

    def render(
        self,
        *,
        business_date: str,
        window_type: str,
        market_outlook: AgentOutputEnvelope | None,
        sector_outlooks: list[AgentOutputEnvelope],
        stock_outlooks: list[AgentOutputEnvelope],
        committee_decision: AgentOutputEnvelope | None,
    ) -> str:
        lines: list[str] = []
        lines.append(f"# A 股投研日报 — {business_date}（{window_type}）")
        lines.append("")
        lines.append(f"_生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")
        lines.append("")

        # 1) 委员会决策
        if committee_decision is not None:
            so = committee_decision.structured_output or {}
            lines.append("## 1. 委员会最终决策")
            lines.append("")
            lines.append(f"- **Verdict**: `{so.get('verdict', '?')}`")
            lines.append(f"- **Confidence**: `{so.get('confidence', 0):.2f}`")
            lines.append(f"- **Risk Budget**: `{so.get('risk_budget', 'normal')}`")
            lines.append(f"- **Rationale**: {so.get('rationale', '—')}")
            if "llm_unavailable_fallback" in (committee_decision.warnings or []) or (
                "LLM fallback used" in (committee_decision.warnings or [])
            ):
                lines.append("- _本次决策为规则 baseline，LLM 不可用或推理失败_")
            approved = so.get("approved_targets") or []
            if approved:
                lines.append("")
                lines.append("### 1.1 准入标的（≤5）")
                lines.append("")
                lines.append("| 标的 | 趋势 | 置信度 |")
                lines.append("|---|---|---|")
                for t in approved:
                    if isinstance(t, dict):
                        lines.append(
                            f"| {t.get('symbol', '?')} | {t.get('trend', '—')} | "
                            f"{t.get('confidence', 0):.2f} |"
                        )
            blocked = so.get("blocked_targets") or []
            if blocked:
                lines.append("")
                lines.append("### 1.2 拦截标的")
                lines.append("")
                for b in blocked:
                    if isinstance(b, dict):
                        lines.append(f"- {b.get('symbol', '?')}：{b.get('reason', '—')}")
                    else:
                        lines.append(f"- {b}")
            lines.append("")

        # 2) 市场层
        if market_outlook is not None:
            so = market_outlook.structured_output or {}
            lines.append("## 2. 市场层（MarketRegimeAnalyst）")
            lines.append("")
            lines.append(f"- 市场状态：`{so.get('market_regime', '?')}`")
            lines.append(f"- 风险偏好：`{so.get('risk_appetite', '?')}`")
            lines.append(f"- 流动性：`{so.get('liquidity', '?')}`")
            if so.get("key_factors"):
                lines.append("- 关键驱动:")
                for f in so["key_factors"]:
                    lines.append(f"  - {f}")
            lines.append("")

        # 3) 板块层
        if sector_outlooks:
            lines.append("## 3. 板块层")
            lines.append("")
            lines.append("| sector_id | 命名 | 阶段 | 强度 | 一句结论 |")
            lines.append("|---|---|---|---|---|")
            for env in sector_outlooks:
                so = env.structured_output or {}
                lines.append(
                    f"| {so.get('sector_id', '?')} | "
                    f"{so.get('sector_name', '—')} | "
                    f"`{so.get('sector_stage', '?')}` | "
                    f"{so.get('strength_score', 0):.2f} | "
                    f"{so.get('one_liner', '—')[:60]} |"
                )
            lines.append("")

        # 4) 个股层
        if stock_outlooks:
            lines.append("## 4. 个股层")
            lines.append("")
            lines.append("| 标的 | 趋势 | 战术标签 | 置信度 | 一句结论 |")
            lines.append("|---|---|---|---|---|")
            for env in stock_outlooks:
                so = env.structured_output or {}
                trend = so.get("trend_signal") or so.get("trend") or "?"
                tags = ", ".join(so.get("tactical_flags") or so.get("tactical_tags") or []) or "—"
                confidence = float(so.get("conviction_score", so.get("confidence", 0.0)) or 0.0)
                one_liner = so.get("one_liner") or so.get("key_evidence", "—")
                lines.append(
                    f"| {so.get('symbol', '?')} | "
                    f"`{trend}` | "
                    f"{tags} | "
                    f"{confidence:.2f} | "
                    f"{one_liner[:60]} |"
                )
            lines.append("")

        # 5) 免责声明
        lines.append("---")
        lines.append("_本报告由 A-Share-Research-Agent 自动生成，仅供研究参考，不构成投资建议。_")
        return "\n".join(lines)
