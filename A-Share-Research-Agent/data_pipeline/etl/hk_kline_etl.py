"""HKKlineETL — 港股 K 线 ETL（P0-T1）。

设计：
- 两步组合：
  1) `run_security_sync`  拉港股全量列表 → upsert dim_security（symbol="00700", exchange="HK", security_type="hk_stock"）
  2) `run_daily_bars`     按 dim_security.security_id 拉 K 线 → MarketBarNormalizer → fact_market_bar
- provider 抽象：注入 HKFullProvider（默认）或 _FakeHKProvider（测试），不直接调 akshare。
- 失败语义：
  - provider 抛错 → ETL 透传，绝不静默吞错。
  - 单只股票失败 → 记录到 failures 列表，继续处理其他 symbol。
- 数据源标注：`source = "akshare.stock_hk_hist"`，便于追溯。

执行入口：
- `run_full(session, business_date, symbols=None, datalen=30)` — 串行两步
- `apps/worker/tasks.py::run_hk_close_etl` — Celery 入口（用于 17:00 cron）
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import JobType, WindowType
from data_pipeline.etl._common import load_security_rows
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.normalizers import MarketBarNormalizer
from data_pipeline.ops_audit import OpsAuditService
from data_pipeline.providers.hk_full_provider import HKFullProvider
from models.dimensions import DimSecurity
from models.facts import FactMarketBar

logger = logging.getLogger(__name__)


# 港股专用 dim_security 字段
HK_SECURITY_TYPE = "hk_stock"
HK_EXCHANGE = "HK"
HK_SOURCE_TAG = "akshare.stock_hk_hist"


def _build_hk_security_df(stock_list: pd.DataFrame) -> pd.DataFrame:
    """把 HKFullProvider.get_stock_list() 的输出转成 dim_security 入库格式。

    输入列：code, name, exchange（HKFullProvider 已规范）
    输出列：symbol, raw_symbol, security_type, exchange, name, is_active
    """
    if stock_list.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "raw_symbol",
                "security_type",
                "exchange",
                "name",
                "list_date",
                "delist_date",
                "is_active",
            ]
        )
    df = stock_list.copy()
    df["symbol"] = df["code"].astype(str).str.zfill(5)
    df["raw_symbol"] = df["code"].astype(str)
    df["security_type"] = HK_SECURITY_TYPE
    df["exchange"] = df.get("exchange", HK_EXCHANGE)
    df["name"] = df["name"].astype(str)
    df["list_date"] = None
    df["delist_date"] = None
    df["is_active"] = True
    return df[
        [
            "symbol",
            "raw_symbol",
            "security_type",
            "exchange",
            "name",
            "list_date",
            "delist_date",
            "is_active",
        ]
    ].drop_duplicates(subset=["symbol"], keep="last")


class HKKlineETL:
    """港股 K 线 ETL 编排器。

    Attributes:
        provider: HKFullProvider 实例（生产用 AkShareClient 注入；测试可注入 _FakeHKProvider）
    """

    def __init__(self, provider: HKFullProvider | None = None) -> None:
        self.provider = provider or HKFullProvider()

    async def run_security_sync(
        self,
        session: AsyncSession,
        *,
        business_date: date,
    ) -> dict[str, Any]:
        """同步港股主数据到 dim_security。

        Returns:
            {"synced_count": int, "source": "akshare.stock_hk_spot_em"}
        """
        idempotency_key = (
            f"collector:dim_security:hk:{business_date.isoformat()}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "hk_security_master_sync"},
        )

        try:
            stock_list_df = await self.provider.get_stock_list()
            normalized_df = _build_hk_security_df(stock_list_df)
            synced_count = len(normalized_df)
            if synced_count > 0:
                await upsert_dataframe(
                    session=session,
                    df=normalized_df,
                    model=DimSecurity,
                    index_elements=["symbol"],
                )
            await OpsAuditService.finish_job_success(
                session,
                job_id,
                trace_payload={
                    "synced_count": synced_count,
                    "source": "akshare.stock_hk_spot_em",
                    "security_type": HK_SECURITY_TYPE,
                },
            )
            logger.info(
                "HKKlineETL.run_security_sync synced=%d business_date=%s",
                synced_count, business_date,
            )
            return {"synced_count": synced_count, "source": "akshare.stock_hk_spot_em"}
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise

    async def run_daily_bars(
        self,
        session: AsyncSession,
        *,
        business_date: date,
        symbols: list[str] | None = None,
        datalen: int = 30,
        adjust: str = "qfq",
    ) -> dict[str, Any]:
        """按 symbols 列表拉港股 K 线，写 fact_market_bar。

        Args:
            session:       AsyncSession
            business_date: 业务日（用于 ops_audit）
            symbols:       港股代码列表（5 位），None=拉 dim_security 里所有 HK 股
            datalen:       akshare.start_date 回溯窗口（默认 30 个交易日）
            adjust:        qfq/hfq/none

        Returns:
            {"synced_count": int, "failure_count": int, "failures": [...]}
        """
        idempotency_key = (
            f"collector:fact_market_bar:hk_daily:{business_date.isoformat()}:{adjust}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "hk_market_bar_sync", "symbols": symbols or "all_hk"},
        )

        try:
            # 1) 拉 dim_security 映射 (symbol, security_id, exchange)
            rows = await load_security_rows(
                session, symbols=symbols
            )
            hk_rows = [
                (sym, sid, ex)
                for sym, sid, ex in rows
                if ex == HK_EXCHANGE
            ]
            if not hk_rows:
                logger.warning(
                    "HKKlineETL.run_daily_bars: dim_security 中无 HK 股记录，"
                    "请先执行 run_security_sync。symbols=%s",
                    symbols or "all",
                )
                await OpsAuditService.finish_job_success(
                    session, job_id,
                    trace_payload={"synced_count": 0, "failure_count": 0, "note": "no_hk_securities"},
                )
                return {"synced_count": 0, "failure_count": 0, "failures": []}

            start_date = business_date.strftime("%Y%m%d")
            end_date = business_date.strftime("%Y%m%d")

            # 2) 逐 symbol 拉 K
            bar_frames: list[pd.DataFrame] = []
            failures: list[dict[str, str]] = []
            for symbol, security_id, _ in hk_rows:
                try:
                    raw_df = await self.provider.get_daily_bars(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust,
                    )
                    normalized_df = MarketBarNormalizer.from_history(
                        raw_df=raw_df,
                        security_id=security_id,
                        source=HK_SOURCE_TAG,
                        bar_timeframe="1d",
                        adj_type=adjust,
                        version=settings.DEFAULT_FEATURE_VERSION,
                    )
                    if not normalized_df.empty:
                        bar_frames.append(normalized_df)
                except Exception as exc:
                    logger.warning(
                        "同步港股日线失败, symbol=%s, error=%s", symbol, exc
                    )
                    failures.append({"symbol": symbol, "error": str(exc)})

            # 3) 合并 + upsert（库里的 unique 约束已带 (security_id, bar_date, timeframe, adj, version)）
            merged_df = (
                pd.concat(bar_frames, ignore_index=True) if bar_frames else pd.DataFrame()
            )
            if not merged_df.empty:
                await upsert_dataframe(
                    session=session,
                    df=merged_df,
                    model=FactMarketBar,
                    index_elements=[
                        "security_id",
                        "bar_date",
                        "bar_timeframe",
                        "adj_type",
                        "version",
                    ],
                )

            synced_count = len(merged_df)
            await OpsAuditService.finish_job_success(
                session,
                job_id,
                trace_payload={
                    "synced_count": synced_count,
                    "failure_count": len(failures),
                    "failures": failures[:20],
                },
            )
            logger.info(
                "HKKlineETL.run_daily_bars synced=%d failures=%d business_date=%s",
                synced_count, len(failures), business_date,
            )
            return {
                "synced_count": synced_count,
                "failure_count": len(failures),
                "failures": failures,
            }
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise

    async def run_full(
        self,
        session: AsyncSession,
        *,
        business_date: date,
        symbols: list[str] | None = None,
        datalen: int = 30,
        adjust: str = "qfq",
    ) -> dict[str, Any]:
        """全链路：先 sync 主数据 → 再拉 K。

        适用于 ETL 入口；分步调用用于 admin / debug 场景。
        """
        security_sync = await self.run_security_sync(
            session, business_date=business_date
        )
        kline = await self.run_daily_bars(
            session,
            business_date=business_date,
            symbols=symbols,
            datalen=datalen,
            adjust=adjust,
        )
        return {"security_sync": security_sync, "kline": kline}
