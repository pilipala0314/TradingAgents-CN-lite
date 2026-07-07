"""回测引擎 — 组合 + 调仓 + 收益计算。

## 流程

1. **数据准备**：对股票池中每只股票通过 DataRouter 拉日 K 线（带 date range 过滤）
2. **调仓日选择**：按 `rebalance_freq`（daily/weekly/monthly）从交易日序列中取调仓日
3. **每个调仓日**：
   a. 调用 strategy.score(bars, end_idx) 算每只股票分数
   b. 排序取 top N（按 score 降序，剔除 score<=0 或数据不足的）
   c. 调仓：卖出不在新 top N 的持仓，买入新 top N
4. **日收益计算**：用 bar-to-bar 价格变动 + 调仓冲击（默认忽略摩擦成本）
5. **指标计算**：调 backtest_metrics.compute_metrics

## 约束

- 拉数据失败的股票 → 跳过（不阻塞整体回测）
- 拉到的数据 < min_lookback → 跳过该股票
- 调仓日无 bar 数据 → 当日不调仓
- 持仓期内的日收益 = ∑(各持仓 weight_i × 持仓股日收益)

## 配置

- 股票池默认：沪深 300 全部 300 只
- 区间默认：近 1 年
- 调仓频率：weekly（推荐，降低反转策略的换手率）
- 持仓 N：top 20
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from data_pipeline.data_router import DataRouter
from data_pipeline.facade_normalizer import normalize_response
from data_pipeline.providers.facade import get_router
from services.backtest_attribution import alpha_beta
from services.backtest_metrics import compute_metrics, nav_curve
from services.strategies import STRATEGY_REGISTRY, Strategy, get_strategy

logger = logging.getLogger(__name__)


# ── 配置 ──────────────────────────────────────────────────────

VALID_FREQS = frozenset({"daily", "weekly", "monthly"})


def _coerce_date(value: Any, field_name: str) -> date:
    """统一把 date / datetime / ISO str 转为 date。

    dataclass 不会自动做类型转换。BacktestConfig 在 async job 路径里
    是从 dict 重建（dict 里的日期是 str），需要兜底；同步路径传 date
    直接返回。
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"{field_name} 字符串 '{value}' 无法解析为 ISO 日期（YYYY-MM-DD）: {exc}"
            ) from exc
    raise TypeError(
        f"{field_name} 必须是 date / datetime / ISO str，传入：{type(value).__name__}"
    )


