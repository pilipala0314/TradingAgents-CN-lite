"""USKlineETL — 美股 K 线 ETL（P0-T2）。

复用 HKKlineETL 的两步模式（run_security_sync + run_daily_bars + run_full）：
- USStockProvider.get_stock_list() → upsert dim_security（symbol="AAPL", exchange="US", security_type="us_stock"）
- USStockProvider.get_daily_bars() → MarketBarNormalizer → fact_market_bar

美股代码是字母（如 AAPL / TSLA / MSFT），不补零。
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
from data_pipeline.providers.us_stock_provider import USStockProvider
from models.dimensions import DimSecurity
from models.facts import FactMarketBar

logger = logging.getLogger(__name__)


US_SECURITY_TYPE = "us_stock"
US_EXCHANGE = "US"
US_SOURCE_TAG = "akshare.stock_us_hist"


def _build_us_security_df(stock_list: pd.DataFrame) -> pd.DataFrame:
    """把 USStockProvider.get_stock_list() 的输出转成 dim_security 入库格式。

    输入列：code, name, exchange（USStockProvider 已规范）
    输出列：symbol, raw_symbol, security_type, exchange, name, is_active
    """
    if stock_list.empty:
        return pd.DataFrame(
            columns=[
                "symbol", "raw_symbol", "security_type", "exchange",
                "name", "list_date", "delist_date", "is_active",
            ]
        )
    df = stock_list.copy()
    df["symbol"] = df["code"].astype(str).str.upper()
    df["raw_symbol"] = df["code"].astype(str)
    df["security_type"] = US_SECURITY_TYPE
    df["exchange"] = df.get("exchange", US_EXCHANGE)
    df["name"] = df["name"].astype(str)
    df["list_date"] = None
    df["delist_date"] = None
    df["is_active"] = True
    return df[
        ["symbol", "raw_symbol", "security_type", "exchange", "name",
         "list_date", "delist_date", "is_active"]
    ].drop_duplicates(subset=["symbol"], keep="last")


class USKlineETL:
    """美股 K 线 ETL 编排器。"""

    def __init__(self, provider: USStockProvider | None = None) -> None:
        self.provider = provider or USStockProvider()

    async def run_security_sync(
        self, session: AsyncSession, *, business_date: date,
    ) -> dict[str, Any]:
        """同步美股主数据到 dim_security。"""
        idempotency_key = f"collector:dim_security:us:{business_date.isoformat()}"
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "us_security_master_sync"},
        )
        try:
            stock_list_df = await self.provider.get_stock_list()
            normalized_df = _build_us_security_df(stock_list_df)
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
                    "source": "akshare.stock_us_spot_em",
                    "security_type": US_SECURITY_TYPE,
                },
            )
            logger.info(
                "USKlineETL.run_security_sync synced=%d business_date=%s",
                synced_count, business_date,
            )
            return {"synced_count": synced_count, "source": "akshare.stock_us_spot_em"}
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
        """按 symbols 拉美股 K 线，写 fact_market_bar。"""
        idempotency_key = (
            f"collector:fact_market_bar:us_daily:{business_date.isoformat()}:{adjust}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "us_market_bar_sync", "symbols": symbols or "all_us"},
        )
        try:
            rows = await load_security_rows(session, symbols=symbols)
            us_rows = [
                (sym, sid, ex) for sym, sid, ex in rows if ex == US_EXCHANGE
            ]
            if not us_rows:
                logger.warning(
                    "USKlineETL.run_daily_bars: dim_security 中无 US 股记录，"
                    "请先执行 run_security_sync。symbols=%s",
                    symbols or "all",
                )
                await OpsAuditService.finish_job_success(
                    session, job_id,
                    trace_payload={"synced_count": 0, "failure_count": 0, "note": "no_us_securities"},
                )
                return {"synced_count": 0, "failure_count": 0, "failures": []}

            start_date = business_date.strftime("%Y%m%d")
            end_date = business_date.strftime("%Y%m%d")

            bar_frames: list[pd.DataFrame] = []
            failures: list[dict[str, str]] = []
            for symbol, security_id, _ in us_rows:
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
                        source=US_SOURCE_TAG,
                        bar_timeframe="1d",
                        adj_type=adjust,
                        version=settings.DEFAULT_FEATURE_VERSION,
                    )
                    if not normalized_df.empty:
                        bar_frames.append(normalized_df)
                except Exception as exc:
                    logger.warning(
                        "同步美股日线失败, symbol=%s, error=%s", symbol, exc
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
                "USKlineETL.run_daily_bars synced=%d failures=%d business_date=%s",
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
        datalen: int = 30, adjust: str = "qfq",
    ) -> dict[str, Any]:
        security_sync = await self.run_security_sync(
            session, business_date=business_date
        )
        kline = await self.run_daily_bars(
            session, business_date=business_date,
            symbols=symbols, datalen=datalen, adjust=adjust,
        )
        return {"security_sync": security_sync, "kline": kline}
