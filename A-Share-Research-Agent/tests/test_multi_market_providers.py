"""test_multi_market_providers — P4.3 多市场 provider 单测。

覆盖：
- BSEProvider / BSEIndexProvider / FuturesProvider / HKFullProvider / USStockProvider
  的端点签名 / 字段归一 / 失败语义。
- DataRouter EXTENDED_METHOD_TABLE 中 12 个新端点的路由与 provider 注入。
- normalize_symbol 静态方法的纯函数行为（6 位补零 / 5 位补零 / 字母大写）。

设计：
- 不真实打外网：通过 patch 替换 provider 内部 `ak` 句柄 + 直接 mock `_run_with_retry`。
- 真网路 e2e 在 verify_multi_market_smoke.py 中跑（不在 pytest 内）。
"""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

from data_pipeline.data_router import DataRouter
from data_pipeline.providers.bse_provider import BSEIndexProvider, BSEProvider
from data_pipeline.providers.futures_provider import FuturesProvider
from data_pipeline.providers.hk_full_provider import HKFullProvider
from data_pipeline.providers.us_stock_provider import USStockProvider


# ── BSE 基础结构 ────────────────────────────────────────────────────
class TestBSEProviderStructure:
    def test_has_methods(self) -> None:
        p = BSEProvider()
        assert callable(p.get_stock_list)
        assert callable(p.get_stock_daily_bars)

    def test_methods_are_coroutines(self) -> None:
        p = BSEProvider()
        assert inspect.iscoroutinefunction(p.get_stock_list)
        assert inspect.iscoroutinefunction(p.get_stock_daily_bars)

    def test_bse_index_provider_has_method(self) -> None:
        p = BSEIndexProvider()
        assert callable(p.get_index_daily_bars)
        assert inspect.iscoroutinefunction(p.get_index_daily_bars)


# ── BSE normalize_symbol 纯函数 ─────────────────────────────────────
class TestBSENormalizeSymbol:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("830001", "830001"),
            ("83001", "083001"),  # 补零
            ("BJ.830001", "830001"),  # 去前缀
            ("bj.830001", "830001"),
            ("  836999  ", "836999"),  # 去空白
        ],
    )
    def test_normalize(self, raw: str, expected: str) -> None:
        assert BSEProvider.normalize_symbol(raw) == expected


# ── BSE get_stock_list：列名归一 + exchange 标签 ─────────────────────
class TestBSEGetStockList:
    @pytest.mark.asyncio
    async def test_returns_normalized_df(self) -> None:
        raw = pd.DataFrame({
            "代码": ["830001", "830002"],
            "名称": ["颖泰生物", "康乐卫士"],
        })
        p = BSEProvider(akshare=_DummyAksh(raw))  # type: ignore[arg-type]
        df = await p.get_stock_list()
        assert list(df.columns) == ["code", "name", "exchange"]
        assert df.iloc[0]["code"] == "830001"
        assert df.iloc[0]["exchange"] == "BSE"
        assert df.iloc[1]["name"] == "康乐卫士"

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_df_with_schema(self) -> None:
        empty = pd.DataFrame()
        p = BSEProvider(akshare=_DummyAksh(empty))  # type: ignore[arg-type]
        df = await p.get_stock_list()
        assert list(df.columns) == ["code", "name", "exchange"]
        assert len(df) == 0


# ── BSE get_stock_daily_bars：symbol 归一 + 日期格式化 ───────────────
class TestBSEGetStockDailyBars:
    @pytest.mark.asyncio
    async def test_passes_normalized_symbol(self) -> None:
        raw = pd.DataFrame({
            "date": ["2026-06-01", "2026-06-02"],
            "open": [10.0, 10.5],
            "close": [10.4, 10.7],
            "high": [10.5, 10.8],
            "low": [9.9, 10.4],
            "volume": [1_000_000, 1_100_000],
        })
        captured: dict = {}

        async def _fake_run(func, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            captured["symbol"] = kw.get("symbol")
            captured["start_date"] = kw.get("start_date")
            captured["end_date"] = kw.get("end_date")
            return raw

        p = BSEProvider(akshare=_DummyAksh(None, _fake_run))  # type: ignore[arg-type]
        df = await p.get_stock_daily_bars("830001", "2026-06-01", "2026-06-30")
        assert captured["symbol"] == "830001"
        assert captured["start_date"] == "20260601"  # _fmt 去 - 拼接
        assert captured["end_date"] == "20260630"
        assert len(df) == 2


# ── BSE 失败语义：raise 透传 ─────────────────────────────────────────
class TestBSEFailureSemantics:
    @pytest.mark.asyncio
    async def test_stock_list_propagates(self) -> None:
        p = BSEProvider(akshare=_DummyAksh(None, side_effect=RuntimeError("akshare 反爬")))  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="反爬"):
            await p.get_stock_list()

    @pytest.mark.asyncio
    async def test_daily_bars_propagates(self) -> None:
        p = BSEProvider(akshare=_DummyAksh(None, side_effect=ConnectionError("timeout")))  # type: ignore[arg-type]
        with pytest.raises(ConnectionError, match="timeout"):
            await p.get_stock_daily_bars("830001")


