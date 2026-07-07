"""NewsSentimentETL — 舆情新闻 ETL（P2-T5）。

设计：
- provider 抽象：注入 NewsSentimentProvider（默认），不直接调 akshare。
- 单只股票 ETL：拉新闻 → 词典打分 → upsert fact_news_item。
- 失败语义：
  - provider 抛错 → ETL 透传。
  - 数据为空 → 返回 0/0 不抛错。
- 唯一键 url 判重，可幂等重跑。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import JobType, WindowType
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.ops_audit import OpsAuditService
from data_pipeline.providers.news_sentiment_provider import (
    NewsSentimentProvider,
    score_dataframe,
)
from models.facts import FactNewsItem

logger = logging.getLogger(__name__)


NEWS_SOURCE_TAG = "akshare.stock_news_em"


class NewsSentimentETL:
    """舆情新闻 ETL 编排器。"""

    def __init__(self, provider: NewsSentimentProvider | None = None) -> None:
        self.provider = provider or NewsSentimentProvider()

    async def run_news(
        self,
        session: AsyncSession,
        *,
        business_date: date,
        security_id: Any,  # UUID
        symbol: str,
    ) -> dict[str, Any]:
        """单只股票 × 当日 → 拉新闻 → 打分 → upsert。

        Returns:
            {"synced_count": int, "score_distribution": {...}, "failures": []}
        """
        idempotency_key = (
            f"collector:fact_news_item:{symbol}:{business_date.isoformat()}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "news_sentiment_sync", "symbol": symbol},
        )

        try:
            raw_df = await self.provider.get_news(symbol=symbol)
            if raw_df.empty:
                logger.warning("NewsSentimentETL.run_news: %s 无新闻", symbol)
                await OpsAuditService.finish_job_success(
                    session, job_id,
                    trace_payload={"synced_count": 0, "note": "no_news"},
                )
                return {"synced_count": 0, "score_distribution": {}, "failures": []}

            # 1) 打分
            scored_df = score_dataframe(raw_df)

            # 2) 补 security_id + symbol + source
            scored_df = scored_df.copy()
            scored_df["security_id"] = security_id
            scored_df["symbol"] = symbol
            # url 是主键，确保非空
            scored_df = scored_df[scored_df["url"].astype(str).str.len() > 0]
            if scored_df.empty:
                await OpsAuditService.finish_job_success(
                    session, job_id,
                    trace_payload={"synced_count": 0, "note": "all_urls_empty"},
                )
                return {"synced_count": 0, "score_distribution": {}, "failures": []}

            # 3) 入库
            synced_count = await upsert_dataframe(
                session=session,
                df=scored_df,
                model=FactNewsItem,
                index_elements=["url"],
            )

            # 4) 统计
            from data_pipeline.providers.news_sentiment_provider import aggregate_sentiment
            score_dist = aggregate_sentiment(scored_df)

            await OpsAuditService.finish_job_success(
                session, job_id,
                trace_payload={
                    "synced_count": synced_count,
                    "symbol": symbol,
                    "source": NEWS_SOURCE_TAG,
                    "score_distribution": score_dist,
                },
            )
            logger.info(
                "NewsSentimentETL.run_news synced=%d symbol=%s dist=%s",
                synced_count, symbol, score_dist,
            )
            return {
                "synced_count": synced_count,
                "score_distribution": score_dist,
                "failures": [],
            }
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise
