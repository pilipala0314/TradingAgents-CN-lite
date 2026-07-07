"""APScheduler — 调度 daily research 流（容器内 API 进程同进程调度）。

默认 cron：
- 17:30 (工作日) 触发 run_research_for_business_date(昨日)
  - 时序：A 股 15:00 收盘 → 17:00 ETL 收尾 → 17:30 跑研究流（基于 ETL 落库的事实）
  - 跑完自动 persist committee 决策到 fact_strategy_output

设计原则：
- 失败不可阻塞 scheduler（log + return，不抛）
- 跑前 best-effort 做 LLM 探活（不探活也跑，graph 内部有 fallback）
- 与 etl_scheduler 解耦（独立 job 独立 id）
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


# ── 入口任务（可独立 import / 手动调） ─────────────────────────────
async def run_research_for_business_date(
    business_date: str | None = None,
    window_type: str = "close",
) -> dict[str, object]:
    """跑一次完整研究流：DB 模式装载 + graph 跑 + 4 类信号落库。

    Args:
        business_date: ISO 格式 'YYYY-MM-DD'（None = 昨日 Asia/Shanghai）
        window_type:   'close' / 'intraday'

    Returns:
        dict 含 ok / halt / persist_summary / 异常信息
    """
    from datetime import date as _date

    from core.database import AsyncSessionLocal
    from graph.research_graph import build_research_graph
    from graph.state import ResearchWorkflowState
    from services.research_context_loader import ResearchContextLoader
    from services.signal_persister import persist_committee_signals

    if business_date is None:
        # Asia/Shanghai 昨日
        bj_now = datetime.now(timezone(timedelta(hours=8)))
        biz_date = (bj_now - timedelta(days=1)).date()
        business_date = biz_date.isoformat()

    if isinstance(business_date, str):
        biz_date_obj = _date.fromisoformat(business_date)
    else:
        biz_date_obj = business_date
        business_date = biz_date_obj.isoformat()

    logger.info(
        "[research_scheduler] 开始跑 research flow: business_date=%s window=%s",
        business_date, window_type,
    )

    try:
        # 1) DB 装载快照
        async with AsyncSessionLocal() as session:
            try:
                context = await ResearchContextLoader.load(
                    session=session,
                    business_date=biz_date_obj,
                    window_type=window_type,
                )
            except LookupError as exc:
                msg = f"装载快照失败（数据未就绪?）: {exc}"
                logger.warning("[research_scheduler] %s", msg)
                return {"ok": False, "reason": msg, "business_date": business_date}

            initial_state: ResearchWorkflowState = {
                "business_date": business_date,
                "window_type": window_type,
                "target_symbol": "",
                "market_snapshot": context.market_snapshot,
                "market_rule_hits": context.market_rule_hits,
                "sector_snapshots": context.sector_snapshots,
                "sector_rule_hits_map": context.sector_rule_hits_map,
                "stock_snapshots": [],
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

            # 2) graph 跑
            graph = build_research_graph()
            final = await graph.ainvoke(initial_state)

            # 3) 信号落库（committee 跑了才落）
            persist_summary: dict[str, object] = {"status": "skipped"}
            if final.get("committee_decision") and final.get("market_outlook") is not None:
                try:
                    result = await persist_committee_signals(
                        session,
                        business_date=biz_date_obj,
                        window_type=window_type,
                        market_outlook=final["market_outlook"],
                        committee_decision=final["committee_decision"],
                        ranked_sectors=final.get("ranked_sectors") or [],
                        stock_outlooks=final.get("stock_outlooks") or [],
                    )
                    persist_summary = {"status": "ok", **result.to_dict()}
                except Exception as exc:
                    logger.warning(
                        "[research_scheduler] persist signals 失败: %s",
                        exc, exc_info=True,
                    )
                    persist_summary = {
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }

        logger.info(
            "[research_scheduler] 完成: business_date=%s halt=%s persist=%s",
            business_date, final.get("halt_execution", False), persist_summary,
        )
        return {
            "ok": True,
            "business_date": business_date,
            "window_type": window_type,
            "halt": final.get("halt_execution", False),
            "halt_reason": final.get("halt_reason", ""),
            "persist": persist_summary,
        }
    except Exception as exc:
        logger.error(
            "[research_scheduler] 跑失败: business_date=%s err=%s",
            business_date, exc, exc_info=True,
        )
        return {
            "ok": False,
            "business_date": business_date,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _safe_run_research() -> None:
    """scheduler 调度的同步入口 — 内部跑 async。"""
    try:
        result = asyncio.run(run_research_for_business_date())
        logger.info("[research_scheduler] _safe_run_research 返回: %s", result)
    except Exception as exc:
        logger.error("[research_scheduler] _safe_run_research 抛异常: %s", exc, exc_info=True)


# ── 注册入口 ─────────────────────────────────────────────────────
def register_research_jobs(scheduler: AsyncIOScheduler) -> None:
    """在已有 scheduler 上注册 research jobs（与 etl 共用一个 scheduler 实例）。"""
    scheduler.add_job(
        _safe_run_research,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=30),
        id="daily_research_flow",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    logger.info("APScheduler 已注册 daily_research_flow @ 17:30 (mon-fri)")


def start_research_scheduler() -> AsyncIOScheduler:
    """启动独立 scheduler（仅 research jobs）。"""
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    register_research_jobs(scheduler)
    scheduler.start()
    return scheduler
