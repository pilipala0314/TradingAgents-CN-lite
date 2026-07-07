"""FuturesKlineETL — 国内期货主力合约 K 线 ETL（P0-T2）。

复用两步模式：
- FuturesProvider.get_main_contracts_sina() → upsert dim_security（symbol="RB2501", exchange="FUT", security_type="futures"）
- FuturesProvider.get_daily_bars_sina() → MarketBarNormalizer → fact_market_bar

主力合约代码通常是字母+数字（如 RB2501 / CU2501 / AU2502）。
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
from data_pipeline.providers.futures_provider import FuturesProvider
from models.dimensions import DimSecurity
from models.facts import FactMarketBar

logger = logging.getLogger(__name__)


FUTURES_SECURITY_TYPE = "futures"
FUTURES_EXCHANGE = "FUT"
FUTURES_SOURCE_TAG = "akshare.futures_main_sina_hist"


def _build_futures_security_df(contract_list: pd.DataFrame) -> pd.DataFrame:
    """把 FuturesProvider.get_main_contracts_sina() 的输出转成 dim_security 入库格式。"""
    if contract_list.empty:
        return pd.DataFrame(
            columns=[
                "symbol", "raw_symbol", "security_type", "exchange",
                "name", "list_date", "delist_date", "is_active",
            ]
        )
    df = contract_list.copy()
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["raw_symbol"] = df["symbol"].astype(str)
    df["security_type"] = FUTURES_SECURITY_TYPE
    df["exchange"] = FUTURES_EXCHANGE
    df["name"] = df["name"].astype(str)
    df["list_date"] = None
    df["delist_date"] = None
    df["is_active"] = True
    return df[
        ["symbol", "raw_symbol", "security_type", "exchange", "name",
         "list_date", "delist_date", "is_active"]
    ].drop_duplicates(subset=["symbol"], keep="last")


class FuturesKlineETL:
    """期货主力合约 K 线 ETL 编排器。"""

    def __init__(self, provider: FuturesProvider | None = None) -> None:
        self.provider = provider or FuturesProvider()

    async def run_security_sync(
        self, session: AsyncSession, *, business_date: date,
    ) -> dict[str, Any]:
        """同步主力合约列表到 dim_security。"""
        idempotency_key = f"collector:dim_security:futures:{business_date.isoformat()}"
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "futures_security_master_sync"},
        )
        try:
            contract_df = await self.provider.get_main_contracts_sina()
            normalized_df = _build_futures_security_df(contract_df)
            synced_count = len(normalized_df)
            if synced_count > 0:
                await upsert_dataframe(
                    session=session, df=normalized_df,
                    model=DimSecurity, index_elements=["symbol"],
                )
            await OpsAuditService.finish_job_success(
                session, job_id,
                trace_payload={
                    "synced_count": synced_count,
                    "source": "akshare.futures_main_sina",
                    "security_type": FUTURES_SECURITY_TYPE,
                },
            )
            logger.info(
                "FuturesKlineETL.run_security_sync synced=%d business_date=%s",
                synced_count, business_date,
            )
            return {"synced_count": synced_count, "source": "akshare.futures_main_sina"}
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
    ) -> dict[str, Any]:
        """按 symbols 拉主力合约 K 线，写 fact_market_bar。

        注：期货没有复权（adjust 永远是 "none"）。
        """
        idempotency_key = (
            f"collector:fact_market_bar:futures_daily:{business_date.isoformat()}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "futures_market_bar_sync", "symbols": symbols or "all_futures"},
        )
        try:
            rows = await load_security_rows(session, symbols=symbols)
            fut_rows = [
                (sym, sid, ex) for sym, sid, ex in rows if ex == FUTURES_EXCHANGE
            ]
            if not fut_rows:
                logger.warning(
                    "FuturesKlineETL.run_daily_bars: dim_security 中无期货记录，"
                    "请先执行 run_security_sync。symbols=%s",
                    symbols or "all",
                )
                await OpsAuditService.finish_job_success(
                    session, job_id,
                    trace_payload={"synced_count": 0, "failure_count": 0, "note": "no_futures_securities"},
                )
                return {"synced_count": 0, "failure_count": 0, "failures": []}

            start_date = business_date.strftime("%Y%m%d")
            end_date = business_date.strftime("%Y%m%d")
            adjust = "none"  # 期货无复权

            bar_frames: list[pd.DataFrame] = []
            failures: list[dict[str, str]] = []
            for symbol, security_id, _ in fut_rows:
                try:
                    raw_df = await self.provider.get_daily_bars_sina(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                    )
                    normalized_df = MarketBarNormalizer.from_history(
                        raw_df=raw_df,
                        security_id=security_id,
                        source=FUTURES_SOURCE_TAG,
                        bar_timeframe="1d",
                        adj_type=adjust,
                        version=settings.DEFAULT_FEATURE_VERSION,
                    )
                    if not normalized_df.empty:
                        bar_frames.append(normalized_df)
                except Exception as exc:
                    logger.warning(
                        "同步期货日线失败, symbol=%s, error=%s", symbol, exc
                    )
                    failures.append({"symbol": symbol, "error": str(exc)})

            merged_df = (
                pd.concat(bar_frames, ignore_index=True) if bar_frames else pd.DataFrame()
            )
            if not merged_df.empty:
                await upsert_dataframe(
                    session=session, df=merged_df,
                    model=FactMarketBar,
                    index_elements=[
                        "security_id", "bar_date", "bar_timeframe", "adj_type", "version",
                    ],
                )

            synced_count = len(merged_df)
            await OpsAuditService.finish_job_success(
                session, job_id,
                trace_payload={
                    "synced_count": synced_count,
                    "failure_count": len(failures),
                    "failures": failures[:20],
                },
            )
            logger.info(
                "FuturesKlineETL.run_daily_bars synced=%d failures=%d business_date=%s",
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
        self, session: AsyncSession, *,
        business_date: date, symbols: list[str] | None = None,
        datalen: int = 30,
    ) -> dict[str, Any]:
        security_sync = await self.run_security_sync(
            session, business_date=business_date
        )
        kline = await self.run_daily_bars(
            session, business_date=business_date,
            symbols=symbols, datalen=datalen,
        )
        return {"security_sync": security_sync, "kline": kline}
