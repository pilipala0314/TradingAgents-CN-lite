---
name: using-datasource
description: 指导正确使用本项目的 datasource 多源金融数据路由库——get_source/route 调用模式、89 个数据 API 选型、参数与返回值契约、多源 fallback 与异常处理、依赖配置、新增 provider 流程。Use when 需要获取 A 股行情、K线、基本面、资金流、涨停/龙虎榜、板块、新闻、舆情、宏观、港股/美股/北交所/期货数据，用户提到 datasource、get_source、route、数据源、行情接口，或编写集成/扩展 datasource 包的代码时。
---

# 使用 datasource 数据源库

`datasource/` 是本项目的独立多源金融数据路由库：**24 个 provider、89 个数据 API、自动注册、多源 fallback、韧性保护（重试/限流/熔断/隔离）、分层缓存**。所有数据获取都应通过本库的 `route()` 统一入口，不要直接调用底层 akshare/tushare/baostock。

**渐进式参考**（按需查阅，一级深度）：
- 完整 89 个 API 的参数/返回值/分类/签名 → [reference.md](reference.md)
- 可直接运行的端到端示例 → [examples.md](examples.md)

## 核心调用模式

```python
import asyncio
from datasource import get_source

async def main():
    source = get_source()  # 单例；首次调用触发全部 provider 注册与实例化

    # 路由调用：自动按健康度选源 + 失败自动 fallback + 缓存 + 韧性保护
    df = await source.route("get_daily_bars", "600519", "20250101", "20250701")

asyncio.run(main())
```

`route()` 完整签名与可选参数：

```python
await source.route(
    "get_daily_bars", "600519", "20250101", "20250701",  # method + 位置参数
    provider="baostock",  # 强制指定数据源（跳过 fallback）；默认 None=自动选源
    strict=True,          # True=任一失败即抛；默认 False=尝试全链后再抛
    timeout=20,           # 单端点超时秒数；默认 12（ROUTE_DEFAULT_TIMEOUT）
    use_cache=False,      # False=跳过缓存；默认 True（30s TTL）
)
```

**硬性规则**
- 所有数据方法都是 `async`，必须在事件循环内 `await`。
- 方法名以 `datasource/method_table.py` 为准；调用未注册方法抛 `ValueError`。
- 位置参数跨源兼容；**多源方法避免传 kwargs**（各源 kwargs 命名不完全一致，可能导致 fallback 源 `TypeError`）。
- **缓存返回共享引用**：命中缓存的 DataFrame/list 不要原地修改（会污染后续命中者）；需要加工先 `df.copy()`。
- 缓存按 `provider` 隔离：强制指定源的结果不会命中默认链缓存（不同源口径可能不同）。

## 选型速查（按场景）

写代码前，先在下表定位方法，再去 [reference.md](reference.md) 核对精确参数与返回字段。

| 场景 | 方法 | 示例参数 |
|---|---|---|
| 日 K 线 | `get_daily_bars` | `"600519", "20250101", "20250701"` |
| 分钟线 | `get_minute_bars` | `"600519", "20250714 09:30:00", "20250718 15:00:00"` |
| 实时行情（批量） | `get_tencent_quote` | `["600519", "000001"]` |
| 指数日 K | `get_index_daily_bars` | `"000001", "20250101", "20250701"` |
| 股票列表 / 个股信息 | `get_stock_list` / `get_stock_individual_info` | — / `"600519"` |
| 基本面（PE/PB/ROE） | `get_stock_fundamentals` | `"600519"` |
| 财务三表 | `get_balance_sheet` / `get_income_statement` / `get_cashflow_statement` | `"600519"` |
| 资金流 | `get_market_fund_flow` / `get_individual_fund_flow` / `get_northbound_flow` | — / `"600519"` / — |
| 涨停生态 | `get_limit_up_pool` / `get_limit_down_pool` / `get_bomb_board_pool` | —（默认当日） |
| 龙虎榜 | `get_dragon_tiger_list` / `get_stock_lhb` | — / `"600519"` |
| 板块 | `get_industry_board_list` / `get_sector_fund_flow` / `get_sector_leaders` | — / `"半导体"` |
| 新闻 / 快讯 / 公告 | `get_stock_news` / `get_news_flash` / `get_cninfo_announcements` | `"600519"` / — / `"600519"` |
| 宏观 | `get_macro_pmi` / `get_macro_cpi` / `get_macro_m2` / `get_macro_bond_yield` | — |
| 舆情 / 热门 | `get_stock_sentiment` / `get_hot_stocks` | `"600519"` / — |
| 研报 | `get_research_reports` / `get_research_chunks` / `search_research` | `"600519"` / `"600519"` / `"算力"` |
| 港 / 美 / 北交所 / 期货 | `get_hk_full_daily_bars` / `get_us_stock_daily_bars` / `get_bse_stock_daily_bars` / `get_futures_daily_bars` | `"00700"` / `"AAPL"` / `"430047"` / `"RB0"` |

