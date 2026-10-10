# 数据 API 清单报告

> 生成日期：2026-08-26 ｜ 来源：静态解析 `datasource/` 源码（method_table + provider 签名）

## 一、总览

- **统一调用入口**：`from datasource import get_source`，然后 `await source.route("<method>", *args, **kwargs)`
- **API 总数**：**89** 个（核心高频 33 个 + 扩展低频 56 个）
- **Provider 数**：**24** 个（自动注册，多源端点自动 fallback + 按历史成功率自适应排序）
- **多源 fallback 端点**：14 个
- **返回值契约**：`pd.DataFrame` ｜ `list[dict]` ｜ `dict`（质量校验拒绝 `None`；选择器类端点空结果视为失败并继续 fallback；事件类端点空结果合法）
- **K 线契约已统一**：`get_daily_bars` / `get_index_daily_bars` / `get_minute_bars` 全源返回 `pd.DataFrame`；`adjust` 统一值域 `qfq`/`hfq`/`""`（baostock 兼容旧值域 1/2/3）
- **异常语义**：单源失败抛 `ProviderError`（路由层 fallback）；全部失败抛 `DataFetchError`（`.summary()` 透出根因）

```python
import asyncio
from datasource import get_source

async def main():
    source = get_source()
    df = await source.route("get_daily_bars", "600519", "20250101", "20250701")
    quote = await source.route("get_tencent_quote", ["600519", "000001"])

asyncio.run(main())
```

## 二、Provider 清单（24 个）

| Provider | 类型 | 依赖/凭证 | 实现文件 | 端点数 |
|---|---|---|---|---|
| `a_stock_data` | 通达信协议 + 东财 | pip install mootdx | providers/a_stock_data.py | 10 |
| `akshare` | AkShare 爬虫聚合 | pip install akshare | providers/akshare.py | 34 |
| `baostock` | 官方 SDK（历史 K 线/盈利指标） | pip install baostock | providers/baostock.py | 4 |
| `bse` | 北交所行情 | 部分依赖 akshare | providers/bse.py | 2 |
| `bse_index` | 北证 50 指数 | 部分依赖 akshare | providers/bse.py | 1 |
| `cninfo` | 巨潮资讯公告 | 无（HTTP） | providers/cninfo.py | 1 |
| `cninfo_news` | 巨潮新闻/监管动态 | 无（HTTP） | providers/cninfo_news.py | 3 |
| `eastmoney` | 东财直连 HTTP | 无（HTTP） | providers/eastmoney.py | 4 |
| `financial_report` | 东财数据中心财务三表 | 无（HTTP） | providers/financial_report.py | 4 |
| `fundamentals_ext` | 业绩预告/持仓/分红/解禁 | 部分依赖 akshare | providers/fundamentals_ext.py | 5 |
| `futures` | 国内期货（新浪源） | 部分依赖 akshare | providers/futures.py | 3 |
| `geopolitics` | 地缘政治新闻与风险评分 | 无（HTTP） | providers/geopolitics.py | 4 |
| `global_media` | 华尔街见闻/CNBC/Yahoo/Reuters | 无（HTTP） | providers/global_media.py | 5 |
| `global_quote` | 全球指数实时行情 | 部分依赖 akshare | providers/global_quote.py | 1 |
| `hk_full` | 港股全量行情 | 部分依赖 akshare | providers/hk_full.py | 3 |
| `hk_southbound` | 港股通南向资金 | 部分依赖 akshare | providers/hk_southbound.py | 2 |
| `netease` | 网易财经 HTTP | 无（HTTP） | providers/netease.py | 3 |
| `news` | 财联社/东财新闻流 | 无（HTTP） | providers/news.py | 4 |
| `policy` | 证监会/央行/政策新闻 | 无（HTTP） | providers/policy.py | 4 |
| `research_rag` | 研报 RAG（内存索引） | 无（HTTP + akshare 兜底） | providers/research_rag.py | 2 |
| `sentiment` | 舆情/人气榜 | 无（HTTP） | providers/sentiment.py | 3 |
| `sina` | 新浪财经 HTTP | 无（HTTP） | providers/sina.py | 8 |
| `tushare` | 官方 API（专业级全量） | pip install tushare + TUSHARE_TOKEN | providers/tushare.py | 7 |
| `us_stock` | 美股基础行情 | 部分依赖 akshare | providers/us_stock.py | 3 |

