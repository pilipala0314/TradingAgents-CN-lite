"""P2-T11/P2-T13 回测 → 线上信号 strategy selector。

提供：
- StrategySelector.get_current(tenant_id) -> FactStrategyConfig | None
- StrategySelector.set_from_run(run_id, tenant_id, selected_by, note) -> FactStrategyConfig
  - 读取 run 的 strategy_name / universe_code / params_json
  - 关闭该 tenant 所有 active config
  - 写入新行（is_active=True）
- StrategySelector.list_history(tenant_id, limit) -> list[FactStrategyConfig]
- pick_best_run(tenant_id, days, metric) -> str | None（P2-T13 用）
  - 取最近 N 天 completed run，按 metric 倒序，返回 top 1 run_id
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.dimensions import FactStrategyConfig

logger = logging.getLogger(__name__)


class StrategySelector:
    """管理线上 signal 当前用的 strategy 配置（手动 + 自动切换）。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_current(self, tenant_id: Optional[str] = None) -> Optional[FactStrategyConfig]:
        """返回当前 tenant 激活的策略配置（最新一条）。"""
        stmt = (
            select(FactStrategyConfig)
            .where(FactStrategyConfig.is_active.is_(True))
            .order_by(FactStrategyConfig.selected_at.desc())
        )
        if tenant_id is not None:
            stmt = stmt.where(FactStrategyConfig.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_history(
        self, tenant_id: Optional[str] = None, limit: int = 20
    ) -> list[FactStrategyConfig]:
        """列出历史策略配置（按时间倒序）。"""
        stmt = select(FactStrategyConfig).order_by(FactStrategyConfig.selected_at.desc()).limit(limit)
        if tenant_id is not None:
            stmt = stmt.where(FactStrategyConfig.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_from_run(
        self,
        run_id: uuid.UUID,
        tenant_id: Optional[str] = None,
        selected_by: Optional[str] = None,
        note: Optional[str] = None,
        selection_mode: str = "manual",
    ) -> FactStrategyConfig:
        """根据回测 run 切换当前 strategy（关闭旧 active → 写新 active）。

        Returns 新创建的 FactStrategyConfig 行。
        """
        # 1) 读回测 run 的关键配置（避免导入循环，直接 import）
        from models.outputs import FactBacktestRun

        run = (
            await self.session.execute(
                select(FactBacktestRun).where(FactBacktestRun.job_id == run_id)
            )
        ).scalars().first()
        if not run:
            raise ValueError(f"BacktestRun {run_id} 不存在")

        if run.status != "completed":
            raise ValueError(
                f"BacktestRun {run_id} 未完成（status={run.status}），不能切换线上策略"
            )

        # FactBacktestRun 的 strategy_name / universe / params 都存在 JSON 字段中
        cfg = run.config_json or {}
        result = run.result_json or {}
        metrics = result.get("metrics") or {}

        strategy_name = cfg.get("strategy_name") or "unknown"
        universe_code = cfg.get("universe") or cfg.get("universe_code")
        params_json = {
            "start_date": cfg.get("start_date"),
            "end_date": cfg.get("end_date"),
            "top_n": cfg.get("top_n"),
            "rebalance_freq": cfg.get("rebalance_freq"),
            "commission_rate": cfg.get("commission_rate"),
            "stamp_tax_rate": cfg.get("stamp_tax_rate"),
            "slippage_rate": cfg.get("slippage_rate"),
            "benchmark": cfg.get("benchmark"),
            "symbols_count": len(cfg.get("symbols") or []),
        }

        # 2) 关掉该 tenant 旧 active（保证唯一性）
        await self._deactivate_all(tenant_id)

        # 3) 计算 metrics_snapshot（关键指标取自 result_json.metrics）
        metrics_snapshot = {
            "total_return": metrics.get("total_return"),
            "annual_return": metrics.get("annual_return"),
            "sharpe": metrics.get("sharpe"),
            "max_drawdown": metrics.get("max_drawdown"),
            "win_rate": metrics.get("win_rate"),
            "cost_total_pct": float(run.cost_total_pct) if run.cost_total_pct is not None else None,
            "benchmark_alpha": float(run.benchmark_alpha) if run.benchmark_alpha is not None else None,
            "benchmark_beta": float(run.benchmark_beta) if run.benchmark_beta is not None else None,
            "business_date": run.business_date.isoformat() if run.business_date else None,
            "nav_points": result.get("nav_points"),
        }

        # 4) 写新 active
        new_cfg = FactStrategyConfig(
            run_id=run.job_id,
            strategy_name=strategy_name,
            universe_code=universe_code,
            params_json=params_json,
            is_active=True,
            selection_mode=selection_mode,
            metrics_snapshot=metrics_snapshot,
            note=note,
            selected_by=selected_by,
            tenant_id=tenant_id if tenant_id is not None else run.tenant_id,
            selected_at=datetime.now(timezone.utc),
        )
        self.session.add(new_cfg)
        await self.session.flush()
        logger.info(
            "StrategySelector.set_from_run: tenant=%s run_id=%s strategy=%s universe=%s",
            tenant_id, run_id, strategy_name, universe_code,
        )
        return new_cfg

    async def _deactivate_all(self, tenant_id: Optional[str]) -> int:
        """关掉 tenant 所有 active config（保留行，不删）。"""
        stmt = update(FactStrategyConfig).where(
            FactStrategyConfig.is_active.is_(True)
        ).values(is_active=False)
        if tenant_id is not None:
            stmt = stmt.where(FactStrategyConfig.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def pick_best_run(
        self,
        tenant_id: Optional[str] = None,
        days: int = 30,
        metric: str = "sharpe",
    ) -> Optional[uuid.UUID]:
        """P2-T13 用：从最近 N 天 completed run 中选 metric top 1。

        metric 从 run.result_json['metrics'][metric] 读取（如 sharpe/annual_return/total_return）。
        None metric 视为 -Infinity 排最后。
        """
        from models.outputs import FactBacktestRun

        valid_metrics = {"sharpe", "annual_return", "total_return", "max_drawdown", "win_rate"}
        if metric not in valid_metrics:
            raise ValueError(
                f"不支持的 metric: {metric}。可选: {sorted(valid_metrics)}"
            )

        since = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(FactBacktestRun)
            .where(
                FactBacktestRun.status == "completed",
                FactBacktestRun.started_at >= since,
            )
            .order_by(FactBacktestRun.started_at.desc())
        )
        if tenant_id is not None:
            stmt = stmt.where(FactBacktestRun.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        runs = list(result.scalars().all())

        if not runs:
            return None

        def _key(r):
            v = (r.result_json or {}).get("metrics", {}).get(metric)
            if v is None:
                # None 排最后：用最小可能值
                return float("-inf")
            return float(v)

        runs.sort(key=_key, reverse=True)
        return runs[0].job_id


def serialize_strategy_config(cfg: FactStrategyConfig) -> dict[str, Any]:
    """API 层把 FactStrategyConfig 序列化为 JSON。"""
    if cfg is None:
        return {}
    return {
        "config_id": str(cfg.config_id),
        "run_id": str(cfg.run_id) if cfg.run_id else None,
        "strategy_name": cfg.strategy_name,
        "universe_code": cfg.universe_code,
        "params_json": cfg.params_json,
        "is_active": bool(cfg.is_active),
        "selection_mode": cfg.selection_mode,
        "metrics_snapshot": cfg.metrics_snapshot,
        "note": cfg.note,
        "selected_by": cfg.selected_by,
        "tenant_id": cfg.tenant_id,
        "selected_at": cfg.selected_at.isoformat() if cfg.selected_at else None,
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
    }