其余 70+ 方法（业绩预告/解禁/分红/机构持仓/国际媒体/地缘政治等）见 [reference.md](reference.md)。

## 返回值契约（必须遵守）

- **K 线类**（`get_daily_bars` / `get_index_daily_bars` / `get_minute_bars`）：全源统一返回 `pd.DataFrame`，列含 `date / open / high / low / close / volume`。
- **实时行情**（`get_tencent_quote`）：`dict[symbol -> {name, open, current, high, low, volume, ...}]`。
- **新闻 / 公告 / 研报 / 情绪列表类**：`list[dict]`（字段见 reference.md）。
- **聚合类**（情绪评分/风险评分/快照）：`dict`。
- **空 `list` / 空 `DataFrame`**：事件类端点（新闻/公告/龙虎榜等）空结果是合法业务结果（如"今日无新闻"），**不要对空结果抛错**，按业务语义处理；选择器类端点（`get_industry_ranking` / `get_sector_fund_flow` / `get_stock_list` / `get_individual_fund_flow`）空结果=失败并自动继续 fallback，全链空则抛 `DataFetchError`。只有 `None` 一定被质量校验拒绝。
- `adjust` 复权参数统一值域：`"qfq"`（前复权，默认）/ `"hfq"`（后复权）/ `""`（不复权）。

## 异常处理

```python
from datasource import DataFetchError

try:
    df = await source.route("get_daily_bars", "600519", "20250101", "20250701")
except DataFetchError as e:
    # 仅当所有 provider 都失败才到这里（单源失败已自动 fallback）
    diag = e.summary()  # 结构化诊断：逐源失败原因、韧性策略、延迟
    logger.error("数据获取失败: %s", diag)
    raise  # 或明确降级，绝不静默返回假数据
```

- 不要捕获异常后静默返回假数据/占位值；向上传播或做明确的降级分支。
- `get_insider_transactions` 是契约占位端点，调用必抛 `NotImplementedError`，改用 `get_regulatory_penalties` / `get_dragon_tiger`。

## 依赖与配置

| 依赖 | 用途 | 安装 |
|---|---|---|
| akshare / pandas / httpx / requests | 核心（默认） | `pip install -e .` |
| baostock / tushare | 专业历史数据 | `pip install -e ".[pro]"`，tushare 需 `TUSHARE_TOKEN` 环境变量 |
| mootdx | 通达信行情（a_stock_data） | `pip install -e ".[tdx]"` |

可选依赖缺失时对应 provider 给出明确安装提示，不影响其他源。常用环境变量：`ROUTE_CACHE_TTL_SEC`（缓存 TTL）、`ROUTE_DEFAULT_TIMEOUT`（超时）、`ROUTE_CACHE_PERSISTENT=true`（文件缓存持久化）、`TUSHARE_TOKEN`。

**运行时注意事项**
- **baostock** 单全局连接已做进程级串行化，并发安全但吞吐受限——批量回测拉历史时用信号量控制并发。
- **akshare** 初始化时会 patch 全局 `requests.get`（反爬所需）；集成进大型应用时知悉该副作用。
- 批量行情单次上限 20 只（`get_tencent_quote`），超出会告警截断，需自行分批。

## 观测与自检

```python
source.available_methods()   # 全部可用方法名（89 个）
source.health()              # 各端点成功率/延迟/最近错误/24h 分桶
source.cache_stats()         # 缓存命中率
# 运行时接入未注册端点（见 reference.md 第六节）
source.register_method("my_method", "tushare", "get_index_daily_bars")
```

## 新增 Provider（3 步）

1. 新建 `datasource/providers/my_source.py`：类标 `@register_provider("my_source")`，实现 `name` 属性、`health_check()`、`supported_methods()`（从 `FULL_METHOD_TABLE` 推导）；数据方法为 `async def`，失败抛 `ProviderError(..., provider=..., method=..., cause=...)` 并 `raise ... from exc`。
2. 在 `datasource/providers/__init__.py` 的 `_PROVIDER_MODULES` 追加模块名。
3. 在 `datasource/method_table.py` 注册路由（链尾追加即成为新的 fallback 源）。

**编码约定**：同步库用 `asyncio.to_thread` 包装（禁止在 async 方法里直接调阻塞 IO）；单一全局连接的库加进程级锁；不在 provider 内做静默降级。完整模板见 [examples.md](examples.md)。