@dataclass
class BacktestConfig:
    """回测配置。

    Attributes:
        strategy_name:    策略名（STRATEGY_REGISTRY 的 key）
        start_date:       起始日期（含）
        end_date:         结束日期（含）
        symbols:          股票池（list of 6 位 code，不带交易所前缀）
                          与 universe 互斥（symbols 优先）
        universe:         池子 preset（"hs300" / "csi500" / "csi1000" / "gem" / "star" / "all_a"）
        initial_capital:  初始资金（元，默认 1_000_000）
        top_n:            持仓数量（默认 20）
        rebalance_freq:   调仓频率（daily / weekly / monthly，默认 weekly）
        commission_rate:  手续费率（双边，默认 0.0003 = 万分之 3，含规费）
        stamp_tax_rate:   印花税率（卖出单边，默认 0.001 = 千分之 1，A 股标准）
        slippage_rate:    滑点率（双边，默认 0.001 = 千分之 1，建仓/平仓各扣）
        benchmark:        基准代码（"000300.SH"=沪深 300 / "000905.SH"=中证 500，
                          None=不计算超额收益）
    """

    strategy_name: str
    start_date: date
    end_date: date
    symbols: list[str] = field(default_factory=list)
    universe: str | None = None
    initial_capital: float = 1_000_000.0
    top_n: int = 20
    rebalance_freq: str = "weekly"
    # P2.4 交易成本模型
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.001
    # P2.7 基准对比
    benchmark: str | None = None

    def __post_init__(self) -> None:
        # date 字段：接受 date | str | datetime，统一转 date。
        # 异步 job 里 BacktestConfig(**config_dict) 拿到的是 JSON 反序列化后的 str
        # （config_to_dict 把 date.isoformat() 了），需在 dataclass 层兜底。
        self.start_date = _coerce_date(self.start_date, "start_date")
        self.end_date = _coerce_date(self.end_date, "end_date")
        if self.rebalance_freq not in VALID_FREQS:
            raise ValueError(
                f"rebalance_freq 必须是 {VALID_FREQS} 之一，传入：{self.rebalance_freq}"
            )
        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date 必须早于 end_date：{self.start_date} vs {self.end_date}"
            )
        if self.top_n <= 0:
            raise ValueError(f"top_n 必须 > 0，传入：{self.top_n}")
        if not self.symbols and not self.universe:
            raise ValueError("symbols 和 universe 至少指定一个")
        if self.strategy_name not in STRATEGY_REGISTRY:
            raise KeyError(
                f"未知策略：{self.strategy_name}。可选：{list(STRATEGY_REGISTRY.keys())}"
            )
        # P2.6 universe 校验
        if self.universe:
            from data_pipeline.universes import is_valid
            if not is_valid(self.universe):
                from data_pipeline.universes import list_universes
                valid = [u["preset"] for u in list_universes()]
                raise ValueError(f"universe 不在白名单：{self.universe}。可选：{valid}")
        # P2.4 成本模型校验
        if not 0 <= self.commission_rate < 0.01:
            raise ValueError(f"commission_rate 应在 [0, 0.01) 区间，传入：{self.commission_rate}")
        if not 0 <= self.stamp_tax_rate < 0.05:
            raise ValueError(f"stamp_tax_rate 应在 [0, 0.05) 区间，传入：{self.stamp_tax_rate}")
        if not 0 <= self.slippage_rate < 0.05:
            raise ValueError(f"slippage_rate 应在 [0, 0.05) 区间，传入：{self.slippage_rate}")
        # P2.7 benchmark 校验
        if self.benchmark is not None and not (
            self.benchmark.endswith(".SH") or self.benchmark.endswith(".SZ")
        ):
            raise ValueError(
                f"benchmark 须以 .SH / .SZ 结尾（指数代码），传入：{self.benchmark}"
            )


