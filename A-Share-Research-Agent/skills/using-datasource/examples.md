# datasource 使用示例集

可直接运行的端到端示例。前置：`pip install -e ".[pro,tdx]"`（最小运行只需 `pip install -e .`）。

## 1. 基础：日 K 线

```python
import asyncio
from datasource import get_source

async def main():
    source = get_source()
    df = await source.route("get_daily_bars", "600519", "20250101", "20250701")
    # 返回 pd.DataFrame，列: date / open / high / low / close / volume ...
    print(df.head())
    print(f"共 {len(df)} 根 K 线，区间 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")

asyncio.run(main())
```

## 2. 实时行情（批量，注意单次 ≤20 只）

```python
async def quotes(source):
    symbols = ["600519", "000001", "300750"]
    data = await source.route("get_tencent_quote", symbols)
    # 返回 dict[symbol -> {name, current, open, high, low, volume, ...}]
    for sym, q in data.items():
        print(f"{q['name']}({sym}) 现价 {q['current']}")
    # 超过 20 只时自行分批：
    batch_all = [f"60{str(i).zfill(4)}" for i in range(40)]
    for i in range(0, len(batch_all), 20):
        part = await source.route("get_tencent_quote", batch_all[i:i + 20])
```

## 3. 个股研究快照（多 API 组合 + 单票失败不阻断）

```python
import logging
from datasource import DataFetchError

logger = logging.getLogger(__name__)

async def research_snapshot(source, symbol: str) -> dict:
    """组合多端点；单端点失败记录后继续，不让一个源拖垮整体。"""
    snapshot: dict = {"symbol": symbol}

    async def safe(key, method, *args):
        try:
            snapshot[key] = await source.route(method, *args)
        except DataFetchError as e:
            logger.warning("%s 获取失败: %s", method, e.summary()["last_error"])
            snapshot[key] = None

    await safe("bars", "get_daily_bars", symbol, "20250101", "20250701")
    await safe("fundamentals", "get_stock_fundamentals", symbol)
    await safe("news", "get_stock_news", symbol)
    await safe("sentiment", "get_stock_sentiment", symbol)
    return snapshot
    # 注意：news 为空 list 是合法结果（今日无新闻），不要当作失败
```

## 4. 控制路由行为：强制源 / strict / 超时 / 绕过缓存

```python
# 强制走 baostock（不走 fallback）——回测拉历史时推荐
df = await source.route("get_daily_bars", "600519", "20250101", "20250701", provider="baostock")

# strict：首个失败立即抛（快速失败场景）
df = await source.route("get_daily_bars", "600519", "20250101", "20250701", strict=True)

# 长耗时端点放宽超时；实时数据绕过缓存
news = await source.route("get_news_flash", timeout=20, use_cache=False)
```

## 5. 异常处理与结构化诊断

```python
from datasource import DataFetchError

try:
    df = await source.route("get_daily_bars", "600519", "20250101", "20250701")
except DataFetchError as e:
    diag = e.summary()
    # diag = {
    #   "method": "get_daily_bars",
    #   "attempted_providers": ["baostock", "akshare", "tushare", "sina"],
    #   "last_error": "...", "last_error_type": "TimeoutError",
    #   "attempted_details": [{provider, error_type, latency_ms, resilience_strategy, ...}, ...],
    # }
    for d in diag["attempted_details"]:
        print(f"  {d['provider']}: {d['error_type']} - {d['error_message']}")
    raise  # 绝不静默返回假数据
```

## 6. 并发批量拉取（信号量限流）

```python
async def batch_bars(source, symbols: list[str], start: str, end: str) -> dict:
    """批量拉日线；用信号量控制并发，避免压垮数据源。"""
    sem = asyncio.Semaphore(6)
    results: dict = {}

    async def one(sym):
        async with sem:
            try:
                results[sym] = await source.route("get_daily_bars", sym, start, end)
            except DataFetchError as e:
                logger.warning("拉取 %s 失败: %s", sym, e.summary()["last_error"])
                results[sym] = None

    await asyncio.gather(*(one(s) for s in symbols))
    return results
    # 提示：baostock 单连接已串行化，主力源为 baostock 时调大信号量也无吞吐收益
```

## 7. 观测：健康度 / 缓存 / 可用方法

```python
source = get_source()
print(len(source.available_methods()))     # 89
h = source.health()
print(f"总调用 {h['calls_total']}，成功率 {h['calls_success_rate']:.1%}")
for ep in h["endpoints"][:5]:
    print(f"  {ep['method']}:{ep['provider']} 成功率 {ep['success_rate']:.0%} p95={ep['p95_latency_ms']}ms")
print(source.cache_stats().to_dict())
```

## 8. 运行时接入未注册端点

```python
# tushare 已实现 get_index_daily_bars 但未入路由表，可运行时接入：
source.register_method("get_index_bars_tushare", "tushare", "get_index_daily_bars")
df = await source.route("get_index_bars_tushare", "000300.SH", "20250101", "20250701")
```

## 9. 新增 Provider 完整模板

```python
# datasource/providers/my_source.py
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd

from datasource.errors import ProviderError
from datasource.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("my_source")
class MySourceProvider:
    """示例 Provider。"""

    @property
    def name(self) -> str:
        return "my_source"

    async def health_check(self) -> bool:
        return True  # 检查依赖可用性（不发真实请求）

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE
        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "my_source" in provs)

    async def get_daily_bars(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """同步第三方库必须走 asyncio.to_thread；失败抛 ProviderError 并保留异常链。"""
        try:
            def _fetch() -> pd.DataFrame:
                # rows = my_sync_sdk.query(symbol, start_date, end_date)
                # return pd.DataFrame(rows)
                ...
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            raise ProviderError(
                f"my_source get_daily_bars 失败: {exc}",
                provider="my_source", method="get_daily_bars", cause=exc,
            ) from exc
```

然后两步接入：

```python
# datasource/providers/__init__.py → _PROVIDER_MODULES 追加 "my_source"
# datasource/method_table.py → 路由表追加（放链尾即为新 fallback 源）：
#   "get_daily_bars": {..., "my_source": "get_daily_bars"},
```
