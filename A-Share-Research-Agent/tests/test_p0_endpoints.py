"""P0 补强端点注册 + Provider 方法存在性测试。"""
from __future__ import annotations

import pytest

# ── DataRouter EXTENDED 注册验证 ────────────────────────────────────

def test_p0_methods_in_extended_table() -> None:
    """P0 四个端点都在 EXTENDED_METHOD_TABLE。"""
    from data_pipeline.data_router import DataRouter
    expected = {
        "get_earnings_forecast",
        "get_analyst_consensus",
        "get_research_reports",
        "get_regulatory_penalties",
    }
    for m in expected:
        assert m in DataRouter.EXTENDED_METHOD_TABLE, f"{m} 缺 EXTENDED 注册"


def test_macro_methods_in_extended_table() -> None:
    """Macro 四个端点都在 EXTENDED_METHOD_TABLE。"""
    from data_pipeline.data_router import DataRouter
    expected = {
        "get_macro_pmi",
        "get_macro_cpi",
        "get_macro_m2",
        "get_macro_bond_yield",
    }
    for m in expected:
        assert m in DataRouter.EXTENDED_METHOD_TABLE, f"{m} 缺 EXTENDED 注册"


def test_extended_total_count() -> None:
    """EXTENDED 端点数 = 8(原始) + 4(P0) + 4(Macro) + 4(港股通+研报RAG) = 20。"""
    from data_pipeline.data_router import DataRouter
    # 历史：22 = 8 原始 + 4 P0 + 4 Macro + 2 港股通 + 2 研报 RAG + 1 cninfo + 1 global_quote
    # P4.3 多市场 +12 = 34
    # P2.7 指数 K +1 = 35
    assert len(DataRouter.EXTENDED_METHOD_TABLE) == 35  # 34 + 1 (index_kline)


def test_p0_methods_routable_with_extended() -> None:
    """DataRouter(include_extended=True) 能注册所有 P0 / Macro 端点。"""
    from data_pipeline.data_router import DataRouter
    from data_pipeline.providers.akshare_provider import AkshareProvider
    router = DataRouter(
        providers={"akshare": AkshareProvider()},
        include_extended=True,
    )
    methods = router.available_methods()
    for m in [
        "get_earnings_forecast", "get_analyst_consensus",
        "get_research_reports", "get_regulatory_penalties",
        "get_macro_pmi", "get_macro_cpi", "get_macro_m2", "get_macro_bond_yield",
    ]:
        assert m in methods


def test_p0_methods_NOT_in_default() -> None:
    """默认 router 不暴露 P0 / Macro 端点（按需 enable）。"""
    from data_pipeline.data_router import DataRouter
    router = DataRouter()  # 不传 include_extended
    methods = router.available_methods()
    for m in [
        "get_earnings_forecast", "get_analyst_consensus",
        "get_research_reports", "get_regulatory_penalties",
        "get_macro_pmi", "get_macro_cpi", "get_macro_m2", "get_macro_bond_yield",
    ]:
        assert m not in methods


# ── AkshareProvider 方法存在性 ─────────────────────────────────────

def test_akshare_provider_has_p0_methods() -> None:
    from data_pipeline.providers.akshare_provider import AkshareProvider
    p = AkshareProvider()
    for name in [
        "get_earnings_forecast", "get_analyst_consensus",
        "get_research_reports", "get_regulatory_penalties",
    ]:
        assert hasattr(p, name), f"AkshareProvider 缺方法 {name}"
        assert callable(getattr(p, name))


def test_akshare_provider_has_macro_methods() -> None:
    from data_pipeline.providers.akshare_provider import AkshareProvider
    p = AkshareProvider()
    for name in [
        "get_macro_pmi", "get_macro_cpi", "get_macro_m2", "get_macro_bond_yield",
    ]:
        assert hasattr(p, name), f"AkshareProvider 缺方法 {name}"
        assert callable(getattr(p, name))
