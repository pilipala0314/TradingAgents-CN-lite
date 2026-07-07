"""provider_adapters.py 单元测试 — 各 provider 签名转换正确性。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from data_pipeline.provider_adapters import (
    _akshare_daily_bars_adapter,
    _akshare_minute_bars_adapter,
    _netease_daily_bars_adapter,
    _netease_minute_bars_adapter,
    _parse_yyyymmdd,
    _period_to_scale,
    _sina_daily_bars_adapter,
    _sina_minute_bars_adapter,
)

# ── get_daily_bars adapter ─────────────────────────────────────

class TestSinaDailyBarsAdapter:
    def test_with_dates(self) -> None:
        """传 start_date / end_date → 转 (symbol, scale=240, datalen=N)，
        并把 start_date / end_date 透传为 kwargs 给 sina provider 内部 post-filter。"""
        end = date.today()
        start = end - timedelta(days=9)  # 10 个日历日
        args, kwargs = _sina_daily_bars_adapter(
            ("000636", start.strftime("%Y%m%d"), end.strftime("%Y%m%d")),
            {},
        )
        assert args[0] == "000636"
        assert args[1] == 240  # scale=日 K
        assert 5 <= args[2] <= 500  # datalen
        assert kwargs["start_date"] == start.strftime("%Y%m%d")
        assert kwargs["end_date"] == end.strftime("%Y%m%d")

    def test_no_dates_uses_default(self) -> None:
        args, kwargs = _sina_daily_bars_adapter(("000636",), {})
        assert args == ("000636", 240, 60)

    def test_dashed_format_dates(self) -> None:
        end = date.today()
        start = end - timedelta(days=4)
        args, kwargs = _sina_daily_bars_adapter(
            ("000636", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            {},
        )
        assert args[0] == "000636"
        assert args[1] == 240
        assert kwargs["start_date"] == start.strftime("%Y-%m-%d")
        assert kwargs["end_date"] == end.strftime("%Y-%m-%d")

    def test_empty_args(self) -> None:
        args, kwargs = _sina_daily_bars_adapter((), {})
        # 没 symbol 时不强行构造，返回空
        assert args == ()
        assert kwargs == {}


class TestAkshareDailyBarsAdapter:
    def test_passthrough(self) -> None:
        args_in = ("000636", "20240101", "20240115", "")
        args, kwargs = _akshare_daily_bars_adapter(args_in, {})
        assert args == args_in
        assert kwargs == {}


class TestNeteaseDailyBarsAdapter:
    def test_renames_dates_to_start_end(self) -> None:
        """标准签名 start_date / end_date → netease 签名 start / end（按位置参数）。"""
        end = date(2024, 1, 15)
        start = end - timedelta(days=14)
        args, kwargs = _netease_daily_bars_adapter(
            ("000636", start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ""),
            {},
        )
        # 位置参数：symbol, start, end, adjust
        assert args == ("000636", start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "")
        assert kwargs == {}

    def test_kwargs_input_renamed(self) -> None:
        """kwargs 输入：start_date / end_date 也重命名（结果放在位置）。"""
        args, kwargs = _netease_daily_bars_adapter(
            (),
            {"symbol": "000636", "start_date": "20240101", "end_date": "20240115", "adjust": "qfq"},
        )
        assert args == ("000636", "20240101", "20240115", "qfq")
        assert kwargs == {}

    def test_empty_args(self) -> None:
        args, kwargs = _netease_daily_bars_adapter((), {})
        assert args == ()
        assert kwargs == {}


# ── get_minute_bars adapter ────────────────────────────────────

class TestSinaMinuteBarsAdapter:
    def test_period_1min(self) -> None:
        end = "2024-01-15 15:00:00"
        args, kwargs = _sina_minute_bars_adapter(("000636", "", end, "1"), {})
        assert args == ("000636", 1, 240)

    def test_period_5min(self) -> None:
        args, kwargs = _sina_minute_bars_adapter(("000636", "", "", "5"), {})
        assert args == ("000636", 5, 240)

    def test_period_15min(self) -> None:
        args, kwargs = _sina_minute_bars_adapter(("000636", "", "", "15"), {})
        assert args == ("000636", 15, 240)

    def test_period_30min(self) -> None:
        args, kwargs = _sina_minute_bars_adapter(("000636", "", "", "30"), {})
        assert args == ("000636", 30, 240)

    def test_period_60min(self) -> None:
        args, kwargs = _sina_minute_bars_adapter(("000636", "", "", "60"), {})
        assert args == ("000636", 60, 240)


class TestNeteaseMinuteBarsAdapter:
    def test_passthrough_period(self) -> None:
        args, kwargs = _netease_minute_bars_adapter(("000636", "", "", "1m"), {})
        assert args == ("000636", "1m", 240)


class TestAkshareMinuteBarsAdapter:
    def test_passthrough(self) -> None:
        args_in = ("000636", "20240115 09:30:00", "20240115 15:00:00", "1", "")
        args, kwargs = _akshare_minute_bars_adapter(args_in, {})
        assert args == args_in


# ── 工具函数 ──────────────────────────────────────────────────

class TestParseYyyymmdd:
    def test_compact(self) -> None:
        d = _parse_yyyymmdd("20240115")
        assert d == date(2024, 1, 15)

    def test_dashed(self) -> None:
        d = _parse_yyyymmdd("2024-01-15")
        assert d == date(2024, 1, 15)

    def test_slashed(self) -> None:
        d = _parse_yyyymmdd("2024/01/15")
        assert d == date(2024, 1, 15)

    def test_empty_returns_today(self) -> None:
        d = _parse_yyyymmdd("")
        assert d == date.today()

    def test_none_returns_today(self) -> None:
        d = _parse_yyyymmdd(None)  # type: ignore[arg-type]
        assert d == date.today()


class TestPeriodToScale:
    def test_int_periods(self) -> None:
        assert _period_to_scale(1) == 1
        assert _period_to_scale(5) == 5
        assert _period_to_scale(15) == 15
        assert _period_to_scale(30) == 30
        assert _period_to_scale(60) == 60

    def test_string_periods(self) -> None:
        assert _period_to_scale("1") == 1
        assert _period_to_scale("5") == 5

    def test_invalid_returns_1(self) -> None:
        assert _period_to_scale("invalid") == 1
        assert _period_to_scale(999) == 1
        assert _period_to_scale(None) == 1  # type: ignore[arg-type]


# ── DataRouter 集成验证（adapter 自动包装） ─────────────────

class TestDataRouterAdapterRegistration:
    def test_adapters_registered(self) -> None:
        from data_pipeline.data_router import DataRouter

        # register_all_adapters() 在 DataRouter.__init__ 触发，实例化一次即可
        DataRouter(include_extended=True)
        assert ("get_daily_bars", "akshare") in DataRouter._METHOD_ADAPTERS
        assert ("get_daily_bars", "sina") in DataRouter._METHOD_ADAPTERS
        assert ("get_daily_bars", "netease") in DataRouter._METHOD_ADAPTERS
        assert ("get_minute_bars", "akshare") in DataRouter._METHOD_ADAPTERS
        assert ("get_minute_bars", "sina") in DataRouter._METHOD_ADAPTERS
        assert ("get_minute_bars", "netease") in DataRouter._METHOD_ADAPTERS

    def test_method_table_uses_adapted_provider(self) -> None:
        from data_pipeline.adapters import AdaptedProvider
        from data_pipeline.data_router import DataRouter
        from data_pipeline.providers.akshare_provider import AkshareProvider
        from data_pipeline.providers.sina_provider import SinaProvider

        router = DataRouter(include_extended=True)
        # 验证 get_daily_bars 的 sina 方法被 AdaptedProvider 包装
        sina_fn = router._method_table.get("get_daily_bars", {}).get("sina")
        if sina_fn is not None:  # 如果 SinaProvider 加载成功
            assert isinstance(sina_fn, AdaptedProvider)
            assert sina_fn.adapter is not None
