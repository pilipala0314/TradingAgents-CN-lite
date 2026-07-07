"""SWIndustryKlineETL — 申万行业指数 K 线 ETL（P0-T4）。

复用两步模式：
- SWIndustryProvider.get_index_list() → upsert dim_security（symbol="801010", exchange="SW", security_type="industry_index"）
- SWIndustryProvider.get_daily_bars() → MarketBarNormalizer → fact_market_bar

申万行业指数代码是 6 位数字字符串（801010 / 801020 / ...）。
- 无复权（index 本身无复权概念），但保留 adjust 参数（akshare 支持 hfq / qfq / none）
- 默认 adjust="hfq"（后复权，对齐市场习惯）
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
from data_pipeline.providers.sw_industry_provider import SWIndustryProvider
from models.dimensions import DimSecurity
from models.facts import FactMarketBar

logger = logging.getLogger(__name__)


SW_SECURITY_TYPE = "industry_index"
SW_EXCHANGE = "SW"
SW_SOURCE_TAG = "akshare.stock_board_industry_hist_em"


def _build_sw_industry_security_df(index_list: pd.DataFrame) -> pd.DataFrame:
    """把 SWIndustryProvider.get_index_list() 的输出转成 dim_security 入库格式。"""
    if index_list.empty:
        return pd.DataFrame(
            columns=[
                "symbol", "raw_symbol", "security_type", "exchange",
                "name", "list_date", "delist_date", "is_active",
            ]
        )
    df = index_list.copy()
    df["symbol"] = df["code"].astype(str).str.zfill(6)
    df["raw_symbol"] = df["code"].astype(str)
    df["security_type"] = SW_SECURITY_TYPE
    df["exchange"] = SW_EXCHANGE
    df["name"] = df["name"].astype(str)
    df["list_date"] = None
    df["delist_date"] = None
    df["is_active"] = True
    return df[
        ["symbol", "raw_symbol", "security_type", "exchange", "name",
         "list_date", "delist_date", "is_active"]
    ].drop_duplicates(subset=["symbol"], keep="last")


class SWIndustryKlineETL:
    """申万行业指数 K 线 ETL 编排器。"""

    def __init__(self, provider: SWIndustryProvider | None = None) -> None:
        self.provider = provider or SWIndustryProvider()

    async def run_security_sync(
        self, session: AsyncSession, *, business_date: date,
    ) -> dict[str, Any]:
        """同步申万行业指数列表到 dim_security。"""
        idempotency_key = (
            f"collector:dim_security:sw_industry:{business_date.isoformat()}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "sw_industry_security_master_sync"},
        )
        try:
            index_list_df = await self.provider.get_index_list()
            normalized_df = _build_sw_industry_security_df(index_list_df)
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
                    "source": "akshare.stock_board_industry_name_em",
                    "security_type": SW_SECURITY_TYPE,
                },
            )
            logger.info(
                "SWIndustryKlineETL.run_security_sync synced=%d business_date=%s",
                synced_count, business_date,
            )
            return {
                "synced_count": synced_count,
                "source": "akshare.stock_board_industry_name_em",
            }
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
        adjust: str = "hfq",
    ) -> dict[str, Any]:
        """按 symbols 拉申万行业指数 K 线，写 fact_market_bar。"""
        idempotency_key = (
            f"collector:fact_market_bar:sw_industry_daily:{business_date.isoformat()}:{adjust}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "sw_industry_market_bar_sync", "symbols": symbols or "all_sw"},
        )
        try:
            rows = await load_security_rows(session, symbols=symbols)
            sw_rows = [
                (sym, sid, ex) for sym, sid, ex in rows if ex == SW_EXCHANGE
            ]
            if not sw_rows:
                logger.warning(
                    "SWIndustryKlineETL.run_daily_bars: dim_security 中无 SW 行业记录，"
                    "请先执行 run_security_sync。symbols=%s",
                    symbols or "all",
                )
                await OpsAuditService.finish_job_success(
                    session, job_id,
                    trace_payload={"synced_count": 0, "failure_count": 0, "note": "no_sw_securities"},
                )
                return {"synced_count": 0, "failure_count": 0, "failures": []}

            # 拉 K 范围：业务日前 6 个月（行业指数波动大，需要更长历史）
            start_date = business_date.replace(
                year=business_date.year - 1
            ).strftime("%Y%m%d")
            end_date = business_date.strftime("%Y%m%d")

            bar_frames: list[pd.DataFrame] = []
            failures: list[dict[str, str]] = []
            for symbol, security_id, _ in sw_rows:
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
                        source=SW_SOURCE_TAG,
                        bar_timeframe="1d",
                        adj_type=adjust,
                        version=settings.DEFAULT_FEATURE_VERSION,
                    )
                    if not normalized_df.empty:
                        bar_frames.append(normalized_df)
                except Exception as exc:
                    logger.warning(
                        "同步申万行业日线失败, symbol=%s, error=%s", symbol, exc
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
                "SWIndustryKlineETL.run_daily_bars synced=%d failures=%d business_date=%s",
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
        datalen: int = 30, adjust: str = "hfq",
    ) -> dict[str, Any]:
        security_sync = await self.run_security_sync(
            session, business_date=business_date
        )
        kline = await self.run_daily_bars(
            session, business_date=business_date,
            symbols=symbols, datalen=datalen, adjust=adjust,
        )
        return {"security_sync": security_sync, "kline": kline}
