"""BSEKlineETL — 北交所 K 线 ETL（P1-T11）。

设计：
- 两步组合：
  1) `run_security_sync`  拉北交所全量列表 → upsert dim_security（symbol="8xxxxxx" / "4xxxxxx", exchange="BJ", security_type="bse_stock"）
  2) `run_daily_bars`     按 dim_security.security_id 拉 K 线 → MarketBarNormalizer → fact_market_bar
- provider 抽象：注入 BSEKlineProvider（默认）或 _FakeBSEProvider（测试）。
- 失败语义：与港股 ETL 一致 — provider 抛错 → ETL 透传；单只失败记录到 failures 继续。
- 数据源标注：`source = "akshare.stock_bj_hist"`，便于追溯。
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
from data_pipeline.providers.bse_kline_provider import BSEKlineProvider
from models.dimensions import DimSecurity
from models.facts import FactMarketBar

logger = logging.getLogger(__name__)


# 北交所专用 dim_security 字段
BSE_SECURITY_TYPE = "bse_stock"
BSE_EXCHANGE = "BJ"
BSE_SOURCE_TAG = "akshare.stock_bj_hist"


def _build_bse_security_df(stock_list: pd.DataFrame) -> pd.DataFrame:
    """BSEKlineProvider.get_stock_list() → dim_security 入库格式。"""
    if stock_list.empty:
        return pd.DataFrame(
            columns=[
                "symbol", "raw_symbol", "security_type", "exchange",
                "name", "list_date", "delist_date", "is_active",
            ]
        )
    df = stock_list.copy()
    df["symbol"] = df["code"].astype(str).str.zfill(8)
    df["raw_symbol"] = df["code"].astype(str)
    df["security_type"] = BSE_SECURITY_TYPE
    df["exchange"] = df.get("exchange", BSE_EXCHANGE)
    df["name"] = df["name"].astype(str)
    df["list_date"] = None
    df["delist_date"] = None
    df["is_active"] = True
    return df[
        [
            "symbol", "raw_symbol", "security_type", "exchange",
            "name", "list_date", "delist_date", "is_active",
        ]
    ].drop_duplicates(subset=["symbol"], keep="last")


class BSEKlineETL:
    """北交所 K 线 ETL 编排器。"""

    def __init__(self, provider: BSEKlineProvider | None = None) -> None:
        self.provider = provider or BSEKlineProvider()

    async def run_security_sync(
        self,
        session: AsyncSession,
        *,
        business_date: date,
    ) -> dict[str, Any]:
        """同步北交所主数据到 dim_security。"""
        idempotency_key = (
            f"collector:dim_security:bse:{business_date.isoformat()}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "bse_security_master_sync"},
        )
        try:
            stock_list_df = await self.provider.get_stock_list()
            normalized_df = _build_bse_security_df(stock_list_df)
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
                    "source": "akshare.stock_zh_a_spot_em(8/4 prefix)",
                    "security_type": BSE_SECURITY_TYPE,
                },
            )
            logger.info(
                "BSEKlineETL.run_security_sync synced=%d business_date=%s",
                synced_count, business_date,
            )
            return {"synced_count": synced_count, "source": "akshare.stock_zh_a_spot_em"}
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
        """按 symbols 拉北交所 K 线，写 fact_market_bar。"""
        idempotency_key = (
            f"collector:fact_market_bar:bse_daily:{business_date.isoformat()}:{adjust}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "bse_market_bar_sync", "symbols": symbols or "all_bse"},
        )
        try:
            rows = await load_security_rows(session, symbols=symbols)
            bse_rows = [
                (sym, sid, ex) for sym, sid, ex in rows if ex == BSE_EXCHANGE
            ]
            if not bse_rows:
                logger.warning(
                    "BSEKlineETL.run_daily_bars: dim_security 中无 BJ 股记录。"
                )
                await OpsAuditService.finish_job_success(
                    session, job_id,
                    trace_payload={"synced_count": 0, "failure_count": 0, "note": "no_bse_securities"},
                )
                return {"synced_count": 0, "failure_count": 0, "failures": []}

            start_date = (business_date - pd.Timedelta(days=datalen * 2)).strftime("%Y%m%d")
            end_date = business_date.strftime("%Y%m%d")

            bar_frames: list[pd.DataFrame] = []
            failures: list[dict[str, str]] = []
            for symbol, security_id, _ in bse_rows:
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
                        source=BSE_SOURCE_TAG,
                        bar_timeframe="1d",
                        adj_type=adjust,
                        version=settings.DEFAULT_FEATURE_VERSION,
                    )
                    if not normalized_df.empty:
                        bar_frames.append(normalized_df)
                except Exception as exc:
                    logger.warning(
                        "同步北交所日线失败, symbol=%s, error=%s", symbol, exc
                    )
                    failures.append({"symbol": symbol, "error": str(exc)})

            merged_df = (
                pd.concat(bar_frames, ignore_index=True) if bar_frames else pd.DataFrame()
            )
            if not merged_df.empty:
                await upsert_dataframe(
                    session=session,
                    df=merged_df,
                    model=FactMarketBar,
                    index_elements=[
                        "security_id", "bar_date", "bar_timeframe", "adj_type", "version",
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
                "BSEKlineETL.run_daily_bars synced=%d failures=%d business_date=%s",
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
        """全链路：先 sync 主数据 → 再拉 K。"""
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
