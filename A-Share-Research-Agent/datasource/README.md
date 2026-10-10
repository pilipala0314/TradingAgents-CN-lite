# datasource — 独立数据源模块

## 概述

`datasource/` 是独立的数据源层，负责所有外部金融数据的获取、路由、韧性保护和缓存。
零上层项目依赖：整体复制本目录即可在其他项目独立运行。

**设计目标**：解耦、可扩展、稳定可靠。

## 快速使用

```python
from datasource import get_source

source = get_source()

# 路由调用（自动 fallback + 韧性保护 + 缓存）
df = await source.route("get_daily_bars", "600519", "20250101", "20250701")
quote = await source.route("get_tencent_quote", ["600519", "000001"])
```

## 架构

```
datasource/
├── __init__.py          # 公共 API
├── types.py             # 类型定义 (DataResult, MethodTable)
├── protocol.py          # ProviderProtocol 接口契约
├── errors.py            # 异常体系
├── registry.py          # @register_provider 自动注册
├── router.py            # DataSource 路由引擎
├── method_table.py      # 路由表（唯一真相源）
├── facade.py            # get_source() 单例入口
├── config.py            # 自包含配置（环境变量）
├── bootstrap.py         # 可选依赖检查/安装
├── call_logger.py       # 调用记录（本地 JSONL）
├── stats.py             # 端点健康度统计
├── resilience/          # 韧性中间件
│   ├── retry.py         # 指数退避重试
│   ├── rate_limit.py    # 令牌桶限流
│   ├── circuit_breaker.py # 熔断器
│   ├── bulkhead.py      # 并发隔离
│   └── policy.py        # 策略编排器
├── cache/               # 多级缓存
│   ├── memory.py        # L1 内存 LRU+TTL
│   ├── file.py          # L2 文件持久化
│   └── tiered.py        # L1+L2 分层
└── providers/           # 24 个数据源实现（见下）
    ├── _http.py         # 全局 httpx 连接池
    ├── _shared/         # 共享工具（东财节流/通达信/腾讯行情/日期格式）
    └── _impl/           # a_stock_data 内部端点实现
```

## 数据源一览（24 个 Provider）

| Provider | 类型 | 特点 | 需要 Token/依赖 |
|----------|------|------|-----------|
| **eastmoney** | 直连 HTTP API | 实时行情、资金流、板块排名 | 否 |
| **baostock** | 官方 SDK | 免费、历史 K 线、盈利能力指标 | `pip install baostock` |
| **tushare** | 官方 API | 专业级全量数据，真复权 | `TUSHARE_TOKEN` |
| **akshare** | 爬虫聚合 | 覆盖最广（涨停/龙虎榜/宏观/板块） | `pip install akshare` |
| a_stock_data | mootdx + 东财 | 通达信协议行情、五档盘口 | `pip install mootdx` |
| sina / netease | HTTP API | 行情 fallback 源 | 否 |
| news / cninfo / cninfo_news | HTTP API | 快讯、巨潮公告与监管动态 | 否 |
| sentiment / global_media / geopolitics / policy | HTTP API | 舆情、国际媒体、地缘、政策 | 否 |
| fundamentals_ext / financial_report | HTTP/akshare | 业绩预告、持仓、财务三表 | 否 |
| global_quote / hk_full / hk_southbound / us_stock / bse / bse_index / futures | 混合 | 多市场行情（港/美/北交所/期货） | 部分依赖 akshare |
| research_rag | HTTP + 内存索引 | 研报 chunks 与关键词检索 | 否 |

## Fallback 链示例（多源端点）

```
get_daily_bars:        baostock → akshare → tushare → sina
get_tencent_quote:     eastmoney → a_stock_data → sina → netease
get_stock_fundamentals: baostock → akshare → tushare
get_market_fund_flow:  akshare → tushare
get_individual_fund_flow: eastmoney → akshare
get_industry_ranking:  eastmoney → a_stock_data → sina
get_stock_news:        news → akshare → a_stock_data
```

单源端点（北交所/期货/港股/美股等）失败时直接抛 `DataFetchError`。

## 新增 Provider 指南（3 步）

### 1. 创建 Provider 文件

```python
# datasource/providers/my_source.py
from datasource.registry import register_provider
from datasource.errors import ProviderError

@register_provider("my_source")
class MySourceProvider:
    @property
    def name(self) -> str:
        return "my_source"

    async def health_check(self) -> bool:
        return True  # 检查依赖是否可用

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE
        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "my_source" in provs)

    async def get_daily_bars(self, symbol: str, **kwargs):
        # 实现数据获取逻辑，失败时抛 ProviderError
        ...
```

### 2. 注册到 providers/__init__.py 的 `_PROVIDER_MODULES`

### 3. 在 method_table.py 注册方法路由

完成！新 provider 自动参与 fallback 链、韧性保护、缓存。

## 韧性策略

默认开启（`ROUTE_DEFAULT_RESILIENCE=true`），每 provider 独立策略：

- **重试**：3 次，指数退避（0.5s 起），±25% 抖动
- **限流**：5 rps 令牌桶，burst=10
- **熔断**：窗口内 5 次失败触发，30s 恢复期
- **隔离**：单 provider 最多 10 并发

## 配置项（环境变量）

```env
ROUTE_DEFAULT_RESILIENCE=true    # 开启韧性
ROUTE_DEFAULT_TIMEOUT=12         # 单端点超时(秒)
ROUTE_CACHE_TTL_SEC=30           # 默认缓存TTL
ROUTE_CACHE_PERSISTENT=false     # true=内存+本地文件分层缓存
ROUTE_CACHE_QUALITY_CHECK=true   # 结果质量校验（拒绝 None；选择器类空结果=失败）
TUSHARE_TOKEN=your_token_here    # Tushare Token
DATASOURCE_AUTO_INSTALL=false    # 缺失依赖时允许自动 pip 安装
DATASOURCE_CALL_LOG_DIR=.call_logs # 调用日志目录
```

## 异常体系

```
DataSourceError (base)
├── ProviderError        — 单 provider 内部错误（网络/解析/接口缺失）
├── DataFetchError       — 所有 provider 均失败（含逐 provider 诊断明细）
└── ResilienceError      — 韧性层拒绝（熔断/限流/重试用尽）
```

`DataFetchError.summary()` 自动解包韧性包装，透出真实根因。
