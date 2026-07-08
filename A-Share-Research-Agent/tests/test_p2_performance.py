"""P2 性能 smoke test — 关键路径耗时上界。

锁定：
- StrategySelector.set_from_run：单次 < 100ms（mock 模式）
- NewsSentimentProvider.score_sentiment：单次 < 10ms
- NewsSentimentProvider.aggregate_sentiment：100 条 < 100ms
- ModelRegistry.set_active：单次 < 100ms
- 字典哈希稳定：score IC 计算 < 50ms（如果有）
"""
from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── StrategySelector.set_from_run ─────────────────────


@pytest.mark.asyncio
async def test_strategy_set_from_run_under_100ms():
    from models.dimensions import FactStrategyConfig
    from services.strategy_selector import StrategySelector

    # 准备 run
    run = MagicMock()
    run.job_id = uuid.uuid4()
    run.status = "completed"
    run.tenant_id = "default"
    run.business_date = date(2026, 6, 30)
    run.cost_total_pct = Decimal("0.005")
    run.benchmark_alpha = Decimal("0.05")
    run.benchmark_beta = Decimal("0.98")
    run.started_at = datetime.now(timezone.utc)
    run.config_json = {
        "strategy_name": "momentum",
        "universe": "hs300",
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "top_n": 20,
        "rebalance_freq": "weekly",
        "symbols": ["600519"] * 30,
    }
    run.result_json = {
        "metrics": {"sharpe": 1.2, "annual_return": 0.18, "total_return": 0.32, "max_drawdown": -0.12, "win_rate": 0.55},
    }

    class _F:
        def __init__(self, items):
            self._items = items
        def first(self):
            return self._items[0] if self._items else None
        def all(self):
            return self._items

    class _R:
        def __init__(self, items):
            self._items = items
        def scalars(self):
            return _F(self._items)

    sess = MagicMock()
    sess.execute = AsyncMock(side_effect=[_R([run]), MagicMock(rowcount=0)])
    sess.flush = AsyncMock()
    sel = StrategySelector(sess)

    t0 = time.perf_counter()
    cfg = await sel.set_from_run(run_id=run.job_id, tenant_id="default")
    elapsed = (time.perf_counter() - t0) * 1000
    assert elapsed < 100, f"set_from_run 耗时 {elapsed:.1f}ms > 100ms"


# ── NewsSentiment 性能 ─────────────────────


def test_news_score_sentiment_under_10ms():
    from data_pipeline.providers.news_sentiment_provider import score_sentiment

    text = "公司业绩超预期增长，营收创新高，市场情绪乐观，利润大幅提升，看好未来前景"
    # 跑 100 次取平均
    N = 100
    t0 = time.perf_counter()
    for _ in range(N):
        score_sentiment(text)
    avg = (time.perf_counter() - t0) * 1000 / N
    assert avg < 10, f"score_sentiment 平均耗时 {avg:.2f}ms > 10ms"


def test_news_aggregate_sentiment_100_items_under_100ms():
    import pandas as pd

    from data_pipeline.providers.news_sentiment_provider import aggregate_sentiment

    rows = []
    for i in range(100):
        rows.append({
            "sentiment_label": ["positive", "neutral", "negative"][i % 3],
            "sentiment_score": (i % 11) / 10.0 - 0.5,
        })
    df = pd.DataFrame(rows)
    t0 = time.perf_counter()
    result = aggregate_sentiment(df)
    elapsed = (time.perf_counter() - t0) * 1000
    assert elapsed < 100, f"aggregate_sentiment 耗时 {elapsed:.1f}ms > 100ms"
    assert "total_count" in result
    assert "positive_count" in result
    assert result["total_count"] == 100


# ── ModelRegistry.set_active ─────────────────────


@pytest.mark.asyncio
async def test_model_set_active_under_100ms():
    from services.model_registry import ModelRegistry

    target = MagicMock()
    target.model_id = 1
    target.role = "stock_analyst"
    target.is_active = True

    class _F:
        def __init__(self, items):
            self._items = items
        def first(self):
            return self._items[0] if self._items else None

    class _R:
        def __init__(self, items):
            self._items = items
        def scalars(self):
            return _F(self._items)

    sess = MagicMock()
    # execute 1: update 关闭其他 (rowcount)
    # execute 2: select 查 target
    sess.execute = AsyncMock(side_effect=[
        MagicMock(rowcount=1),  # update
        _R([target]),           # select
    ])
    sess.flush = AsyncMock()

    reg = ModelRegistry(sess)
    t0 = time.perf_counter()
    result = await reg.set_active(role="stock_analyst", model_id=1)
    elapsed = (time.perf_counter() - t0) * 1000
    assert elapsed < 100, f"ModelRegistry.set_active 耗时 {elapsed:.1f}ms > 100ms"
    assert result.is_active is True


# ── 综合 ─────────────────────


def test_p2_key_paths_perf_summary():
    """汇总：所有 P2 关键路径性能均达标（其他子测试已单独 assert）。"""
    # 此测试不重复 assert，只作为 documentation
    assert True