## 三、多源 Fallback 链（14 个端点）

| 方法 | Provider 链（声明顺序，运行期按成功率/延迟自适应排序） |
|---|---|
| `get_analyst_consensus` | fundamentals_ext → akshare |
| `get_daily_bars` | baostock → akshare → tushare → sina |
| `get_global_macro_news` | news → akshare |
| `get_individual_fund_flow` | eastmoney → akshare |
| `get_industry_ranking` | eastmoney → a_stock_data → sina |
| `get_kline` | a_stock_data → sina |
| `get_market_fund_flow` | akshare → tushare |
| `get_minute_bars` | akshare → sina |
| `get_northbound_flow` | akshare → tushare |
| `get_stock_fundamentals` | baostock → akshare → tushare |
| `get_stock_info` | a_stock_data → sina |
| `get_stock_news` | news → akshare → a_stock_data |
| `get_tencent_quote` | eastmoney → a_stock_data → sina → netease |

## 四、分类 API 明细

### 证券主数据（4 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_stock_list` | 全市场股票代码列表 | `route("get_stock_list")` | akshare | `pd.DataFrame` |
| `get_stock_individual_info` | 个股基本信息（总市值/流通市值/行业等） | `route("get_stock_individual_info", '600519')` | akshare | `pd.DataFrame` |
| `get_stock_info` | 个股基础资料（名称/当日 OHLCV） | `route("get_stock_info", '600519')` | a_stock_data → sina | `dict[str, Any]` |
| `get_index_constituents` | 指数成分股（如沪深300/中证500） | `route("get_index_constituents", '000300')` | akshare | `pd.DataFrame` |

### 行情（K线/实时）（7 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_daily_bars` | 个股日线行情（前复权），返回 DataFrame | `route("get_daily_bars", '600519', '20250101', '20250701')` | baostock → akshare → tushare → sina | `pd.DataFrame` |
| `get_minute_bars` | 分钟线行情（1/5/15/30/60分钟） | `route("get_minute_bars", '600519', '20250714 09:30:00', '20250718 15:00:00')` | akshare → sina | `pd.DataFrame` |
| `get_kline` | 个股 K 线（通达信/新浪源） | `route("get_kline", '600519')` | a_stock_data → sina | `list[dict[str, Any]]` |
| `get_quote_5level` | 五档盘口实时行情 | `route("get_quote_5level", '600519')` | a_stock_data | `dict[str, Any] | None` |
| `get_tencent_quote` | 腾讯实时行情快照，支持单个/批量代码 | `route("get_tencent_quote", '600519')` | eastmoney → a_stock_data → sina → netease | `dict` |
| `get_index_daily_bars` | 指数日 K 线（沪深300/中证500 等基准） | `route("get_index_daily_bars", '000001', '20250101', '20250701')` | sina | `pd.DataFrame` |
| `get_global_quote` | 全球指数实时行情 | `route("get_global_quote")` | global_quote | `dict[str, dict[str, Any]]` |

### 资金流向（4 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_market_fund_flow` | 大盘资金流向（主力/超大单/大单） | `route("get_market_fund_flow")` | akshare → tushare | `pd.DataFrame` |
| `get_northbound_flow` | 北向资金净流入 | `route("get_northbound_flow")` | akshare → tushare | `pd.DataFrame` |
| `get_individual_fund_flow` | 个股资金流向 | `route("get_individual_fund_flow", '600519')` | eastmoney → akshare | `pd.DataFrame` |
| `get_fund_flow_minute` | 分钟级资金流 | `route("get_fund_flow_minute", '600519')` | a_stock_data | `list[dict[str, Any]]` |

### 涨停生态（3 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_limit_up_pool` | 涨停股池（默认当日） | `route("get_limit_up_pool")` | akshare | `pd.DataFrame` |
| `get_limit_down_pool` | 跌停股池（默认当日） | `route("get_limit_down_pool")` | akshare | `pd.DataFrame` |
| `get_bomb_board_pool` | 炸板股池（默认当日） | `route("get_bomb_board_pool")` | akshare | `pd.DataFrame` |

### 龙虎榜（3 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_dragon_tiger_list` | 龙虎榜明细（默认当日） | `route("get_dragon_tiger_list")` | akshare | `pd.DataFrame` |
| `get_dragon_tiger` | 通达信龙虎榜数据 | `route("get_dragon_tiger", '600519')` | a_stock_data | `dict[str, Any]` |
| `get_stock_lhb` | 个股龙虎榜明细（近 N 日） | `route("get_stock_lhb", '600519')` | akshare | `pd.DataFrame` |