# ── Futures 基础结构 ────────────────────────────────────────────────
class TestFuturesProviderStructure:
    def test_has_methods(self) -> None:
        p = FuturesProvider()
        assert callable(p.get_main_contracts_sina)
        assert callable(p.get_daily_bars_sina)
        assert callable(p.get_sector_spot)
        for name in ("get_main_contracts_sina", "get_daily_bars_sina", "get_sector_spot"):
            assert inspect.iscoroutinefunction(getattr(p, name))

    @pytest.mark.parametrize("raw,expected", [("rb2501", "RB2501"), ("CU2501", "CU2501"), ("  AG2502  ", "AG2502")])
    def test_normalize(self, raw: str, expected: str) -> None:
        assert FuturesProvider.normalize_symbol(raw) == expected


# ── Futures get_main_contracts_sina：列名兼容（symbol/代码 + name/名称） ─
class TestFuturesGetMainContracts:
    @pytest.mark.asyncio
    async def test_renames_columns_to_canonical(self) -> None:
        raw = pd.DataFrame({
            "代码": ["RB2501", "CU2501"],
            "名称": ["螺纹钢", "沪铜"],
        })
        p = FuturesProvider(akshare=_DummyAksh(raw))  # type: ignore[arg-type]
        df = await p.get_main_contracts_sina()
        assert list(df.columns) == ["symbol", "name", "exchange"]
        assert df.iloc[0]["symbol"] == "RB2501"
        assert df.iloc[1]["name"] == "沪铜"


# ── Futures get_daily_bars_sina：日期过滤 ──────────────────────────
class TestFuturesGetDailyBars:
    @pytest.mark.asyncio
    async def test_passes_normalized_symbol(self) -> None:
        captured: dict = {}

        async def _fake_run(func, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            captured["symbol"] = kw.get("symbol")
            return pd.DataFrame({"date": pd.to_datetime(["2026-06-01", "2026-06-02"]), "close": [100, 101]})

        p = FuturesProvider(akshare=_DummyAksh(None, _fake_run))  # type: ignore[arg-type]
        df = await p.get_daily_bars_sina("rb2501")
        assert captured["symbol"] == "RB2501"
        assert len(df) == 2


# ── HK Full 基础结构 + normalize ───────────────────────────────────
class TestHKFullProviderStructure:
    def test_has_methods(self) -> None:
        p = HKFullProvider()
        assert callable(p.get_stock_list)
        assert callable(p.get_daily_bars)
        assert callable(p.get_index_daily_bars)
        for name in ("get_stock_list", "get_daily_bars", "get_index_daily_bars"):
            assert inspect.iscoroutinefunction(getattr(p, name))

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("00700", "00700"),
            ("700", "00700"),  # 5 位补零
            ("HK.00700", "00700"),
            ("hk.700", "00700"),
        ],
    )
    def test_normalize(self, raw: str, expected: str) -> None:
        assert HKFullProvider.normalize_symbol(raw) == expected


# ── HK Full get_stock_list：列名兼容 + 5 位补零 ─────────────────────
class TestHKFullGetStockList:
    @pytest.mark.asyncio
    async def test_normalize_code_to_5digits(self) -> None:
        raw = pd.DataFrame({
            "代码": ["700", "9988"],  # 3 位 / 4 位原始
            "名称": ["腾讯控股", "阿里巴巴"],
        })
        p = HKFullProvider(akshare=_DummyAksh(raw))  # type: ignore[arg-type]
        df = await p.get_stock_list()
        assert list(df.columns) == ["code", "name", "exchange"]
        assert df.iloc[0]["code"] == "00700"  # 补零
        assert df.iloc[1]["code"] == "09988"  # 补零
        assert df.iloc[0]["exchange"] == "HK"


