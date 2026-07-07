"""examples/generate_000636_report.py — 端到端跑一份 000636 的真实研究报告示例。

直接用已知的 stock_snapshot + LLM 推理结果（来源：analyze_stock_live 真数据）
构造市场层 + 板块层 + 个股层 → 调 Committee + ReportRenderer 输出 markdown。

用法:
    docker exec -it ashare-api python /app/examples/generate_000636_report.py
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from agents.committee import CommitteeSynthesizer
from agents.report_renderer import ReportRenderer
from schemas.agent_io import AgentOutputEnvelope


def _env(agent_name: str, structured: dict, confidence: float = 0.5) -> AgentOutputEnvelope:
    return AgentOutputEnvelope(
        agent_name=agent_name,
        structured_output=structured,
        narrative_summary="",
        evidence_refs=[],
        confidence=confidence,
    )


async def main():
    business_date = datetime.now().strftime("%Y-%m-%d")

    # ── 1) 市场层（来自 analyze_stock_live 真数据） ─────────────────────
    market = _env(
        "MarketRegimeAnalyst",
        {
            "market_regime": "risk_on_expansion",
            "risk_appetite": "neutral",
            "liquidity": "ample",
            "key_factors": [
                "PMI 站上荣枯线, 制造业景气回升",
                "PBoC 流动性维持宽松, 隔夜 SHIBOR 处于低位",
                "国家队护盘动作明显, 沪深 300 ETF 持续净申购",
            ],
        },
        confidence=0.78,
    )

    # ── 2) 板块层 ─────────────────────────────────────────────────────
    sectors = [
        _env(
            "SectorAnalyst",
            {
                "sector_id": "IND_ELEC",
                "sector_name": "电子元件",
                "sector_stage": "diverging",
                "strength_score": 0.62,
                "one_liner": "AI 算力板块高位震荡, MLCC 涨价预期分化, 龙头主动回调",
            },
            confidence=0.68,
        ),
        _env(
            "SectorAnalyst",
            {
                "sector_id": "IND_BANK",
                "sector_name": "银行",
                "sector_stage": "confirmed",
                "strength_score": 0.75,
                "one_liner": "国家队护盘主力, 国有大行高位维稳, 高股息再受关注",
            },
            confidence=0.81,
        ),
    ]

    # ── 3) 个股层（来自 000636 真数据） ────────────────────────────────
    stock = _env(
        "StockAnalyst",
        {
            "symbol": "000636",
            "trend_signal": "bearish",
            "entry_timing": "avoid",
            "risk_mode": "defensive",
            "position_role": "leader",
            "conviction_score": 0.25,
            "support_level": 66.96,
            "resistance_level": 71.0,
            "stop_loss_hint": 66.96,
            "tactical_flags": ["a_kill", "first_yin_after_streak"],
            "key_evidence": (
                "风华高科今日下跌-6.16%, 振幅高达-6.44%, 成交额25.2亿, 换手率2.66%, "
                "成交活跃. 今日股价跌停. 上海某券商发布\"涨价就涨\"调研报告, "
                "公司近期大股东减持, 套现\"A杀\"形态, 估值百分位高达95%, PE(TTM)262倍, "
                "泡沫迹象明显. 尽管游资继续买入4.33亿, 中长期趋势下行, "
                "主资净卖全天共流出-23.56亿, 游资进场机构卖出形态非常态."
            ),
            "upstream_alignment": "conflicting",
        },
        confidence=0.25,
    )

    # ── 4) 委员会 + 报告渲染 ──────────────────────────────────────────
    committee = CommitteeSynthesizer()
    decision = await committee.run(
        market_outlook=market,
        sector_outlooks=sectors,
        stock_outlooks=[stock],
    )

    renderer = ReportRenderer()
    md = renderer.render(
        business_date=business_date,
        window_type="close",
        market_outlook=market,
        sector_outlooks=sectors,
        stock_outlooks=[stock],
        committee_decision=decision,
    )

    # ── 5) 输出 ─────────────────────────────────────────────────────
    out_dir = Path(__file__).parent / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "000636_report.md"
    json_path = out_dir / "000636_report.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "business_date": business_date,
                "market": market.structured_output,
                "sectors": [s.structured_output for s in sectors],
                "stock": stock.structured_output,
                "committee_decision": decision.structured_output,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"✅ 报告已生成: {md_path}")
    print(f"✅ JSON 已生成: {json_path}")
    print("\n--- 委员会决策 ---")
    print(json.dumps(decision.structured_output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
