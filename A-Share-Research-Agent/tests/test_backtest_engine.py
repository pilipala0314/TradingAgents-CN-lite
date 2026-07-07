"""回测引擎单测（mock DataRouter，无网络依赖）。

覆盖：
- BacktestConfig 校验（日期顺序 / 调仓频率 / 持仓 N）
- BacktestEngine.run 完整流程（拉数据 → 调仓 → 收益 → 指标）
- 跳过股票（拉数据失败 / 数据不足）
- 调仓日选择（daily/weekly/monthly）
- 调仓记录完整性
- get_hs300_symbols 容错（akshare 失败）
- BacktestResult.to_dict 输出结构
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    get_hs300_symbols,
)
from services.strategies import get_strategy

# ── 工具函数 ─────────────────────────────────────────────

def _mk_bars(closes: list[float], start: date = date(2025, 1, 1)) -> list[dict]:
    out: list[dict] = []
    for i, c in enumerate(closes):
        out.append(
            {
                "date": start + timedelta(days=i),
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": 1_000_000,
            }
        )
    return out


def _mk_mock_router(bars_by_symbol: dict[str, list[dict]]) -> MagicMock:
    """构造 mock DataRouter：按 symbol 返对应 bars。"""
    router = MagicMock()

    def fake_route(method: str, **kwargs: Any) -> list[dict]:
        if method != "get_daily_bars":
            return []
        sym = kwargs.get("symbol", "")
        return bars_by_symbol.get(sym, [])

    router.route.side_effect = fake_route
    return router


# ── BacktestConfig 校验 ───────────────────────────────────

class TestBacktestConfig:
    def test_valid(self) -> None:
        cfg = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=["600519"],
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        assert cfg.top_n == 20  # default
        assert cfg.rebalance_freq == "weekly"  # default

    def test_invalid_freq(self) -> None:
        with pytest.raises(ValueError, match="rebalance_freq"):
            BacktestConfig(
                strategy_name="momentum_20d",
                symbols=["600519"],
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                rebalance_freq="hourly",
            )

    def test_invalid_dates(self) -> None:
        with pytest.raises(ValueError, match="start_date 必须早于"):
            BacktestConfig(
                strategy_name="momentum_20d",
                symbols=["600519"],
                start_date=date(2025, 12, 31),
                end_date=date(2025, 1, 1),
            )

    def test_zero_top_n(self) -> None:
        with pytest.raises(ValueError, match="top_n"):
            BacktestConfig(
                strategy_name="momentum_20d",
                symbols=["600519"],
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                top_n=0,
            )

    def test_empty_symbols(self) -> None:
        with pytest.raises(ValueError, match="symbols"):
            BacktestConfig(
                strategy_name="momentum_20d",
                symbols=[],
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )


# ── 调仓日选择 ─────────────────────────────────────────

class TestRebalanceDates:
    def test_daily(self) -> None:
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(10)]
        picked = BacktestEngine._pick_rebalance_dates(dates, "daily")
        assert picked == dates

    def test_weekly(self) -> None:
        # 1/1 周三、1/2 周四、1/3 周五、1/4 周六、1/5 周日、1/6 周一、1/7 周二
        # ISO week：1/1 周三属于 2025 W01，1/4 周六也属于 W01，1/6 周一属于 W02
        dates = [
            date(2025, 1, 1),  # W01
            date(2025, 1, 2),  # W01
            date(2025, 1, 3),  # W01
            date(2025, 1, 6),  # W02
            date(2025, 1, 7),  # W02
            date(2025, 1, 8),  # W02
        ]
        picked = BacktestEngine._pick_rebalance_dates(dates, "weekly")
        # 每周第一个交易日：1/1（W01）、1/6（W02）
        assert picked == [date(2025, 1, 1), date(2025, 1, 6)]

    def test_monthly(self) -> None:
        dates = [
            date(2025, 1, 2),
            date(2025, 1, 3),
            date(2025, 2, 1),
            date(2025, 2, 5),
            date(2025, 3, 1),
        ]
        picked = BacktestEngine._pick_rebalance_dates(dates, "monthly")
        # 每月第一个交易日
        assert picked == [date(2025, 1, 2), date(2025, 2, 1), date(2025, 3, 1)]


# ── 引擎主流程 ─────────────────────────────────────────

class TestBacktestEngineRun:
    def test_basic_momentum_weekly(self) -> None:
        """1 只股票、上行趋势，momentum 策略 weekly 调仓，top 1 → 满仓涨。"""
        # 60 根 bar 单调上涨
        closes = [100.0 + i for i in range(60)]
        bars = _mk_bars(closes)
        router = _mk_mock_router({"600519": bars})
        engine = BacktestEngine(router=router)
        cfg = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=["600519"],
            start_date=bars[0]["date"],
            end_date=bars[-1]["date"],
            top_n=1,
            rebalance_freq="weekly",
        )
        result = engine.run(cfg)
        assert isinstance(result, BacktestResult)
        assert result.metrics["n_periods"] > 0
        assert result.metrics["total_return"] > 0  # 整体上行 → 涨
        # 调仓次数 ≈ 9 次 weekly（60 天 ≈ 9 周）
        assert 5 <= len(result.rebalance_dates) <= 15
        # nav 序列长度 = returns 长度 + 1
        assert len(result.nav) == len(result.returns) + 1
        # trades 数量 ≈ 调仓次数 - 1（第 0 日不调）
        assert len(result.trades) == len(result.rebalance_dates) - 1

    def test_skip_insufficient_data(self) -> None:
        """数据不足的股票应被跳过。"""
        bars_short = _mk_bars([100.0] * 5)  # 太短
        bars_ok = _mk_bars([100.0 + i for i in range(60)])
        router = _mk_mock_router({"600519": bars_short, "000001": bars_ok})
        engine = BacktestEngine(router=router)
        cfg = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=["600519", "000001"],
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 1),
        )
        result = engine.run(cfg)
        assert "600519" in result.skipped_symbols
        assert "000001" not in result.skipped_symbols

    def test_skip_router_failure(self) -> None:
        """DataRouter 抛异常时，股票被跳过。"""

        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("network down")

        router = MagicMock()
        router.route.side_effect = boom
        engine = BacktestEngine(router=router)
        cfg = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=["600519"],
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        with pytest.raises(RuntimeError, match="所有股票拉数据均失败"):
            engine.run(cfg)

    def test_top_n_filters(self) -> None:
        """top_n=1 时即使股票池有 3 只，也只买 1 只。"""
        # 3 只股票不同方向
        bars1 = _mk_bars([100.0 + i for i in range(60)])  # 涨
        bars2 = _mk_bars([100.0 - i * 0.5 for i in range(60)])  # 跌
        bars3 = _mk_bars([100.0] * 60)  # 平
        router = _mk_mock_router(
            {
                "A": bars1,
                "B": bars2,
                "C": bars3,
            }
        )
        engine = BacktestEngine(router=router)
        cfg = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=["A", "B", "C"],
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 1),
            top_n=1,
            rebalance_freq="weekly",
        )
        result = engine.run(cfg)
        # 每次调仓最多 1 只
        for t in result.trades:
            assert len(t["bought"]) <= 1
            assert t["selected_n"] <= 1

    def test_to_dict_structure(self) -> None:
        bars = _mk_bars([100.0 + i for i in range(60)])
        router = _mk_mock_router({"600519": bars})
        engine = BacktestEngine(router=router)
        cfg = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=["600519"],
            start_date=bars[0]["date"],
            end_date=bars[-1]["date"],
            top_n=1,
            rebalance_freq="weekly",
        )
        result = engine.run(cfg)
        d = result.to_dict()
        assert "config" in d and "metrics" in d and "trades" in d
        assert "nav" in d and "trading_dates" in d
        assert d["config"]["strategy_name"] == "momentum_20d"
        assert isinstance(d["nav"], list)
        assert isinstance(d["trading_dates"], list)


# ── get_hs300_symbols ────────────────────────────────────

class TestGetHs300Symbols:
    def test_with_mocked_router(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import pandas as pd

        fake_df = pd.DataFrame(
            {
                "品种代码": ["600519", "000001", "600036"],
                "品种名称": ["贵州茅台", "平安银行", "招商银行"],
                "纳入日期": ["2005-01-01"] * 3,
            }
        )

        fake_router = MagicMock()
        fake_router.route = MagicMock(return_value=fake_df)
        monkeypatch.setattr("services.backtest_engine.get_router", lambda: fake_router)
        codes = get_hs300_symbols()
        assert codes == ["600519", "000001", "600036"]

    def test_router_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_router = MagicMock()
        fake_router.route = MagicMock(side_effect=RuntimeError("router down"))
        monkeypatch.setattr("services.backtest_engine.get_router", lambda: fake_router)
        codes = get_hs300_symbols()
        assert codes == []