### 板块数据（10 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_concept_board_list` | 概念板块列表 | `route("get_concept_board_list")` | akshare | `pd.DataFrame` |
| `get_industry_board_list` | 行业板块列表 | `route("get_industry_board_list")` | akshare | `pd.DataFrame` |
| `get_concept_board_components` | 概念板块成分股 | `route("get_concept_board_components", '锂电池')` | akshare | `pd.DataFrame` |
| `get_industry_board_components` | 行业板块成分股 | `route("get_industry_board_components", '半导体')` | akshare | `pd.DataFrame` |
| `get_concept_blocks` | 通达信概念板块行情 | `route("get_concept_blocks", '锂电池')` | a_stock_data | `dict[str, Any]` |
| `get_industry_ranking` | 行业板块涨幅排行 | `route("get_industry_ranking")` | eastmoney → a_stock_data → sina | `list[dict]` |
| `get_sector_recent_bars` | 板块近期行情 K 线 | `route("get_sector_recent_bars", '半导体')` | akshare | `pd.DataFrame` |
| `get_sector_fund_flow` | 板块资金流 | `route("get_sector_fund_flow", '半导体')` | akshare → sina | `pd.DataFrame` |
| `get_sector_leaders` | 板块领涨股 | `route("get_sector_leaders", '半导体')` | akshare | `pd.DataFrame` |
| `get_sector_event_signals` | 板块事件信号 | `route("get_sector_event_signals", '半导体')` | akshare | `list[dict[str, Any]]` |

### 新闻与事件（5 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_stock_news` | 个股新闻 | `route("get_stock_news", '600519')` | news → akshare → a_stock_data | `list[dict[str, Any]]` |
| `get_news_flash` | 财联社电报/快讯 | `route("get_news_flash")` | news | `list[dict[str, Any]]` |
| `get_global_macro_news` | 全球宏观新闻 | `route("get_global_macro_news")` | news → akshare | `list[dict[str, Any]]` |
| `get_global_news_aggregated` | 全球宏观新闻聚合（多源） | `route("get_global_news_aggregated")` | akshare | `list[dict[str, Any]]` |
| `get_global_market_news` | 全球市场新闻 | `route("get_global_market_news")` | global_media | `list[dict[str, Any]]` |

### 基本面与财务（5 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_stock_fundamentals` | 个股基本面指标（PE/PB/ROE 等） | `route("get_stock_fundamentals", '600519')` | baostock → akshare → tushare | `pd.DataFrame` |
| `get_finance_snapshot` | 个股财务快照 | `route("get_finance_snapshot", '600519')` | a_stock_data | `dict[str, Any] | None` |
| `get_balance_sheet` | 资产负债表 | `route("get_balance_sheet", '600519')` | financial_report | `pd.DataFrame` |
| `get_income_statement` | 利润表 | `route("get_income_statement", '600519')` | financial_report | `pd.DataFrame` |
| `get_cashflow_statement` | 现金流量表 | `route("get_cashflow_statement", '600519')` | financial_report | `pd.DataFrame` |

### 扩展基本面（9 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_earnings_forecast` | 业绩预告（东方财富） | `route("get_earnings_forecast", '600519')` | akshare | `pd.DataFrame` |
| `get_earnings_forecast_ext` | 扩展盈利预测 | `route("get_earnings_forecast_ext", '600519')` | fundamentals_ext | `list[dict[str, Any]]` |
| `get_analyst_consensus` | 分析师一致预期 | `route("get_analyst_consensus", '600519')` | fundamentals_ext → akshare | `dict[str, Any]` |
| `get_institution_holdings` | 机构持仓 | `route("get_institution_holdings", '600519')` | fundamentals_ext | `list[dict[str, Any]]` |
| `get_dividend_history` | 分红历史 | `route("get_dividend_history", '600519')` | fundamentals_ext | `list[dict[str, Any]]` |
| `get_lockup_calendar` | 解禁日历 | `route("get_lockup_calendar")` | fundamentals_ext | `list[dict[str, Any]]` |
| `get_research_reports` | 研报列表（个股/行业） | `route("get_research_reports", '600519')` | akshare | `list[dict[str, Any]]` |
| `get_regulatory_penalties` | 监管处罚记录 | `route("get_regulatory_penalties", '600519')` | akshare | `list[dict[str, Any]]` |
| `get_insider_transactions` | 内部人交易（A 股无 SEC 式披露，显式 NotImplementedError） | `route("get_insider_transactions", '600519')` | akshare | `pd.DataFrame` |