# ── HK Full get_index_daily_bars：日期过滤（中文列名兼容） ──────────
class TestHKFullGetIndexDailyBars:
    @pytest.mark.asyncio
    async def test_filters_by_date_chinese_column(self) -> None:
        raw = pd.DataFrame({
            "日期": pd.to_datetime(["2026-06-01", "2026-06-15", "2026-06-30"]),
            "close": [18000.0, 18500.0, 18700.0],
        })

        async def _fake_run(func, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            return raw

        p = HKFullProvider(akshare=_DummyAksh(None, _fake_run))  # type: ignore[arg-type]
        df = await p.get_index_daily_bars("HKHSI", "2026-06-10", "2026-06-30")
        # 过滤后保留 06-15 和 06-30 两条
        assert len(df) == 2


# ── US Stock 基础结构 + normalize ──────────────────────────────────
class TestUSStockProviderStructure:
    def test_has_methods(self) -> None:
        p = USStockProvider()
        assert callable(p.get_stock_list)
        assert callable(p.get_daily_bars)
        assert callable(p.get_spot_quote)
        for name in ("get_stock_list", "get_daily_bars", "get_spot_quote"):
            assert inspect.iscoroutinefunction(getattr(p, name))

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("AAPL", "AAPL"),
            ("aapl", "AAPL"),  # 大写
            ("  TSLA  ", "TSLA"),  # 去空白
            ("US.AAPL", "AAPL"),  # 去前缀
        ],
    )
    def test_normalize(self, raw: str, expected: str) -> None:
        # 美股代码不补零（字母）
        assert USStockProvider.normalize_symbol(raw) == expected


