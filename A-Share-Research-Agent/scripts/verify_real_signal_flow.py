"""真实信号流端到端验证 — LLM 跑图 + DB 落库 + 真读出。

跑前：
- .env 中 LLM_API_KEY / LLM_API_BASE 配好（DeepSeek 真可调）
- DATABASE_URL 配好（真 PG 可连）

跑后：写 results_real_signal_flow.json 备查。

设计：
- LLM 不可用 → status=llm_unavailable（不抛）
- DB 不可用 → status=db_unavailable（不抛）
- 任一环节失败 → status=error（不抛，输出到 json）
- 全成功 → status=ok，4 类信号落库 + 读出 + summary 计数
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.llm.client import LLMClient, LLMUnavailable
from graph.research_graph import build_research_graph
from graph.state import ResearchWorkflowState


def _initial_state() -> ResearchWorkflowState:
    """构造一个能跑通 6 节点的初始 state（跟 test_graph_e2e_real.py 一致）。"""
    return {
        "business_date": "2026-07-01",
        "window_type": "close",
        "target_symbol": "",
        "market_snapshot": {"market_regime": "risk_on_expansion"},
        "market_rule_hits": [],
        "sector_snapshots": [
            {
                "sector_id": "S1",
                "sector_name": "电子",
                "continuation_score": 0.7,
                "flow_inflow_score": 0.6,
                "event_confirmation_score": 0.5,
            },
        ],
        "sector_rule_hits_map": {},
        "stock_snapshots": [
            {
                "symbol": "000636",
                "code": "000636",
                "sector_id": "S1",
                "trend_signal": "bullish",
                "conviction_score": 0.7,
            },
        ],
        "stock_rule_hits_map": {},
        "market_outlook": None,
        "sector_outlooks": [],
        "stock_outlooks": [],
        "committee_decision": None,
        "final_report": "",
        "ranked_sectors": [],
        "halt_execution": False,
        "halt_reason": "",
    }


def _check_llm() -> tuple[bool, str]:
    """探测 LLM 是否可用。"""
    try:
        client = LLMClient()
        if client.chat_model is None:
            return False, "LLMClient.chat_model is None（缺 api_key）"
        return True, f"provider={client.provider} model={client.model_name}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _check_db() -> tuple[bool, str]:
    """探测 DB 是否可用（短超时连接 + SELECT 1）。

    DATABASE_URL override：跑脚本前 export 即可（Pydantic Settings 优先读 env var）。
    例：
        export DATABASE_URL="postgresql+asyncpg://postgres:ashare_dev_pw_2026@localhost:5432/ashare_agent"
        py scripts/verify_real_signal_flow.py
    """
    try:
        from sqlalchemy import text

        from core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=5.0)
        return True, "SELECT 1 ok"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def main() -> None:
    print("=" * 60)
    print("真实信号流端到端验证（LLM graph → DB persist → fetch）")
    print("=" * 60)

    result: dict = {
        "ran_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
    }

    # 1) LLM 探活
    llm_ok, llm_info = _check_llm()
    result["llm"] = {"available": llm_ok, "info": llm_info}
    if not llm_ok:
        print(f"  ⚠ LLM 不可用: {llm_info}")
        result["status"] = "llm_unavailable"
        Path("results_real_signal_flow.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(f"结果写入: results_real_signal_flow.json (status={result['status']})")
        return
    print(f"  ✅ LLM 可用: {llm_info}")

    # 2) DB 探活
    db_ok, db_info = await _check_db()
    result["db"] = {"available": db_ok, "info": db_info}
    if not db_ok:
        print(f"  ⚠ DB 不可用: {db_info}")
        result["status"] = "db_unavailable"
        Path("results_real_signal_flow.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(f"结果写入: results_real_signal_flow.json (status={result['status']})")
        return
    print(f"  ✅ DB 可用: {db_info}")

    # 3) 跑 graph
    print("\n[Step 1] 跑 6 节点 graph（真 LLM）")
    t0 = time.time()
    try:
        state = _initial_state()
        graph = build_research_graph()
        final = await graph.ainvoke(state)
        graph_elapsed = time.time() - t0
    except LLMUnavailable as e:
        print(f"  ⚠ LLMUnavailable: {e}")
        result["status"] = "llm_unavailable_runtime"
        result["error"] = str(e)
        Path("results_real_signal_flow.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        return
    except Exception as e:
        print(f"  ERROR graph 跑失败: {type(e).__name__}: {e}")
        result["status"] = "graph_error"
        result["error"] = f"{type(e).__name__}: {e}"
        Path("results_real_signal_flow.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        return

    result["graph_elapsed_sec"] = round(graph_elapsed, 3)
    result["graph_halt"] = final.get("halt_execution", False)
    result["graph_halt_reason"] = final.get("halt_reason", "")
    result["market_outlook_present"] = final.get("market_outlook") is not None
    result["committee_decision_present"] = final.get("committee_decision") is not None
    result["ranked_sectors_count"] = len(final.get("ranked_sectors") or [])
    result["stock_outlooks_count"] = len(final.get("stock_outlooks") or [])
    result["final_report_chars"] = len(final.get("final_report") or "")

    print(f"  ✅ graph 跑通: {graph_elapsed:.2f}s")
    print(f"     market_outlook={result['market_outlook_present']} "
          f"committee={result['committee_decision_present']} "
          f"sectors={result['ranked_sectors_count']} "
          f"stocks={result['stock_outlooks_count']} "
          f"report_chars={result['final_report_chars']}")

    if not final.get("committee_decision"):
        print("  ⚠ committee 没出决策，跳过落库")
        result["status"] = "no_committee_decision"
        Path("results_real_signal_flow.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        return

    # 4) 落库
    print("\n[Step 2] 4 类信号落库 fact_strategy_output")
    t0 = time.time()
    from core.database import AsyncSessionLocal
    from services.signal_persister import (
        fetch_signals_latest,
        persist_committee_signals,
    )

    biz_date = datetime.fromisoformat(final["business_date"]).date()
    try:
        async with AsyncSessionLocal() as session:
            persist_result = await persist_committee_signals(
                session,
                business_date=biz_date,
                window_type=final.get("window_type", "close"),
                market_outlook=final["market_outlook"],
                committee_decision=final["committee_decision"],
                ranked_sectors=final.get("ranked_sectors") or [],
                stock_outlooks=final.get("stock_outlooks") or [],
            )
        persist_elapsed = time.time() - t0
        result["persist_elapsed_sec"] = round(persist_elapsed, 3)
        result["persist_summary"] = persist_result.to_dict()
        print(f"  ✅ 落库完成: {persist_result.to_dict()}")
    except Exception as e:
        persist_elapsed = time.time() - t0
        print(f"  ERROR 落库失败: {type(e).__name__}: {e}")
        result["status"] = "persist_error"
        result["persist_elapsed_sec"] = round(persist_elapsed, 3)
        result["error"] = f"{type(e).__name__}: {e}"
        Path("results_real_signal_flow.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        return

    # 5) 真读出
    print("\n[Step 3] fetch_signals_latest 真读出")
    t0 = time.time()
    try:
        async with AsyncSessionLocal() as session:
            grouped = await fetch_signals_latest(session, business_date=biz_date)
        fetch_elapsed = time.time() - t0
        result["fetch_elapsed_sec"] = round(fetch_elapsed, 3)
        result["fetch_summary"] = {
            "market": len(grouped["market"]),
            "sector": len(grouped["sector"]),
            "approved": len(grouped["approved"]),
            "blocked": len(grouped["blocked"]),
        }
        # 取 market 一条的 verdict / risk_budget / rationale 摘要
        if grouped["market"]:
            m = grouped["market"][0]
            p = m.get("output_payload", {})
            result["fetch_market_verdict"] = p.get("verdict")
            result["fetch_market_risk_budget"] = p.get("risk_budget")
            result["fetch_market_rationale_excerpt"] = (p.get("rationale") or "")[:200]
        if grouped["approved"]:
            result["fetch_approved_symbols"] = [
                (a.get("output_payload") or {}).get("symbol") for a in grouped["approved"]
            ]
        if grouped["blocked"]:
            result["fetch_blocked_symbols"] = [
                (b.get("output_payload") or {}).get("symbol") for b in grouped["blocked"]
            ]
        print(f"  ✅ 读出: {result['fetch_summary']}")
    except Exception as e:
        fetch_elapsed = time.time() - t0
        print(f"  ERROR 读出失败: {type(e).__name__}: {e}")
        result["status"] = "fetch_error"
        result["fetch_elapsed_sec"] = round(fetch_elapsed, 3)
        result["error"] = f"{type(e).__name__}: {e}"
        Path("results_real_signal_flow.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        return

    # 6) 写结果
    result["status"] = "ok"
    out_path = Path("results_real_signal_flow.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("=" * 60)
    print("✅ 真实信号流端到端通过 (status=ok)")
    print(f"   graph: {result['graph_elapsed_sec']}s, persist: {result['persist_elapsed_sec']}s, fetch: {result['fetch_elapsed_sec']}s")
    print(f"   4 类信号: {result['fetch_summary']}")
    print(f"   结果: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