### 宏观数据（5 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_macro_pmi` | 中国 PMI 宏观指标 | `route("get_macro_pmi")` | akshare | `pd.DataFrame` |
| `get_macro_cpi` | 中国 CPI 宏观指标 | `route("get_macro_cpi")` | akshare | `pd.DataFrame` |
| `get_macro_m2` | M2 货币供应量 | `route("get_macro_m2")` | akshare | `pd.DataFrame` |
| `get_macro_bond_yield` | 国债收益率曲线（中美国债利差） | `route("get_macro_bond_yield")` | akshare | `pd.DataFrame` |
| `get_macro_indicators` | 宏观指标汇总 | `route("get_macro_indicators")` | policy | `dict[str, Any]` |

### 政策与监管（7 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_csrc_announcements` | 证监会公告 | `route("get_csrc_announcements")` | policy | `list[dict[str, Any]]` |
| `get_pboc_operations` | 央行公开市场操作 | `route("get_pboc_operations")` | policy | `list[dict[str, Any]]` |
| `get_policy_news` | 政策新闻 | `route("get_policy_news")` | policy | `list[dict[str, Any]]` |
| `get_cninfo_announcements` | 巨潮公告列表 | `route("get_cninfo_announcements", '600519')` | cninfo | `list[dict[str, Any]]` |
| `get_cninfo_announcements_full` | 巨潮全量公告 | `route("get_cninfo_announcements_full")` | cninfo_news | `list[dict[str, Any]]` |
| `get_cninfo_news` | 巨潮新闻资讯 | `route("get_cninfo_news")` | cninfo_news | `list[dict[str, Any]]` |
| `get_cninfo_regulatory` | 巨潮监管动态 | `route("get_cninfo_regulatory")` | cninfo_news | `list[dict[str, Any]]` |

### 舆情情绪（3 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_stock_sentiment` | 个股舆情情绪 | `route("get_stock_sentiment", '600519')` | sentiment | `dict[str, Any]` |
| `get_sector_sentiment` | 板块舆情情绪 | `route("get_sector_sentiment", '半导体')` | sentiment | `dict[str, Any]` |
| `get_hot_stocks` | 人气榜热门股 | `route("get_hot_stocks")` | sentiment | `list[dict[str, Any]]` |

### 国际媒体（4 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_wallstreet_news` | 华尔街见闻新闻 | `route("get_wallstreet_news")` | global_media | `list[dict[str, Any]]` |
| `get_cnbc_news` | CNBC 新闻 | `route("get_cnbc_news")` | global_media | `list[dict[str, Any]]` |
| `get_yahoo_finance_news` | Yahoo Finance 新闻 | `route("get_yahoo_finance_news", 'AAPL')` | global_media | `list[dict[str, Any]]` |
| `get_reuters_news` | Reuters 新闻 | `route("get_reuters_news")` | global_media | `list[dict[str, Any]]` |

### 地缘政治（4 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_geopolitics_news` | 地缘政治新闻 | `route("get_geopolitics_news")` | geopolitics | `list[dict[str, Any]]` |
| `get_sanctions_news` | 制裁相关新闻 | `route("get_sanctions_news")` | geopolitics | `list[dict[str, Any]]` |
| `get_trade_war_news` | 贸易战相关新闻 | `route("get_trade_war_news")` | geopolitics | `list[dict[str, Any]]` |
| `get_geopolitics_risk_score` | 地缘政治风险评分 | `route("get_geopolitics_risk_score")` | geopolitics | `dict[str, Any]` |