# ── 结果 ──────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    """回测结果。

    Attributes:
        config:            入参配置
        returns:           日收益序列
        nav:               净值序列（含起点 1.0）
        metrics:           全部指标 dict
        rebalance_dates:   调仓日列表
        trading_dates:     实际交易日序列（P2.8 修复：之前未填充）
        trades:            调仓记录 list[dict]（date, sold, bought）
        skipped_symbols:   拉数据失败/数据不足被跳过的股票
        cost_summary:      交易成本汇总（P2.4：commission / stamp_tax / slippage 各自累计）
        benchmark:         基准 dict（P2.7：{code, returns, nav, alpha, beta}），
                          未指定 benchmark 时为 None
    """

    config: BacktestConfig
    returns: list[float] = field(default_factory=list)
    nav: list[float] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    rebalance_dates: list[date] = field(default_factory=list)
    trading_dates: list[date] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    skipped_symbols: list[str] = field(default_factory=list)
    cost_summary: dict[str, float] = field(default_factory=dict)
    benchmark: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": {
                "strategy_name": self.config.strategy_name,
                "symbols": self.config.symbols,
                "start_date": self.config.start_date.isoformat(),
                "end_date": self.config.end_date.isoformat(),
                "initial_capital": self.config.initial_capital,
                "top_n": self.config.top_n,
                "rebalance_freq": self.config.rebalance_freq,
                "commission_rate": self.config.commission_rate,
                "stamp_tax_rate": self.config.stamp_tax_rate,
                "slippage_rate": self.config.slippage_rate,
                "benchmark": self.config.benchmark,
            },
            "metrics": self.metrics,
            "rebalance_dates": [d.isoformat() for d in self.rebalance_dates],
            "trading_dates": [d.isoformat() for d in self.trading_dates],
            "trades": self.trades,
            "skipped_symbols": self.skipped_symbols,
            "nav": self.nav,  # 净值序列（含起点 1.0，长度 = n_periods+1）
            "nav_points": len(self.nav),
            "returns_points": len(self.returns),
            "initial_capital": self.config.initial_capital,  # 透传便于前端展示
            "cost_summary": self.cost_summary,
            "benchmark": self.benchmark,
        }


# ── 默认股票池：沪深 300 ─────────────────────────────────────

def get_hs300_symbols() -> list[str]:
    """沪深 300 成分股 code 列表（经 DataRouter → akshare provider）。

    返回的 code 已归一为 6 位字符串。
    失败时返 []，由调用方判空处理。
    """
    router = get_router()
    try:
        result = router.route("get_index_constituents", symbol="000300")
        if inspect.iscoroutine(result):
            df = asyncio.run(result)
        else:
            df = result
        codes = df["品种代码"].astype(str).str.zfill(6).tolist()
        logger.info("沪深 300 成分股：%d 只", len(codes))
        return codes
    except Exception as exc:
        logger.error("拉沪深 300 失败: %s", exc)
        return []


# ── 引擎 ──────────────────────────────────────────────────────

class BacktestEngine:
    """回测引擎（同步 + async 双入口）。

    异步入口：`run_async(config)`，内部用 asyncio.to_thread 包装 sync 拉数据。
    同步入口：`run(config)`，直接调 sync 拉数据。
    """

    def __init__(self, router: DataRouter | None = None) -> None:
        self.router = router

    # ── 拉单只股票日 K 线 ─────────────────────────────────────

    @staticmethod
    def _normalize_rows(rows: Any) -> list[dict[str, Any]]:
        """把 normalize_response 输出归一为 list[{date, close}]。"""
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            d = r.get("date") or r.get("Date") or r.get("日期")
            c = r.get("close") or r.get("Close") or r.get("收盘")
            if d is None or c is None:
                continue
            try:
                c_val = float(c)
            except (TypeError, ValueError):
                continue
            if isinstance(d, str):
                try:
                    d_val = datetime.fromisoformat(d).date()
                except ValueError:
                    continue
            elif isinstance(d, datetime):
                d_val = d.date()
            elif isinstance(d, date):
                d_val = d
            else:
                continue
            out.append({"date": d_val, "close": c_val})
        out.sort(key=lambda b: b["date"])
        return out

    def _fetch_bars_sync(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        """同步拉单只股票日 K 线（自动判断 router.route 同步/异步）。

        同步分支：直接用返回结果。
        异步分支：用 asyncio.run 在新 event loop 跑，避免 pytest 残留 loop 卡死。
        """
        if self.router is None:
            raise RuntimeError("BacktestEngine 未注入 DataRouter")
        result = self.router.route(
            "get_daily_bars",
            symbol=symbol,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        if inspect.iscoroutine(result):
            # 每次新 event loop（避免外层有 running loop 卡死）
            raw = asyncio.run(self._await_result(result))
        else:
            raw = result
        rows = normalize_response("get_daily_bars", raw)
        normalized = self._normalize_rows(rows)
        logger.debug("_fetch_bars_sync %s: raw_type=%s, rows=%d, normalized=%d",
                     symbol, type(raw).__name__,
                     len(rows) if isinstance(rows, list) else -1,
                     len(normalized))
        return normalized

    @staticmethod
    async def _await_result(coro: Any) -> Any:
        return await coro

    async def _fetch_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        if self.router is None:
            raise RuntimeError("BacktestEngine 未注入 DataRouter")
        raw = await self.router.route(
            "get_daily_bars",
            symbol=symbol,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        rows = normalize_response("get_daily_bars", raw)
        return self._normalize_rows(rows)

    # ── 选调仓日 ────────────────────────────────────────────

    @staticmethod
    def _pick_rebalance_dates(
        trading_dates: list[date], freq: str
    ) -> list[date]:
        """从交易日序列中按频率选调仓日。

        - daily:   每个交易日
        - weekly:  每周第一个交易日（按 ISO week 判定）
        - monthly: 每月第一个交易日
        """
        if freq == "daily":
            return list(trading_dates)
        seen: set[Any] = set()
        out: list[date] = []
        for d in trading_dates:
            if freq == "weekly":
                key = d.isocalendar().year, d.isocalendar().week
            else:  # monthly
                key = d.year, d.month
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
        return out

    # ── 单日调仓 ────────────────────────────────────────────

    def _rebalance_at(
        self,
        idx: int,  # 当前调仓日在 trading_dates 中的索引
        trading_dates: list[date],
        bars_by_symbol: dict[str, list[dict[str, Any]]],
        strategy: Strategy,
        current_holdings: set[str],
        top_n: int,
    ) -> tuple[set[str], dict[str, Any]]:
        """在调仓日 idx 执行调仓，返 (新持仓集合, 调仓记录)。"""
        target_date = trading_dates[idx]
        scores: list[tuple[str, float]] = []
        for sym, bars in bars_by_symbol.items():
            # 找 target_date 在 bars 中的索引
            bar_idx = None
            for j, b in enumerate(bars):
                if b["date"] >= target_date:
                    bar_idx = j
                    break
            if bar_idx is None or bar_idx < strategy.min_lookback():
                continue
            s = strategy.score(bars, bar_idx)
            if s is None:
                continue
            try:
                f = float(s)
            except (TypeError, ValueError):
                continue
            if math_isnan(f):
                continue
            scores.append((sym, f))
        # 按分数降序，取 top N（分数 > 0）
        scores.sort(key=lambda x: x[1], reverse=True)
        new_holdings: set[str] = set()
        for sym, s in scores:
            if len(new_holdings) >= top_n:
                break
            if s > 0:  # 分数为 0 或负 → 不持仓
                new_holdings.add(sym)
        # 调仓记录
        sold = sorted(current_holdings - new_holdings)
        bought = sorted(new_holdings - current_holdings)
        record = {
            "date": target_date.isoformat(),
            "sold": sold,
            "bought": bought,
            "scores_n": len(scores),
            "selected_n": len(new_holdings),
        }
        return new_holdings, record

    # ── 引擎主入口（同步） ────────────────────────────────────

    def run(self, config: BacktestConfig) -> BacktestResult:
        """同步执行回测。"""
        # 0. P2.6 universe 解析：symbols 优先，无 symbols 时按 universe preset 拉成分股
        from data_pipeline.universes import UniverseError, resolve_universe
        symbols: list[str] = list(config.symbols)
        if not symbols and config.universe:
            try:
                coro = resolve_universe(config.universe, router=self.router)
                # resolve_universe 是 async def；在同步 run() 里用 asyncio.run 跑
                symbols = asyncio.run(coro)
                logger.info(
                    "universe %s 解析完成: %d 只标的", config.universe, len(symbols),
                )
            except UniverseError as exc:
                raise RuntimeError(f"universe 解析失败：{exc}") from exc
        # 1. 拉所有股票日 K 线
        bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
        skipped: list[str] = []
        for sym in symbols:
            try:
                bars = self._fetch_bars_sync(sym, config.start_date, config.end_date)
            except Exception as exc:
                logger.warning("拉 %s 日 K 失败: %s: %s", sym, type(exc).__name__, exc)
                skipped.append(sym)
                continue
            if len(bars) < 30:  # 数据太少，没法算指标
                logger.warning("拉 %s 日 K 不足 30 根: %d 根", sym, len(bars))
                skipped.append(sym)
                continue
            bars_by_symbol[sym] = bars
        if not bars_by_symbol:
            raise RuntimeError("所有股票拉数据均失败，回测无法进行")
        # 2. 交易日序列（取所有股票日期的并集 + 排序）
        trading_dates_set: set[date] = set()
        for bars in bars_by_symbol.values():
            for b in bars:
                trading_dates_set.add(b["date"])
        trading_dates = sorted(trading_dates_set)
        # 3. 选调仓日
        rebalance_dates = self._pick_rebalance_dates(trading_dates, config.rebalance_freq)
        # 4. 算每日收益
        strategy = get_strategy(config.strategy_name)
        holdings: set[str] = set()
        rebalance_set = set(rebalance_dates)
        returns: list[float] = []
        trades: list[dict[str, Any]] = []
        # P2.4 成本累计
        cost_commission_total = 0.0
        cost_stamp_total = 0.0
        cost_slippage_total = 0.0
        cost_total = 0.0
        for i, d in enumerate(trading_dates):
            is_rebalance = d in rebalance_set
            if is_rebalance and i > 0:  # 第 0 日不调仓（无持仓）
                holdings, rec = self._rebalance_at(
                    i, trading_dates, bars_by_symbol, strategy, holdings, config.top_n
                )
                trades.append(rec)
            # 当日收益 = ∑(weight_i × 持仓股 i 的 bar-to-bar 收益)
            day_ret = 0.0
            if i == 0 or not holdings:
                returns.append(0.0)
                continue
            weight = 1.0 / len(holdings) if holdings else 0.0
            for sym in holdings:
                bars = bars_by_symbol.get(sym)
                if bars is None:
                    continue
                # 找 d 和 d 的前一天在 bars 中的索引
                cur_idx = None
                for j, b in enumerate(bars):
                    if b["date"] == d:
                        cur_idx = j
                        break
                if cur_idx is None or cur_idx == 0:
                    continue
                prev_idx = cur_idx - 1
                c_cur = bars[cur_idx]["close"]
                c_prev = bars[prev_idx]["close"]
                if c_prev <= 0:
                    continue
                day_ret += weight * (c_cur / c_prev - 1.0)
            # P2.4 调仓日扣交易成本
            if is_rebalance and i > 0 and trades:
                rec = trades[-1]
                sold_n = len(rec.get("sold") or [])
                bought_n = len(rec.get("bought") or [])
                # turnover 比例 = 实际换手 / 最大可能换手
                turnover = (sold_n + bought_n) / (2.0 * max(config.top_n, 1))
                # 双向手续费 + 双向滑点 + 卖出单边印花税
                cost_rate = turnover * (
                    2 * config.commission_rate
                    + 2 * config.slippage_rate
                    + config.stamp_tax_rate
                )
                if cost_rate > 0:
                    cost_commission_total += turnover * 2 * config.commission_rate
                    cost_stamp_total += turnover * config.stamp_tax_rate
                    cost_slippage_total += turnover * 2 * config.slippage_rate
                    cost_total += cost_rate
                    day_ret -= cost_rate
            returns.append(day_ret)
        # 5. 指标
        nav = nav_curve(returns)
        metrics = compute_metrics(returns)
        # 6. P2.4 成本汇总
        cost_summary = {
            "commission_rate": config.commission_rate,
            "stamp_tax_rate": config.stamp_tax_rate,
            "slippage_rate": config.slippage_rate,
            "total_cost_pct": cost_total,
            "commission_pct": cost_commission_total,
            "stamp_tax_pct": cost_stamp_total,
            "slippage_pct": cost_slippage_total,
        }
        # 7. P2.7 基准对比
        benchmark_data: dict[str, Any] | None = None
        if config.benchmark:
            benchmark_data = self._compute_benchmark(
                config.benchmark, trading_dates, returns, nav
            )
        # P2.8 修复：填 trading_dates 字段
        return BacktestResult(
            config=config,
            returns=returns,
            nav=nav,
            metrics=metrics,
            rebalance_dates=rebalance_dates,
            trading_dates=trading_dates,
            trades=trades,
            skipped_symbols=skipped,
            cost_summary=cost_summary,
            benchmark=benchmark_data,
        )

    def _compute_benchmark(
        self,
        benchmark_code: str,
        trading_dates: list[date],
        strat_returns: list[float],
        strat_nav: list[float],
    ) -> dict[str, Any] | None:
        """P2.7：拉基准指数日 K → 算 alpha/beta/correlation。

        Args:
            benchmark_code:  指数代码（"000300.SH" 形式）
            trading_dates:   策略交易日序列
            strat_returns:   策略日收益序列
            strat_nav:       策略净值序列

        Returns:
            dict 含 {code, returns, nav, alpha, beta, correlation, n_obs}；拉数据失败返 None。
        """
        if not self.router:
            logger.warning("无 router，benchmark 跳过: %s", benchmark_code)
            return None
        # 解析 code：000300.SH → "000300"
        code = benchmark_code.split(".")[0]
        try:
            raw = self.router.route(
                "get_index_daily_bars",
                symbol=code,
                start_date=trading_dates[0].isoformat() if trading_dates else None,
                end_date=trading_dates[-1].isoformat() if trading_dates else None,
            )
            # 与 _fetch_bars_sync 一致：coro 用 asyncio.run 在新 loop 跑
            if inspect.iscoroutine(raw):
                bars = asyncio.run(self._await_result(raw))
            else:
                bars = raw
        except Exception as exc:
            logger.warning("拉 benchmark %s 失败: %s: %s", benchmark_code, type(exc).__name__, exc)
            return None
        if not bars or len(bars) < 2:
            logger.warning("benchmark %s 数据不足: %d 根", benchmark_code, len(bars) if bars else 0)
            return None
        # 标准化 bars 为 [{date, close}]
        bench_norm: list[dict[str, Any]] = []
        for b in bars:
            d = b.get("date")
            c = b.get("close")
            if d is None or c is None or c <= 0:
                continue
            # date 可能是 "2026-06-01" / "20260601" 格式
            if isinstance(d, str):
                if "-" in d:
                    d_obj = date.fromisoformat(d)
                else:
                    d_obj = date(
                        int(d[:4]), int(d[4:6]), int(d[6:8])
                    )
            else:
                d_obj = d
            bench_norm.append({"date": d_obj, "close": float(c)})
        bench_norm.sort(key=lambda x: x["date"])
        # 对齐到 trading_dates：按 date 匹配
        date_to_idx = {d: i for i, d in enumerate(trading_dates)}
        bench_returns: list[float] = []
        bench_nav: list[float] = []
        bench_returns_aligned: list[float] = []
        prev_close = None
        nav = 1.0
        bench_nav.append(nav)
        for b in bench_norm:
            r = ((b["close"] / prev_close) - 1.0) if prev_close else 0.0
            prev_close = b["close"]
            bench_returns.append(r)
            nav *= 1.0 + r
            bench_nav.append(nav)
            # 对齐：仅在 trading_dates 出现的日期进入对齐序列
            if b["date"] in date_to_idx:
                bench_returns_aligned.append(r)
        if len(bench_returns_aligned) < 2:
            logger.warning("benchmark %s 对齐后数据不足: %d", benchmark_code, len(bench_returns_aligned))
            return None
        # alpha/beta
        ab = alpha_beta(strat_returns, bench_returns_aligned)
        # 基准自身 metrics
        bench_metrics = compute_metrics(bench_returns_aligned)
        return {
            "code": benchmark_code,
            "returns": bench_returns_aligned,
            "nav": [1.0] + [1.0] * len(bench_returns_aligned),  # 简化：不传完整 nav
            "metrics": bench_metrics,
            "alpha": ab["alpha"],
            "beta": ab["beta"],
            "correlation": ab["correlation"],
            "n_obs": ab["n_obs"],
        }

    # ── 引擎主入口（异步） ────────────────────────────────────

    async def run_async(self, config: BacktestConfig) -> BacktestResult:
        return await asyncio.to_thread(self.run, config)


# ── 小工具 ────────────────────────────────────────────────────

def math_isnan(x: float) -> bool:
    """轻量 NaN 判断（不依赖 math 模块导入）。"""
    return x != x
