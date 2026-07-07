"""回测内置策略集 — 可插拔 4 选 1。

每个策略实现 `score(bars, end_idx) -> float`：
- 输入：单只股票截至回测日的全部日 K 线（list[dict] 含 date/open/high/low/close/volume）
- end_idx：当前调仓日对应的 bar 索引
- 输出：分数（高分 = 排序靠前 / 优先买入；低分 = 优先卖出）

回测引擎约定：策略只算分数，不做仓位管理（调仓 + 仓位由引擎统一控制）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Strategy(ABC):
    """回测策略抽象基类。"""

    name: str = ""
    description: str = ""

    @abstractmethod
    def score(self, bars: list[dict[str, Any]], end_idx: int) -> float:
        """算单只股票截至 end_idx 的分数。

        Args:
            bars:   单只股票全部日 K 线（list[dict]，每条含 date/open/high/low/close/volume）
            end_idx: 当前调仓日对应的 bar 索引（bars[end_idx] 即调仓当日 K 线）

        Returns:
            分数。约定：高分 = 优先买入；低分 = 优先卖出。无信号时返 None 或 NaN。
        """
        raise NotImplementedError

    def min_lookback(self) -> int:
        """策略最少需要的 lookback 窗口（用于引擎判断是否跳过早期 K 线）。"""
        return 20


# ── 1. 动量策略 ────────────────────────────────────────────────
class MomentumStrategy(Strategy):
    """20 日动量（lookback=20，调仓当日 close / 20 日前 close - 1）。

    经典动量因子：强者恒强。但 A 股反转效应显著，调仓频率应低（weekly/monthly）。
    """

    name = "momentum_20d"
    description = "20 日动量：close(t) / close(t-20) - 1"

    def __init__(self, lookback: int = 20) -> None:
        self.lookback = lookback

    def score(self, bars: list[dict], end_idx: int) -> float:
        if end_idx < self.lookback:
            return 0.0
        try:
            c_now = float(bars[end_idx]["close"])
            c_pre = float(bars[end_idx - self.lookback]["close"])
        except (KeyError, TypeError, ValueError):
            return 0.0
        if c_pre <= 0:
            return 0.0
        return c_now / c_pre - 1.0

    def min_lookback(self) -> int:
        return self.lookback + 1


# ── 2. 反转策略 ────────────────────────────────────────────────
class MeanReversionStrategy(Strategy):
    """5 日反转（lookback=5）：close(t) / close(t-5) - 1，再取负。

    反向动量：跌多反弹。短 lookback 适合 daily/weekly 调仓。
    """

    name = "mean_reversion_5d"
    description = "5 日反转：-(close(t) / close(t-5) - 1)"

    def __init__(self, lookback: int = 5) -> None:
        self.lookback = lookback

    def score(self, bars: list[dict], end_idx: int) -> float:
        if end_idx < self.lookback:
            return 0.0
        try:
            c_now = float(bars[end_idx]["close"])
            c_pre = float(bars[end_idx - self.lookback]["close"])
        except (KeyError, TypeError, ValueError):
            return 0.0
        if c_pre <= 0:
            return 0.0
        return -(c_now / c_pre - 1.0)

    def min_lookback(self) -> int:
        return self.lookback + 1


# ── 3. 价值策略（PE/PB proxy） ──────────────────────────────────
class ValueStrategy(Strategy):
    """价值策略 proxy：用 close / 250 日均价的偏离度（越低越"便宜"）。

    真正的 PE/PB 需财务数据（按季度更新），回测场景用均价偏离 proxy 等效：
    - 分数 = (close - MA250) / MA250，越低越"价值"
    - 排序时取负 → 偏离最低的优先买入
    """

    name = "value_ma250"
    description = "价值 proxy：-(close - MA250) / MA250（低偏离优先）"

    def __init__(self, ma_window: int = 250) -> None:
        self.ma_window = ma_window

    def score(self, bars: list[dict], end_idx: int) -> float:
        if end_idx < self.ma_window:
            return 0.0
        try:
            closes = [float(b["close"]) for b in bars[end_idx - self.ma_window + 1: end_idx + 1]]
        except (KeyError, TypeError, ValueError):
            return 0.0
        if not closes or any(c <= 0 for c in closes):
            return 0.0
        ma = sum(closes) / len(closes)
        if ma <= 0:
            return 0.0
        return -((closes[-1] - ma) / ma)

    def min_lookback(self) -> int:
        return self.ma_window + 1


# ── 4. 多因子策略（动量 + 波动率 inverse） ──────────────────────
class MultiFactorStrategy(Strategy):
    """多因子：动量（60 日）+ 低波动（20 日 inverse std）。

    因子合成：score = momentum_zscore - volatility_zscore
    简单 zscore（用 (x - mean) / std）跨股票间归一化。
    """

    name = "multi_factor_mom_vol"
    description = "多因子：60 日动量 zscore - 20 日波动率 zscore"

    def __init__(self, lookback_mom: int = 60, lookback_vol: int = 20) -> None:
        self.lookback_mom = lookback_mom
        self.lookback_vol = lookback_vol

    def score(self, bars: list[dict], end_idx: int) -> float:
        if end_idx < self.lookback_mom:
            return 0.0
        try:
            closes = [float(b["close"]) for b in bars[end_idx - self.lookback_mom + 1: end_idx + 1]]
        except (KeyError, TypeError, ValueError):
            return 0.0
        if len(closes) < 2:
            return 0.0
        # 动量：最后一日相对窗口起点的涨幅
        momentum = closes[-1] / closes[0] - 1.0
        # 波动率：最近 lookback_vol 日收益率标准差
        recent = closes[-self.lookback_vol - 1:]
        if len(recent) < 3:
            vol = 0.0
        else:
            rets = [recent[i] / recent[i - 1] - 1.0 for i in range(1, len(recent))]
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / len(rets)
            vol = var ** 0.5
        # 简单组合：动量 + 低波动（减）
        return momentum - 2.0 * vol

    def min_lookback(self) -> int:
        return self.lookback_mom + 1


# ── 策略注册表 ──────────────────────────────────────────────────
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (
        MomentumStrategy,
        MeanReversionStrategy,
        ValueStrategy,
        MultiFactorStrategy,
    )
}


def get_strategy(name: str) -> Strategy:
    """按 name 取策略实例。"""
    if name not in STRATEGY_REGISTRY:
        raise KeyError(
            f"未知策略：{name}。可选：{list(STRATEGY_REGISTRY.keys())}"
        )
    return STRATEGY_REGISTRY[name]()


def list_strategies() -> list[dict[str, str]]:
    """列出所有可用策略。"""
    return [
        {"name": cls.name, "description": cls.description}
        for cls in (MomentumStrategy, MeanReversionStrategy, ValueStrategy, MultiFactorStrategy)
    ]