### 多市场（北交所/期货/港股/美股）（14 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_bse_stock_list` | 北交所股票列表 | `route("get_bse_stock_list")` | bse | `pd.DataFrame` |
| `get_bse_stock_daily_bars` | 北交所个股日线 | `route("get_bse_stock_daily_bars", '430047')` | bse | `pd.DataFrame` |
| `get_bse_index_daily_bars` | 北证指数日 K（北证50） | `route("get_bse_index_daily_bars", '899050')` | bse_index | `pd.DataFrame` |
| `get_futures_main_contracts` | 期货主力合约列表 | `route("get_futures_main_contracts")` | futures | `pd.DataFrame` |
| `get_futures_daily_bars` | 期货主力合约日线 | `route("get_futures_daily_bars", 'RB0')` | futures | `pd.DataFrame` |
| `get_futures_sector_spot` | 期货板块现货价 | `route("get_futures_sector_spot")` | futures | `pd.DataFrame` |
| `get_hk_southbound_flow` | 港股通南向资金 | `route("get_hk_southbound_flow")` | hk_southbound | `dict[str, Any]` |
| `get_hk_top_stocks` | 港股通十大成交股 | `route("get_hk_top_stocks")` | hk_southbound | `list[dict[str, Any]]` |
| `get_hk_full_stock_list` | 港股全量股票列表 | `route("get_hk_full_stock_list")` | hk_full | `pd.DataFrame` |
| `get_hk_full_daily_bars` | 港股个股日线 | `route("get_hk_full_daily_bars", '00700')` | hk_full | `pd.DataFrame` |
| `get_hk_index_daily_bars` | 港股指数日 K（恒生指数） | `route("get_hk_index_daily_bars", 'HSI')` | hk_full | `pd.DataFrame` |
| `get_us_stock_list` | 美股股票列表 | `route("get_us_stock_list")` | us_stock | `pd.DataFrame` |
| `get_us_stock_daily_bars` | 美股个股日线 | `route("get_us_stock_daily_bars", 'AAPL')` | us_stock | `pd.DataFrame` |
| `get_us_spot_quote` | 美股实时行情 | `route("get_us_spot_quote", 'AAPL')` | us_stock | `dict[str, Any]` |

### 研报 RAG（2 个）

| 方法 | 说明 | 示例调用 | Provider 链 | 返回值 |
|---|---|---|---|---|
| `get_research_chunks` | 研报 RAG 检索（按个股） | `route("get_research_chunks", '600519')` | research_rag | `list[dict[str, Any]]` |
| `search_research` | 研报语义搜索 | `route("search_research", '人工智能算力')` | research_rag | `list[dict[str, Any]]` |

## 五、Provider 端点签名详情（已剔除 `normalize_symbol` 等静态辅助方法）

### `a_stock_data` — a_stock_data.py

```python
async def get_kline(code: str, frequency: int = 9, offset: int = 60) -> list[dict[str, Any]]
async def get_quote_5level(code: str) -> dict[str, Any] | None
async def get_tencent_quote(codes: list[str] | str) -> dict[str, dict[str, Any]]
async def get_concept_blocks(code: str) -> dict[str, Any]
async def get_industry_ranking(limit: int = 90) -> list[dict[str, Any]]
async def get_fund_flow_minute(code: str) -> list[dict[str, Any]]
async def get_dragon_tiger(code: str, trade_date: str | None = None, look_back: int = 30) -> dict[str, Any]
async def get_stock_info(code: str) -> dict[str, Any]
async def get_finance_snapshot(code: str) -> dict[str, Any] | None
async def get_stock_news(code: str, page_size: int = 20) -> list[dict[str, Any]]
```

### `akshare` — akshare.py

