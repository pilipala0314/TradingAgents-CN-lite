"""Facades 走 DataRouter 的集成测试 — 不打外网，用 fake provider 注入。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from data_pipeline.data_router import DataFetchError, DataRouter
from data_pipeline.providers import facade

# ── 全 method_table 覆盖的 fake provider ─────────────────────────────

# facade 实际只用 10 个端点；其余给 noop 让 method_table 注册不报警
class _FullFakeAkshare:
    """覆盖 akshare 全部 method_table 端点。"""

    def __init__(self) -> None:
        self.call_count: dict[str, int] = {}

    def _bump(self, name: str) -> None:
        self.call_count[name] = self.call_count.get(name, 0) + 1

    # facade 用到的 4 个
    async def get_tencent_quote(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        self._bump("get_tencent_quote")
        return {
            codes[0]: {
                "name": "FAKE", "price": 10.0, "last_close": 9.5, "open": 9.6,
                "change_pct": 5.0, "high": 10.5, "low": 9.4, "amount_wan": 10000.0,
                "turnover_pct": 1.5, "pe_ttm": 25.0, "pb": 3.0, "amplitude_pct": 11.0,
                "mcap_yi": 100.0, "float_mcap_yi": 80.0, "vol_ratio": 1.2,
                "limit_up": 10.45, "limit_down": 8.55, "pe_static": 23.0,
            }
        }

    async def get_kline(self, code: str, frequency: int = 9, offset: int = 60) -> list[dict[str, Any]]:
        self._bump("get_kline")
        return [
            {"date": "2026-07-01", "open": 9.5, "close": 9.8, "high": 9.9, "low": 9.4, "vol": 1e6, "amount": 1e8},
            {"date": "2026-07-02", "open": 9.8, "close": 10.0, "high": 10.5, "low": 9.7, "vol": 1.2e6, "amount": 1.2e8},
        ]

    async def get_quote_5level(self, code: str) -> dict[str, Any] | None:
        self._bump("get_quote_5level")
        return {"price": 10.0, "bid1": 9.99, "ask1": 10.01}

    async def get_stock_news(self, code: str, page_size: int = 20) -> list[dict[str, Any]]:
        self._bump("get_stock_news")
        return [{"title": "fake news", "url": "", "show_time": "2026-07-02", "media_name": "fake"}]

    # ── noop stub（不写 __init__，让 isinstance 仍可工作） ──────────
    async def get_stock_list(self): self._bump("get_stock_list"); return []
    async def get_stock_fundamentals(self, symbol): self._bump("get_stock_fundamentals"); return []
    async def get_daily_bars(self, symbol, *a, **kw): self._bump("get_daily_bars"); return []
    async def get_minute_bars(self, symbol, *a, **kw): self._bump("get_minute_bars"); return []
    async def get_market_fund_flow(self): self._bump("get_market_fund_flow"); return []
    async def get_northbound_flow(self): self._bump("get_northbound_flow"); return []
    async def get_individual_fund_flow(self, symbol, market=""): self._bump("get_individual_fund_flow"); return []
    async def get_limit_up_pool(self, date=None): self._bump("get_limit_up_pool"); return []
    async def get_limit_down_pool(self, date=None): self._bump("get_limit_down_pool"); return []
    async def get_bomb_board_pool(self, date=None): self._bump("get_bomb_board_pool"); return []
    async def get_dragon_tiger_list(self, date): self._bump("get_dragon_tiger_list"); return []
    async def get_stock_lhb(self, symbol, days=90): self._bump("get_stock_lhb"); return []
    async def get_concept_board_list(self): self._bump("get_concept_board_list"); return []
    async def get_industry_board_list(self): self._bump("get_industry_board_list"); return []
    async def get_concept_board_components(self, symbol): self._bump("get_concept_board_components"); return []
    async def get_industry_board_components(self, symbol): self._bump("get_industry_board_components"); return []
    async def get_global_macro_news(self, business_date, look_back_days=1): self._bump("get_global_macro_news"); return []
    async def get_global_news_aggregated(self, *a, **kw): self._bump("get_global_news_aggregated"); return []
    async def get_sector_event_signals(self, sector_name): self._bump("get_sector_event_signals"); return []
    async def get_insider_transactions(self, symbol): self._bump("get_insider_transactions"); return []
    async def get_balance_sheet(self, symbol): self._bump("get_balance_sheet"); return []
    async def get_cashflow_statement(self, symbol): self._bump("get_cashflow_statement"); return []
    async def get_income_statement(self, symbol): self._bump("get_income_statement"); return []
    async def get_sector_recent_bars(self, sector_name, days=30): self._bump("get_sector_recent_bars"); return []
    async def get_sector_fund_flow(self, sector_name): self._bump("get_sector_fund_flow"); return []
    async def get_sector_leaders(self, sector_name, top_n=5): self._bump("get_sector_leaders"); return []


class _FullFakeAStockData:
    """覆盖 a_stock_data 全部 method_table 端点。"""

    def __init__(self) -> None:
        self.call_count: dict[str, int] = {}

    def _bump(self, name: str) -> None:
        self.call_count[name] = self.call_count.get(name, 0) + 1

    # facade 用到的 6 个
    async def get_concept_blocks(self, code: str) -> dict[str, Any]:
        self._bump("get_concept_blocks")
        return {
            "total": 1,
            "boards": [{"name": "FAKE_INDUSTRY", "code": "IND_FAKE", "change_pct": 1.5, "lead_stock": "000001"}],
            "concept_tags": ["FAKE_INDUSTRY"],
        }

    async def get_industry_ranking(self, limit: int = 90) -> list[dict[str, Any]]:
        self._bump("get_industry_ranking")
        return [
            {"board_code": "IND_A", "board_name": "IND_A", "change_pct": 2.0, "lead_stock": "000001"},
            {"board_code": "IND_B", "board_name": "IND_B", "change_pct": -1.0, "lead_stock": "000002"},
        ]

    async def get_fund_flow_minute(self, code: str) -> list[dict[str, Any]]:
        self._bump("get_fund_flow_minute")
        return [{"time": "10:00", "main_net": 1e6, "small_net": 0, "mid_net": 0, "large_net": 0, "super_net": 0}]

    async def get_dragon_tiger(self, code: str, trade_date: str | None = None, look_back: int = 30) -> dict[str, Any]:
        self._bump("get_dragon_tiger")
        return {
            "records": [{"date": "2026-07-01", "reason": "fake", "net_buy": 100.0, "turnover": 5.0}],
            "seats": {"buy": [], "sell": []},
            "institution": {"buy_amt": 0, "sell_amt": 0, "net_amt": 0},
        }

    async def get_stock_info(self, code: str) -> dict[str, Any]:
        self._bump("get_stock_info")
        return {
            "code": code, "name": "FAKE_NAME", "industry": "FAKE_INDUSTRY",
            "total_shares": 1e9, "float_shares": 8e8, "mcap": 1e10, "float_mcap": 8e9,
            "list_date": "20200101", "price": 10.0,
        }

    async def get_finance_snapshot(self, code: str) -> dict[str, Any] | None:
        self._bump("get_finance_snapshot")
        return {"eps": 0.5, "roe": 0.15, "bvps": 5.0, "profit": 1e9, "income": 5e9}

    async def get_tencent_quote(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        self._bump("get_tencent_quote")
        return {
            codes[0]: {
                "name": "FAKE", "price": 10.0, "last_close": 9.5, "open": 9.6,
                "change_pct": 5.0, "high": 10.5, "low": 9.4, "amount_wan": 10000.0,
                "turnover_pct": 1.5, "pe_ttm": 25.0, "pb": 3.0, "amplitude_pct": 11.0,
                "mcap_yi": 100.0, "float_mcap_yi": 80.0, "vol_ratio": 1.2,
                "limit_up": 10.45, "limit_down": 8.55, "pe_static": 23.0,
            }
        }

    async def get_quote_5level(self, code: str) -> dict[str, Any] | None:
        self._bump("get_quote_5level")
        return {"price": 10.0, "bid1": 9.99, "ask1": 10.01}

    async def get_kline(self, code: str, frequency: int = 9, offset: int = 60) -> list[dict[str, Any]]:
        self._bump("get_kline")
        return [
            {"date": "2026-07-01", "open": 9.5, "close": 9.8, "high": 9.9, "low": 9.4, "vol": 1e6, "amount": 1e8},
            {"date": "2026-07-02", "open": 9.8, "close": 10.0, "high": 10.5, "low": 9.7, "vol": 1.2e6, "amount": 1.2e8},
        ]

    async def get_stock_news(self, code: str, page_size: int = 20) -> list[dict[str, Any]]:
        self._bump("get_stock_news")
        return [{"title": "fake news", "url": "", "show_time": "2026-07-02", "media_name": "fake"}]


@pytest.fixture
def fake_router() -> DataRouter:
    """注入 fake providers 的 router。"""
    router = DataRouter(providers={
        "akshare": _FullFakeAkshare(),
        "a_stock_data": _FullFakeAStockData(),
    })
    facade.set_router(router)
    return router


# ── 端到端：facade 走 router ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_facade_uses_router(fake_router: DataRouter) -> None:
    """stock_live_context 走 DataRouter，10 端点全 ok，snapshot 合成成功。"""
    result = await facade.stock_live_context("000001", business_date="2026-07-02")

    assert "stock_snapshot" in result
    assert "market_regime" in result
    assert "sector_stage" in result
    assert "source_meta" in result

    snapshot = result["stock_snapshot"]
    assert snapshot["symbol"] == "000001"
    assert snapshot["current_price"] == 10.0
    assert snapshot["name"] == "FAKE_NAME"
    assert snapshot["industry"] == "FAKE_INDUSTRY"

    # 10 端点全 ok
    for key, status in result["source_meta"].items():
        assert status == "ok", f"端点 {key} 状态: {status}"


@pytest.mark.asyncio
async def test_facade_cache_reuses_router_results(fake_router: DataRouter) -> None:
    """同一 symbol 30s 内二次调用，provider 端点不重复打（cache 命中）。"""
    r1 = await facade.stock_live_context("000001", business_date="2026-07-02")
    r2 = await facade.stock_live_context("000001", business_date="2026-07-02")
    # 二次调用应返回等价结构（cache 命中后字段一致）
    assert r1["stock_snapshot"]["symbol"] == r2["stock_snapshot"]["symbol"]

    fake_ak = fake_router.providers["akshare"]
    fake_asd = fake_router.providers["a_stock_data"]

    # 缓存命中：每个 provider 端点只调用 1 次（facade 走的 10 个端点）
    # 1 个在 akshare 上调用 (get_stock_news)
    assert fake_ak.call_count.get("get_stock_news") == 1
    # 9 个在 a_stock_data 上调用
    assert fake_asd.call_count.get("get_tencent_quote") == 1
    assert fake_asd.call_count.get("get_kline") == 1
    assert fake_asd.call_count.get("get_quote_5level") == 1
    assert fake_asd.call_count.get("get_concept_blocks") == 1
    assert fake_asd.call_count.get("get_industry_ranking") == 1
    assert fake_asd.call_count.get("get_fund_flow_minute") == 1
    assert fake_asd.call_count.get("get_dragon_tiger") == 1
    assert fake_asd.call_count.get("get_stock_info") == 1
    assert fake_asd.call_count.get("get_finance_snapshot") == 1


@pytest.mark.asyncio
async def test_facade_strict_mode_raises_on_first_failure() -> None:
    """strict=True + 端点失败 → facade 抛错（DataFetchError from router）。"""

    class _AlwaysFailAsd(_FullFakeAStockData):
        async def get_tencent_quote(self, codes: list[str]) -> dict:
            raise RuntimeError("forced failure for strict test")

    router = DataRouter(
        providers={"akshare": _FullFakeAkshare(), "a_stock_data": _AlwaysFailAsd()},
        default_strict=True,
    )
    facade.set_router(router)

    with pytest.raises(DataFetchError):  # router 端点全失败 → DataFetchError 上抛
        await facade.stock_live_context("000001", business_date="2026-07-02", strict=True)


@pytest.mark.asyncio
async def test_facade_non_strict_continues_on_failure() -> None:
    """非 strict 模式：部分端点失败不影响整体返回（source_meta 标记 failed）。"""

    class _PartialFailAsd(_FullFakeAStockData):
        # a_stock_data 独有的方法 — 直接让它失败（akshare 端点没该方法）
        async def get_kline(self, code: str, frequency: int = 9, offset: int = 60):
            raise RuntimeError("get_kline forced failure")

        async def get_concept_blocks(self, code: str) -> dict:
            raise RuntimeError("get_concept_blocks forced failure")

    router = DataRouter(
        providers={"akshare": _FullFakeAkshare(), "a_stock_data": _PartialFailAsd()},
        default_strict=False,
    )
    facade.set_router(router)

    result = await facade.stock_live_context("000001", business_date="2026-07-02", strict=False)
    # 不抛错，返回结果
    assert "stock_snapshot" in result
    # 失败端点标记 failed（新版 source_meta key 是 method 名）
    assert result["source_meta"]["get_kline"] == "failed"
    assert result["source_meta"]["get_concept_blocks"] == "failed"
    # 成功的端点 ok
    assert result["source_meta"]["get_tencent_quote"] == "ok"
    assert result["source_meta"]["get_stock_news"] == "ok"


@pytest.mark.asyncio
async def test_facade_router_health_visible(fake_router: DataRouter) -> None:
    """调用 facade 后 router.health() 反映成功统计。"""
    await facade.stock_live_context("000001", business_date="2026-07-02")
    h = fake_router.health()
    assert h["calls_total"] >= 10  # 至少 10 端点
    # 所有 facade 走的端点都在
    for method in [
        "get_tencent_quote", "get_kline", "get_quote_5level", "get_concept_blocks",
        "get_industry_ranking", "get_fund_flow_minute", "get_dragon_tiger",
        "get_stock_info", "get_finance_snapshot", "get_stock_news",
    ]:
        stats = [e for e in h["endpoints"] if e["method"] == method]
        assert len(stats) > 0
        assert stats[0]["total"] >= 1
