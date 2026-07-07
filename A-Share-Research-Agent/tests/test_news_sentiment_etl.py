"""test_news_sentiment_etl — P2-T5 新闻舆情 ETL 单测。

覆盖：
- score_sentiment: 正向/负向/中性词典打分
- score_dataframe: 加 sentiment_score/label/method 列
- aggregate_sentiment: 聚合 positive/negative/neutral
- NewsSentimentETL.run_news: 5 条新闻 → 5 行写入 + 分布统计
- NewsSentimentETL.run_news: provider 抛错 → 透传
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pandas as pd
import pytest

from data_pipeline.etl import news_sentiment_etl as nse_mod
from data_pipeline.etl.news_sentiment_etl import (
    NEWS_SOURCE_TAG,
    NewsSentimentETL,
)
from data_pipeline.providers.news_sentiment_provider import (
    aggregate_sentiment,
    score_dataframe,
    score_sentiment,
)
from models.facts import FactNewsItem


class TestScoreSentiment:
    def test_positive(self) -> None:
        score, label = score_sentiment("公司业绩大幅上涨，盈利超预期，强势涨停")
        assert score > 0
        assert label == "positive"

    def test_negative(self) -> None:
        score, label = score_sentiment("公司业绩巨亏，下跌停牌，面临退市风险")
        assert score < 0
        assert label == "negative"

    def test_neutral(self) -> None:
        score, label = score_sentiment("公司召开股东大会，审议年度报告")
        assert label == "neutral"

    def test_empty(self) -> None:
        score, label = score_sentiment("")
        assert score == 0.0
        assert label == "neutral"


class TestScoreDataframe:
    def test_adds_3_columns(self) -> None:
        df = pd.DataFrame({
            "title": ["公司涨停，业绩超预期", "公司巨亏，面临退市", "公司发布公告"],
            "content": ["", "", ""],
            "url": ["a", "b", "c"],
        })
        out = score_dataframe(df)
        assert "sentiment_score" in out.columns
        assert "sentiment_label" in out.columns
        assert "score_method" in out.columns
        assert (out["score_method"] == "dict_v1").all()
        assert out["sentiment_label"].iloc[0] == "positive"
        assert out["sentiment_label"].iloc[1] == "negative"
        assert out["sentiment_label"].iloc[2] == "neutral"

    def test_empty(self) -> None:
        df = pd.DataFrame(columns=["title", "content", "url"])
        out = score_dataframe(df)
        assert out.empty


class TestAggregateSentiment:
    def test_basic_aggregation(self) -> None:
        df = pd.DataFrame({
            "sentiment_score": [0.5, 0.3, -0.4, 0.0, 0.1],
            "sentiment_label": ["positive", "positive", "negative", "neutral", "neutral"],
        })
        agg = aggregate_sentiment(df)
        assert agg["total_count"] == 5
        assert agg["positive_count"] == 2
        assert agg["negative_count"] == 1
        assert agg["neutral_count"] == 2
        assert agg["avg_score"] == pytest.approx(0.1, abs=0.01)
        # 0.1 不够 0.2 阈值，label 应该是 neutral
        assert agg["label"] == "neutral"

    def test_empty(self) -> None:
        agg = aggregate_sentiment(pd.DataFrame())
        assert agg["total_count"] == 0
        assert agg["label"] == "neutral"


def _stub_nse_module(upsert_mock: AsyncMock | None = None):
    from unittest.mock import patch as _patch

    @contextmanager
    def _ctx():
        with _patch.object(nse_mod.OpsAuditService, "start_job", new=AsyncMock(return_value="job-1")):
            with _patch.object(nse_mod.OpsAuditService, "finish_job_success", new=AsyncMock()):
                with _patch.object(nse_mod.OpsAuditService, "finish_job_failed", new=AsyncMock()):
                    with _patch.object(nse_mod, "upsert_dataframe", new=upsert_mock or AsyncMock(return_value=5)):
                        yield
    return _ctx


class TestNewsSentimentETL:
    @pytest.mark.asyncio
    async def test_run_news_writes_5_rows(self) -> None:
        sec_id = uuid4()
        raw_df = pd.DataFrame({
            "news_id": ["n1", "n2", "n3", "n4", "n5"],
            "title": [
                "公司涨停，业绩超预期",
                "公司巨亏，面临退市",
                "公司中标大单，订单充足",
                "公司高管减持，市场担忧",
                "公司发布业绩公告",
            ],
            "content": [""] * 5,
            "url": ["u1", "u2", "u3", "u4", "u5"],
            "published_at": [datetime(2025, 7, 7)] * 5,
            "source_name": ["东财"] * 5,
        })
        provider = MagicMock()
        provider.get_news = AsyncMock(return_value=raw_df)

        upsert_mock = AsyncMock(return_value=5)
        with _stub_nse_module(upsert_mock=upsert_mock)():
            etl = NewsSentimentETL(provider)
            out = await etl.run_news(
                MagicMock(), business_date=date(2025, 7, 7),
                security_id=sec_id, symbol="600519",
            )
        assert out["synced_count"] == 5
        # 验证 5 行都被加 security_id + symbol
        call_args = upsert_mock.await_args
        df_arg = call_args.kwargs["df"]
        assert (df_arg["security_id"] == sec_id).all()
        assert (df_arg["symbol"] == "600519").all()
        assert call_args.kwargs["model"] is FactNewsItem
        # 分布
        assert out["score_distribution"]["total_count"] == 5
        assert out["score_distribution"]["positive_count"] >= 2
        assert out["score_distribution"]["negative_count"] >= 1

    @pytest.mark.asyncio
    async def test_run_news_empty_returns_0(self) -> None:
        sec_id = uuid4()
        provider = MagicMock()
        provider.get_news = AsyncMock(return_value=pd.DataFrame())

        with _stub_nse_module(upsert_mock=AsyncMock())():
            etl = NewsSentimentETL(provider)
            out = await etl.run_news(
                MagicMock(), business_date=date(2025, 7, 7),
                security_id=sec_id, symbol="600519",
            )
        assert out["synced_count"] == 0
        assert out["score_distribution"] == {}

    @pytest.mark.asyncio
    async def test_run_news_provider_error_propagates(self) -> None:
        sec_id = uuid4()
        provider = MagicMock()
        provider.get_news = AsyncMock(side_effect=RuntimeError("news net fail"))

        with _stub_nse_module(upsert_mock=AsyncMock())():
            etl = NewsSentimentETL(provider)
            with pytest.raises(RuntimeError, match="news net fail"):
                await etl.run_news(
                    MagicMock(), business_date=date(2025, 7, 7),
                    security_id=sec_id, symbol="600519",
                )

    @pytest.mark.asyncio
    async def test_run_news_filters_empty_urls(self) -> None:
        """url 为空的新闻被过滤掉。"""
        sec_id = uuid4()
        raw_df = pd.DataFrame({
            "news_id": ["n1", "n2", "n3"],
            "title": ["a", "b", "c"],
            "content": [""] * 3,
            "url": ["u1", "", "u3"],
            "published_at": [datetime(2025, 7, 7)] * 3,
            "source_name": ["x"] * 3,
        })
        provider = MagicMock()
        provider.get_news = AsyncMock(return_value=raw_df)

        upsert_mock = AsyncMock(return_value=2)
        with _stub_nse_module(upsert_mock=upsert_mock)():
            etl = NewsSentimentETL(provider)
            out = await etl.run_news(
                MagicMock(), business_date=date(2025, 7, 7),
                security_id=sec_id, symbol="600519",
            )
        assert out["synced_count"] == 2
        call_args = upsert_mock.await_args
        df_arg = call_args.kwargs["df"]
        assert len(df_arg) == 2