```python
async def get_stock_list() -> pd.DataFrame
async def get_stock_individual_info(symbol: str) -> pd.DataFrame
async def get_index_constituents(symbol: str) -> pd.DataFrame
async def get_stock_fundamentals(symbol: str) -> pd.DataFrame
async def get_daily_bars(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame
async def get_minute_bars(symbol: str, start_datetime: str, end_datetime: str, period: str = "60", adjust: str = "") -> pd.DataFrame
async def get_market_fund_flow() -> pd.DataFrame
async def get_northbound_flow() -> pd.DataFrame
async def get_individual_fund_flow(symbol: str, market: str = "") -> pd.DataFrame
async def get_limit_up_pool(date: str | None = None) -> pd.DataFrame
async def get_limit_down_pool(date: str | None = None) -> pd.DataFrame
async def get_bomb_board_pool(date: str | None = None) -> pd.DataFrame
async def get_dragon_tiger_list(date: str | None = None) -> pd.DataFrame
async def get_stock_lhb(symbol: str, days: int = 90) -> pd.DataFrame
async def get_concept_board_list() -> pd.DataFrame
async def get_industry_board_list() -> pd.DataFrame
async def get_concept_board_components(symbol: str) -> pd.DataFrame
async def get_industry_board_components(symbol: str) -> pd.DataFrame
async def get_stock_news(symbol: str, page_size: int = 20) -> list[dict[str, Any]]
async def get_global_macro_news(business_date: str | None = None, look_back_days: int = 7, per_day_limit: int = 10, **kwargs) -> list[dict[str, Any]]
async def get_global_news_aggregated(business_date: date | str | None = None, look_back_days: int = 1, per_source_limit: int = 20) -> list[dict[str, Any]]
async def get_earnings_forecast(symbol: str) -> pd.DataFrame
async def get_analyst_consensus(symbol: str) -> pd.DataFrame
async def get_research_reports(symbol: str | None = None, industry: str | None = None, page_size: int = 30) -> list[dict[str, Any]]
async def get_regulatory_penalties(symbol: str) -> list[dict[str, Any]]
async def get_macro_pmi() -> pd.DataFrame
async def get_macro_cpi() -> pd.DataFrame
async def get_macro_m2() -> pd.DataFrame
async def get_macro_bond_yield() -> pd.DataFrame
async def get_sector_recent_bars(sector_name: str, days: int = 30) -> pd.DataFrame
async def get_sector_fund_flow(sector_name: str) -> pd.DataFrame
async def get_sector_leaders(sector_name: str, top_n: int = 5) -> pd.DataFrame
async def get_sector_event_signals(sector_name: str) -> list[dict[str, Any]]
async def get_insider_transactions(symbol: str) -> pd.DataFrame
```

### `baostock` — baostock.py

```python
async def get_daily_bars(symbol: str, start_date: str = "", end_date: str = "", adjust: str = "qfq", **kwargs) -> pd.DataFrame
async def get_stock_fundamentals(symbol: str, year: int | None = None, quarter: int | None = None, **kwargs) -> pd.DataFrame
async def get_industry_classification(**kwargs) -> pd.DataFrame
async def get_trade_dates(start_date: str = "20200101", end_date: str = "", **kwargs) -> pd.DataFrame
```

### `bse` — bse.py

```python
async def get_stock_list() -> pd.DataFrame
async def get_stock_daily_bars(symbol: str, start_date: str | date | None = None, end_date: str | date | None = None) -> pd.DataFrame
```

### `bse_index` — bse.py

```python
async def get_index_daily_bars(symbol: str = "BJ899050", start_date: str | date | None = None, end_date: str | date | None = None) -> pd.DataFrame
```

### `cninfo` — cninfo.py

```python
async def get_cninfo_announcements(code: str, recent_days: int = 7, category: str = "") -> list[dict[str, Any]]
```

### `cninfo_news` — cninfo_news.py

```python
async def get_cninfo_news(limit: int = 20, category: str = "", **kwargs) -> list[dict[str, Any]]
async def get_cninfo_regulatory(limit: int = 20, **kwargs) -> list[dict[str, Any]]
async def get_cninfo_announcements_full(symbol: str = "", days: int = 30, category: str = "", limit: int = 30, **kwargs) -> list[dict[str, Any]]
```

### `eastmoney` — eastmoney.py

```python
async def get_tencent_quote(symbols: list[str] | str, **kwargs) -> dict
async def get_individual_fund_flow(symbol: str, **kwargs) -> pd.DataFrame
async def get_industry_ranking(limit: int = 50, **kwargs) -> list[dict]
async def get_market_breadth(**kwargs) -> dict
```

### `financial_report` — financial_report.py

```python
async def get_indicators(symbol: str, *, start_year: str | None = None, end_year: str | None = None) -> pd.DataFrame
async def get_balance_sheet(symbol: str) -> pd.DataFrame
async def get_cashflow_statement(symbol: str) -> pd.DataFrame
async def get_income_statement(symbol: str) -> pd.DataFrame
```

### `fundamentals_ext` — fundamentals_ext.py

```python
async def get_earnings_forecast(symbol: str = "", period: str = "", **kwargs) -> list[dict[str, Any]]
async def get_institution_holdings(symbol: str, holder_type: str = "north", **kwargs) -> list[dict[str, Any]]
async def get_analyst_consensus(symbol: str, **kwargs) -> dict[str, Any]
async def get_dividend_history(symbol: str, **kwargs) -> list[dict[str, Any]]
async def get_lockup_calendar(days_ahead: int = 30, **kwargs) -> list[dict[str, Any]]
```