# ── US Stock get_spot_quote：code 匹配 + 字段归一 ───────────────────
class TestUSStockGetSpotQuote:
    @pytest.mark.asyncio
    async def test_match_and_extract(self) -> None:
        raw = pd.DataFrame({
            "代码": ["AAPL", "TSLA", "MSFT"],
            "名称": ["苹果", "特斯拉", "微软"],
            "最新价": [195.0, 240.0, 410.0],
            "涨跌幅": [1.5, -2.3, 0.8],
        })

        async def _fake_run(func, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            return raw

        p = USStockProvider(akshare=_DummyAksh(None, _fake_run))  # type: ignore[arg-type]
        r = await p.get_spot_quote("aapl")  # 小写传入
        assert r["symbol"] == "AAPL"
        assert r["price"] == 195.0
        assert r["chg_pct"] == 1.5
        assert r["name"] == "苹果"

    @pytest.mark.asyncio
    async def test_not_found_returns_zero(self) -> None:
        raw = pd.DataFrame({
            "代码": ["AAPL", "TSLA"],
            "名称": ["苹果", "特斯拉"],
            "最新价": [195.0, 240.0],
            "涨跌幅": [1.5, -2.3],
        })

        async def _fake_run(func, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            return raw

        p = USStockProvider(akshare=_DummyAksh(None, _fake_run))  # type: ignore[arg-type]
        r = await p.get_spot_quote("GOOG")  # 不在列表中
        assert r["symbol"] == "GOOG"
        assert r["price"] == 0.0


# ── DataRouter EXTENDED 注册 + 注入验证 ────────────────────────────
class TestDataRouterIntegration:
    def test_extended_method_table_has_12_multi_market_methods(self) -> None:
        """P4.3：12 个多市场端点全部在 EXTENDED_METHOD_TABLE。"""
        expected = {
            # BSE 3
            "get_bse_stock_list", "get_bse_stock_daily_bars", "get_bse_index_daily_bars",
            # Futures 3
            "get_futures_main_contracts", "get_futures_daily_bars", "get_futures_sector_spot",
            # HK Full 3
            "get_hk_full_stock_list", "get_hk_full_daily_bars", "get_hk_index_daily_bars",
            # US 3
            "get_us_stock_list", "get_us_stock_daily_bars", "get_us_spot_quote",
        }
        for m in expected:
            assert m in DataRouter.EXTENDED_METHOD_TABLE, f"{m} 缺 EXTENDED 注册"
            providers_map = DataRouter.EXTENDED_METHOD_TABLE[m]
            assert len(providers_map) == 1, f"{m} 路由应只指向单一 provider：{providers_map}"

    def test_extended_method_table_routes_correctly(self) -> None:
        """P4.3：每个 method 路由到正确的 provider。"""
        table = DataRouter.EXTENDED_METHOD_TABLE
        # BSE
        assert table["get_bse_stock_list"] == {"bse": "get_stock_list"}
        assert table["get_bse_stock_daily_bars"] == {"bse": "get_stock_daily_bars"}
        assert table["get_bse_index_daily_bars"] == {"bse_index": "get_index_daily_bars"}
        # Futures
        assert table["get_futures_main_contracts"] == {"futures": "get_main_contracts_sina"}
        assert table["get_futures_daily_bars"] == {"futures": "get_daily_bars_sina"}
        assert table["get_futures_sector_spot"] == {"futures": "get_sector_spot"}
        # HK
        assert table["get_hk_full_stock_list"] == {"hk_full": "get_stock_list"}
        assert table["get_hk_full_daily_bars"] == {"hk_full": "get_daily_bars"}
        assert table["get_hk_index_daily_bars"] == {"hk_full": "get_index_daily_bars"}
        # US
        assert table["get_us_stock_list"] == {"us_stock": "get_stock_list"}
        assert table["get_us_stock_daily_bars"] == {"us_stock": "get_daily_bars"}
        assert table["get_us_spot_quote"] == {"us_stock": "get_spot_quote"}

    def test_tool_categories_have_4_multi_market(self) -> None:
        """P4.3：4 个新分类已在 TOOL_CATEGORIES。"""
        cats = DataRouter.TOOL_CATEGORIES
        for c in ("bse_market", "futures_market", "hk_market", "us_market"):
            assert c in cats, f"分类 {c} 缺注册"
        # 数量检查
        assert len(cats["bse_market"]["tools"]) == 3
        assert len(cats["futures_market"]["tools"]) == 3
        assert len(cats["hk_market"]["tools"]) == 3
        assert len(cats["us_market"]["tools"]) == 3

    def test_method_to_category_lookup(self) -> None:
        """P4.3：method → category 反向索引正确。"""
        r = DataRouter(include_extended=True)
        assert r._method_to_category["get_bse_stock_list"] == "bse_market"
        assert r._method_to_category["get_futures_main_contracts"] == "futures_market"
        assert r._method_to_category["get_hk_full_daily_bars"] == "hk_market"
        assert r._method_to_category["get_us_spot_quote"] == "us_market"

    def test_providers_injected_by_default(self) -> None:
        """P4.3：4 个新 provider 注入 default providers dict。"""
        r = DataRouter(include_extended=True)
        for p in ("bse", "bse_index", "futures", "hk_full", "us_stock"):
            assert p in r.providers, f"provider {p} 未注入"
        assert isinstance(r.providers["bse"], BSEProvider)
        assert isinstance(r.providers["bse_index"], BSEIndexProvider)
        assert isinstance(r.providers["futures"], FuturesProvider)
        assert isinstance(r.providers["hk_full"], HKFullProvider)
        assert isinstance(r.providers["us_stock"], USStockProvider)

    def test_multi_market_methods_NOT_in_default_router(self) -> None:
        """P4.3：默认 router（include_extended=False）不暴露多市场端点。"""
        r = DataRouter()
        for m in (
            "get_bse_stock_list", "get_futures_main_contracts",
            "get_hk_full_daily_bars", "get_us_spot_quote",
        ):
            assert m not in r.available_methods(), (
                f"{m} 不应在默认 router 中（应在 EXTENDED）"
            )


# ── helper：mock AkshareProvider ─────────────────────────────────────
class _DummyAkshModule:
    """哑 akshare 模块 — 任何属性访问都返回一个可调用的占位函数。"""

    def __getattr__(self, name: str):  # noqa: ANN204
        return lambda *a, **kw: None  # noqa: ANN002, ANN003


class _DummyAksh:
    """mock akshare provider — 替身 AkshareProvider 实例。

    接收 `_run_with_retry` 行为：
    - 传 df 时直接返回
    - 传 callable 时作为 fake_run 用
    - 传 side_effect 时抛错

    `_ensure_akshare()` 返回一个哑模块（任何属性都返回可调用占位），
    避免 bse/futures/hk/us provider 内部访问 `self._ak._ensure_akshare().stock_xxx` 报错。
    """

    def __init__(self, df, run_impl=None, side_effect=None):
        self._df = df
        self._run_impl = run_impl
        self._side_effect = side_effect

    # 暴露 _run_with_retry
    async def _run_with_retry(self, func, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if self._side_effect is not None:
            if isinstance(self._side_effect, BaseException):
                raise self._side_effect
            if callable(self._side_effect):
                return await self._side_effect(func, *args, **kwargs)
            return self._side_effect
        if self._run_impl is not None:
            return await self._run_impl(func, *args, **kwargs)
        return self._df

    # 暴露 _ensure_akshare：返回哑模块，避免下游 .stock_xxx 属性访问崩
    def _ensure_akshare(self):  # noqa: ANN202
        return _DummyAkshModule()
