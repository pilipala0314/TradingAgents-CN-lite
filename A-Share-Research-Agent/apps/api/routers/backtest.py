"""回测 API 端点 — P2.2 ~ P2.7 / P4.5 增强版。

端点列表：
- `GET  /v1/backtest/strategies`           列出可用策略
- `GET  /v1/backtest/universes`             列出 universe preset（P2.6）
- `GET  /v1/backtest/pool/{preset}`         拉 preset 成分股（hs300/csi500/...）
- `GET  /v1/backtest/default-config`        默认配置
- `POST /v1/backtest/run`                   同步回测（保留原语义；返完整 result）
- `POST /v1/backtest/run/async`             异步回测（P2.3；返 job_id）
- `GET  /v1/backtest/runs/{job_id}`         轮询 job 状态 + 结果（P2.5 / P2.3）
- `GET  /v1/backtest/runs`                  历史 run 列表（P2.5）
- `GET  /v1/backtest/runs/{job_id}/attribution`  P4.5 归因结果
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from core.database import AsyncSessionLocal
from data_pipeline.providers.facade import get_router
from data_pipeline.universes import (
    UniverseError,
    list_universes,
    resolve_universe,
)
from data_pipeline.universes import (
    is_valid as universe_is_valid,
)
from services.backtest_engine import BacktestConfig, BacktestEngine
from services.backtest_persister import (
    compare_runs,
    config_to_dict,
    fetch_by_job_id,
    list_backtest_runs,
    persist_backtest_run,
    update_backtest_run_status,
)
from services.strategies import list_strategies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/backtest", tags=["backtest"])


# ── 请求/响应模型 ──────────────────────────────────────
class BacktestRunRequest(BaseModel):
    """回测请求 payload。"""

    strategy_name: str = Field(..., description="策略名（见 /v1/backtest/strategies）")
    universe: str = Field("hs300", description="股票池 preset：hs300/csi500/.../all_a")
    symbols: list[str] | None = Field(None, description="显式股票池（与 universe 互斥，优先）")
    start_date: date | None = Field(None, description="起始日期（None = 默认 1 年前）")
    end_date: date | None = Field(None, description="结束日期（None = 默认今日）")
    top_n: int = Field(20, ge=1, le=100, description="持仓数量")
    rebalance_freq: str = Field("weekly", description="daily/weekly/monthly")
    initial_capital: float = Field(1_000_000.0, gt=0, description="初始资金")
    # P2.4 成本模型
    commission_rate: float = Field(0.0003, ge=0, lt=0.01, description="双边手续费率")
    stamp_tax_rate: float = Field(0.001, ge=0, lt=0.05, description="卖出单边印花税率")
    slippage_rate: float = Field(0.001, ge=0, lt=0.05, description="双边滑点率")
    # P2.7 基准对比
    benchmark: str | None = Field(None, description="基准指数代码（如 000300.SH）")
    # P4.5 归因
    compute_attribution: bool = Field(False, description="是否算归因（耗 CPU）")


# ── 元数据端点 ─────────────────────────────────────────
@router.get("/strategies")
async def get_strategies() -> dict[str, Any]:
    """列出所有内置回测策略。"""
    return {"strategies": list_strategies()}


@router.get("/universes")
async def get_universes() -> dict[str, Any]:
    """列出所有 universe preset（P2.6）。"""
    return {"universes": list_universes()}


@router.get("/pool/{preset}")
async def pool_preset(preset: str, sample_n: int | None = None) -> dict[str, Any]:
    """拉指定 preset 成分股 code 列表（P2.6）。

    Args:
        preset:  hs300 / csi500 / csi1000 / gem / star / all_a
        sample_n: ALL_A 池随机抽样数（None=全量）
    """
    if not universe_is_valid(preset):
        raise HTTPException(
            status_code=400,
            detail=f"未知 universe：{preset}。可选：{[u['preset'] for u in list_universes()]}",
        )
    try:
        codes = await resolve_universe(preset, router=get_router(), sample_n=sample_n)
    except UniverseError as exc:
        raise HTTPException(
            status_code=502, detail=f"universe {preset} 解析失败: {exc}"
        ) from exc
    if not codes:
        raise HTTPException(status_code=502, detail=f"universe {preset} 成分股为空")
    return {"preset": preset, "count": len(codes), "codes": codes}


@router.get("/default-config")
async def default_config() -> dict[str, Any]:
    """返回默认配置（前端表单回填用）。"""
    end = date.today()
    start = end - timedelta(days=365)
    return {
        "strategy_name": "momentum_20d",
        "universe": "hs300",
        "symbols": None,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "top_n": 20,
        "rebalance_freq": "weekly",
        "initial_capital": 1_000_000.0,
        "commission_rate": 0.0003,
        "stamp_tax_rate": 0.001,
        "slippage_rate": 0.001,
        "benchmark": "000300.SH",
        "compute_attribution": False,
    }


# ── 同步回测（保留原 /run 语义） ───────────────────
def _build_config(payload: BacktestRunRequest) -> BacktestConfig:
    """从 request 构造 BacktestConfig（带校验）。"""
    end_date = payload.end_date or date.today()
    start_date = payload.start_date or (end_date - timedelta(days=365))
    if start_date >= end_date:
        raise HTTPException(
            status_code=400, detail="start_date 必须早于 end_date"
        )
    symbols = payload.symbols or []
    universe = payload.universe if not symbols else None
    try:
        return BacktestConfig(
            strategy_name=payload.strategy_name,
            symbols=symbols,
            universe=universe,
            start_date=start_date,
            end_date=end_date,
            initial_capital=payload.initial_capital,
            top_n=payload.top_n,
            rebalance_freq=payload.rebalance_freq,
            commission_rate=payload.commission_rate,
            stamp_tax_rate=payload.stamp_tax_rate,
            slippage_rate=payload.slippage_rate,
            benchmark=payload.benchmark,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/run")
async def run_backtest(payload: BacktestRunRequest) -> dict[str, Any]:
    """执行回测（同步阻塞；~秒级返回）。"""
    config = _build_config(payload)
    data_router = get_router()
    engine = BacktestEngine(router=data_router)
    try:
        result = engine.run(config)
    except Exception as exc:
        logger.exception("回测失败: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"回测失败: {type(exc).__name__}: {exc}"
        ) from exc
    out = result.to_dict()
    if payload.compute_attribution:
        # run() 内部已拉 bars 进 router.cache，这里需重新拉一次 — 简化：暂时跳过
        # （如需归因，调用方应改用 /run/async，结果里带 attribution_json）
        out["attribution"] = None
    return out


# ── 异步回测（P2.3） ─────────────────────────────
async def _run_async_job(
    *,
    job_id: str,
    config_dict: dict[str, Any],
    compute_attribution: bool,
) -> None:
    """BackgroundTasks 回调：跑回测 + 落 status=completed。

    进程内运行（无 Celery）。失败 status=failed。

    关键：使用 `async def` 而非 `def + asyncio.run()`，让 BackgroundTasks
    在 FastAPI 主 event loop 上 await。否则 `asyncio.run()` 会创建新 loop，
    SQLAlchemy 异步连接池里来自主 loop 的连接会"attached to a different loop"，
    导致 terminate 时炸出 RuntimeError（典型跨 loop 错误）。
    """
    async with AsyncSessionLocal() as session:
        # 1. status=running
        await update_backtest_run_status(
            session, job_id=job_id, status="running",
        )
        # 2. 跑回测
        try:
            config = BacktestConfig(**config_dict)
            engine = BacktestEngine(router=get_router())
            result = await asyncio.to_thread(engine.run, config)
        except Exception as exc:
            logger.exception("异步回测 %s 失败: %s", job_id, exc)
            await update_backtest_run_status(
                session, job_id=job_id, status="failed",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            return
        # 3. 归因（可选）
        attribution = None
        if compute_attribution:
            try:
                # 简化：仅给 rebalance_attribution（不需 bars）
                from services.backtest_attribution import (
                    compute_rebalance_attribution,
                )
                attribution = {
                    "rebalance": compute_rebalance_attribution(result),
                }
            except Exception as exc:
                logger.warning("归因计算失败（不影响 run 落库）: %s", exc)
        # 4. status=completed + 落 result
        await update_backtest_run_status(
            session, job_id=job_id, status="completed",
            result=result, attribution=attribution,
        )


@router.post("/run/async")
async def run_backtest_async(
    payload: BacktestRunRequest, background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """异步回测（P2.3）：立即返 job_id，后台跑，跑完可查。

    Returns:
        {"job_id": uuid, "status": "pending", "config_hash": ..., "business_date": ...}
    """
    config = _build_config(payload)
    job_id = str(uuid.uuid4())
    bdate = date.today()
    try:
        # 落 FactBacktestRun（status=pending）
        async with AsyncSessionLocal() as session:
            record = await persist_backtest_run(
                session, config=config, business_date=bdate, job_id=job_id,
            )
    except Exception as exc:
        logger.exception("异步回测 job 落库失败: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"job 落库失败: {type(exc).__name__}: {exc}"
        ) from exc
    # BackgroundTasks 调度（进程内 — 重启会丢未完成 job）
    config_dict = config_to_dict(config)
    background_tasks.add_task(
        _run_async_job,
        job_id=job_id,
        config_dict=config_dict,
        compute_attribution=payload.compute_attribution,
    )
    return {
        "job_id": job_id,
        "status": "pending",
        "config_hash": record.config_hash,
        "business_date": record.business_date,
        "config": config_dict,
    }


# ── 轮询 + 历史（P2.5） ─────────────────────────
@router.get("/runs/{job_id}")
async def get_run(job_id: str) -> dict[str, Any]:
    """按 job_id 查 run 状态 + 结果（P2.3 轮询用）。"""
    try:
        async with AsyncSessionLocal() as session:
            run = await fetch_by_job_id(session, job_id=job_id)
    except Exception as exc:
        logger.error("get_run 失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503, detail=f"数据库不可达: {type(exc).__name__}: {exc}",
        ) from exc
    if not run:
        raise HTTPException(status_code=404, detail=f"job_id={job_id} 不存在")
    return run


@router.get("/runs")
async def list_runs(
    business_date: date | None = Query(None),
    status: str | None = Query(None),
    strategy_name: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """历史 run 列表（P2.5）。"""
    try:
        async with AsyncSessionLocal() as session:
            runs = await list_backtest_runs(
                session,
                business_date=business_date,
                status=status,
                strategy_name=strategy_name,
                limit=limit,
            )
    except Exception as exc:
        logger.error("list_runs 失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503, detail=f"数据库不可达: {type(exc).__name__}: {exc}",
        ) from exc
    return {"count": len(runs), "runs": runs}


@router.get("/runs/compare")
async def compare_runs_endpoint(
    ids: str = Query(..., description="逗号分隔的 job_id（最多 5 个）"),
) -> dict[str, Any]:
    """多 run 横向对比（P1-T3）。"""
    raw_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not raw_ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    if len(raw_ids) > 5:
        raise HTTPException(status_code=400, detail="最多 5 个 job_id")
    # 校验 UUID 格式
    for jid in raw_ids:
        try:
            uuid.UUID(jid)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400, detail=f"非法 job_id：{jid}",
            ) from exc
    try:
        async with AsyncSessionLocal() as session:
            runs = await compare_runs(session, raw_ids, max_n=5)
    except Exception as exc:
        logger.error("compare_runs 失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503, detail=f"数据库不可达: {type(exc).__name__}: {exc}",
        ) from exc
    return {
        "count": len(runs),
        "requested": raw_ids,
        "missing": [j for j in raw_ids if not any(r["job_id"] == j for r in runs)],
        "runs": runs,
    }


@router.get("/runs/{job_id}/attribution")
async def get_attribution(job_id: str) -> dict[str, Any]:
    """查 P4.5 归因结果。"""
    try:
        async with AsyncSessionLocal() as session:
            run = await fetch_by_job_id(session, job_id=job_id)
    except Exception as exc:
        logger.error("get_attribution 失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503, detail=f"数据库不可达: {type(exc).__name__}: {exc}",
        ) from exc
    if not run:
        raise HTTPException(status_code=404, detail=f"job_id={job_id} 不存在")
    attribution = run.get("attribution_json")
    if not attribution:
        raise HTTPException(
            status_code=404,
            detail=f"job_id={job_id} 无归因结果（提交时未指定 compute_attribution=true）",
        )
    return {
        "job_id": job_id,
        "status": run.get("status"),
        "attribution": attribution,
    }


# ── P5.2 walk-forward / 蒙特卡洛 ────────────────────


class WalkForwardRequest(BaseModel):
    """walk-forward 入参：日收益序列 + 窗口参数。"""

    returns: list[float] = Field(..., min_length=10, description="日收益序列（小数）")
    n_folds: int = Field(5, ge=1, le=20, description="折数")
    train_pct: float = Field(0.6, gt=0, lt=1, description="训练占比")
    anchored: bool = Field(True, description="anchored 模式（train 起点固定 = 序列首）")


@router.post("/wf/run")
def run_walkforward(payload: WalkForwardRequest) -> dict[str, Any]:
    """跑 walk-forward 滚动窗口验证（OOS 稳定性）。"""
    from services.walkforward import run_walkforward
    try:
        result = run_walkforward(
            returns=payload.returns,
            n_folds=payload.n_folds,
            train_pct=payload.train_pct,
            anchored=payload.anchored,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


class MonteCarloRequest(BaseModel):
    """蒙特卡洛入参。"""

    returns: list[float] = Field(..., min_length=10, description="历史日收益序列")
    n_sims: int = Field(500, ge=1, le=5000, description="模拟次数")
    horizon: int = Field(252, ge=1, le=2000, description="每条路径长度（日）")
    block_size: int = Field(5, ge=1, le=50, description="block bootstrap 块大小")
    seed: int | None = Field(42, description="随机种子（None = 不固定）")


@router.post("/mc/run")
def run_montecarlo(payload: MonteCarloRequest) -> dict[str, Any]:
    """跑蒙特卡洛模拟（block bootstrap）。"""
    from services.montecarlo import run_montecarlo
    try:
        result = run_montecarlo(
            returns=payload.returns,
            n_sims=payload.n_sims,
            horizon=payload.horizon,
            block_size=payload.block_size,
            seed=payload.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