### `futures` — futures.py

```python
async def get_main_contracts_sina() -> pd.DataFrame
async def get_daily_bars_sina(symbol: str, start_date: str | date | None = None, end_date: str | date | None = None) -> pd.DataFrame
async def get_sector_spot() -> pd.DataFrame
```

### `geopolitics` — geopolitics.py

```python
async def get_geopolitics_news(keywords: list[str] | None = None, limit: int = 20, **kwargs) -> list[dict[str, Any]]
async def get_sanctions_news(limit: int = 15, **kwargs) -> list[dict[str, Any]]
async def get_trade_war_news(limit: int = 15, **kwargs) -> list[dict[str, Any]]
async def get_geopolitics_risk_score(**kwargs) -> dict[str, Any]
```

### `global_media` — global_media.py

```python
async def get_wallstreet_news(limit: int = 20, channel: str = "global", **kwargs) -> list[dict[str, Any]]
async def get_cnbc_news(limit: int = 15, **kwargs) -> list[dict[str, Any]]
async def get_yahoo_finance_news(symbol: str = "", limit: int = 15, **kwargs) -> list[dict[str, Any]]
async def get_reuters_news(limit: int = 15, category: str = "business", **kwargs) -> list[dict[str, Any]]
async def get_global_market_news(limit: int = 30, **kwargs) -> list[dict[str, Any]]
```

### `global_quote` — global_quote.py

```python
async def get_global_quote(codes: list[str] | None = None) -> dict[str, dict[str, Any]]
```

### `hk_full` — hk_full.py

```python
async def get_stock_list() -> pd.DataFrame
async def get_daily_bars(symbol: str, start_date: str | date | None = None, end_date: str | date | None = None, adjust: str = "qfq") -> pd.DataFrame
async def get_index_daily_bars(symbol: str = "HKHSI", start_date: str | date | None = None, end_date: str | date | None = None) -> pd.DataFrame
```

### `hk_southbound` — hk_southbound.py

```python
async def get_southbound_flow() -> dict[str, Any]
async def get_top_stocks(market: str = "001", topn: int = 5) -> list[dict[str, Any]]
```

### `netease` — netease.py

```python
async def get_tencent_quote(symbols: list[str] | str) -> dict[str, dict[str, Any]]
async def get_daily_bars(symbol: str, start_date: str | None = None, end_date: str | None = None, adjust: str = "", **kwargs) -> pd.DataFrame
async def get_minute_bars(symbol: str, period: str = "1m", datalen: int = 240) -> list[dict[str, Any]]
```

### `news` — news.py

```python
async def get_news_flash(limit: int = 30, source: str = "auto", **kwargs) -> list[dict[str, Any]]
async def get_stock_news(symbol: str, limit: int = 20, **kwargs) -> list[dict[str, Any]]
async def get_global_news(limit: int = 30, **kwargs) -> list[dict[str, Any]]
async def get_global_macro_news(business_date: str | None = None, look_back_days: int = 7, per_day_limit: int = 10, **kwargs) -> list[dict[str, Any]]
```

### `policy` — policy.py

```python
async def get_csrc_announcements(limit: int = 20, **kwargs) -> list[dict[str, Any]]
async def get_pboc_operations(days: int = 7, **kwargs) -> list[dict[str, Any]]
async def get_macro_indicators(indicators: list[str] | None = None, **kwargs) -> dict[str, Any]
async def get_policy_news(keywords: list[str] | None = None, limit: int = 20, **kwargs) -> list[dict[str, Any]]
```

### `research_rag` — research_rag.py

```python
async def get_chunks(symbol: str, topn: int | None = None) -> list[dict[str, Any]]
async def search(query: str, top_k: int = 5, symbol: str | None = None) -> list[dict[str, Any]]
```

### `sentiment` — sentiment.py

```python
async def get_stock_sentiment(symbol: str, **kwargs) -> dict[str, Any]
async def get_hot_stocks(limit: int = 20, **kwargs) -> list[dict[str, Any]]
async def get_sector_sentiment(sector_name: str = "", **kwargs) -> dict[str, Any]
```

### `sina` — sina.py

```python
async def get_tencent_quote(symbols: list[str] | str) -> dict[str, dict[str, Any]]
async def get_daily_bars(symbol: str, start_date: str | None = None, end_date: str | None = None, scale: int = 240, datalen: int = 60, *, days: int | None = None) -> pd.DataFrame
async def get_kline(code: str, frequency: int = 9, offset: int = 60) -> list[dict[str, Any]]
async def get_index_daily_bars(symbol: str, datalen: int = 500, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame
async def get_minute_bars(symbol: str, start_datetime: str | None = None, end_datetime: str | None = None, scale: int = 60, datalen: int = 240) -> pd.DataFrame
async def get_stock_info(symbol: str) -> dict[str, Any]
async def get_industry_ranking(limit: int = 90, **kwargs) -> list[dict[str, Any]]
async def get_sector_fund_flow(sector_name: str = "", **kwargs) -> pd.DataFrame
```

### `tushare` — tushare.py

```python
async def get_daily_bars(symbol: str, start_date: str = "", end_date: str = "", adjust: str = "qfq", **kwargs) -> pd.DataFrame
async def get_stock_list(**kwargs) -> pd.DataFrame
async def get_stock_fundamentals(symbol: str, **kwargs) -> pd.DataFrame
async def get_market_fund_flow(**kwargs) -> pd.DataFrame
async def get_northbound_flow(**kwargs) -> pd.DataFrame
async def get_index_daily_bars(symbol: str = "000300.SH", start_date: str = "", end_date: str = "", **kwargs) -> pd.DataFrame
async def get_trade_dates(start_date: str = "20200101", end_date: str = "", **kwargs) -> pd.DataFrame
```

### `us_stock` — us_stock.py

```python
async def get_stock_list() -> pd.DataFrame
async def get_daily_bars(symbol: str, start_date: str | None = None, end_date: str | None = None, adjust: str = "qfq") -> pd.DataFrame
async def get_spot_quote(symbol: str) -> dict[str, Any]
```

## 六、未接入路由表的 provider 原生端点（9 个）

以下端点已在 provider 实现，但尚未在 `method_table.py` 注册（可通过 `source.register_method()` 运行时接入）：

| Provider | 端点 | 说明 |
|---|---|---|
| `baostock` | `get_industry_classification(**kwargs)` | 行业分类 |
| `baostock` | `get_trade_dates(start_date, end_date)` | 交易日历 |
| `tushare` | `get_stock_list(**kwargs)` | 股票列表（tushare 源） |
| `tushare` | `get_index_daily_bars(symbol="000300.SH", ...)` | 指数日 K（tushare 源） |
| `tushare` | `get_trade_dates(start_date, end_date)` | 交易日历 |
| `eastmoney` | `get_market_breadth(**kwargs)` | 市场宽度 |
| `financial_report` | `get_indicators(symbol, *, start_year, end_year)` | 财务指标历年 |
| `netease` | `get_daily_bars(symbol, start, end, adjust)` | 日线（网易源，可作额外 fallback） |
| `netease` | `get_minute_bars(symbol, period, datalen)` | 分钟线（网易源） |

## 七、调用约定与运行时行为

| 项 | 说明 |
|---|---|
| 路由参数 | `route(method, *args, provider=None, strict=None, timeout=None, use_cache=True, **kwargs)`；`provider=` 可强制指定源 |
| 缓存 | 默认 30s TTL 内存缓存（`ROUTE_CACHE_TTL_SEC`）；`ROUTE_CACHE_PERSISTENT=true` 开启内存+文件分层 |
| 韧性 | 默认开启：重试 3 次（指数退避+抖动）/ 限流 5rps / 熔断 5 次失败 / 并发隔离 10（每 provider 独立） |
| 超时 | 单端点默认 12s（`ROUTE_DEFAULT_TIMEOUT`） |
| 质量校验 | 拒绝 `None` 结果；事件类端点空 list/dict 是合法业务响应（如"今日无新闻"）；选择器类端点（`get_industry_ranking`/`get_sector_fund_flow`/`get_stock_list`/`get_individual_fund_flow`）空结果=失败并继续 fallback |
| 调用日志 | 异步批量写入 `.call_logs/YYYYMMDD.jsonl` |
| 健康度 | `source.health()` 查看各端点成功率/延迟/最近错误 |

> 注：`get_insider_transactions` 为契约占位端点 —— A 股无 SEC 式内幕交易披露接口，调用显式抛 `NotImplementedError`，请改用 `get_regulatory_penalties` / `get_dragon_tiger`。
