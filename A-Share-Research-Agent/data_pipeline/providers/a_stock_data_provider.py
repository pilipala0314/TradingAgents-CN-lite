"""a-stock-data Provider — DataRouter 的 'a_stock_data' provider 实现。

把 `data_pipeline/providers/` 下的 10 个细粒度端点（quote / capital / fundamentals /
sector / news）包装成 async def 方法，作为 DataRouter 的第二个 provider。

设计目的：
- 让 facade 走 router 而不是直接 import 10 个模块；
- 统一享受 cache / stats / timeout / strict；
- 未来切换数据源（比如换 a_stock_data_v2）只需替换 provider 即可。

端点清单（10 个）：
- get_kline           日 K 线（mootdx）
- get_quote_5level    五档盘口（mootdx）
- get_tencent_quote   腾讯实时估值（HTTP GBK）
- get_concept_blocks  个股板块归属（东财 slist）
- get_industry_ranking 行业涨跌排名（东财 clist）
- get_fund_flow_minute  分钟级资金流（东财 push2）
- get_dragon_tiger     龙虎榜聚合（东财 datacenter）
- get_stock_info       个股基本面（东财 push2 stock/get）
- get_finance_snapshot mootdx 季报快照
- get_stock_news       个股新闻（东财 search-api-web）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import capital, fundamentals, news, quote, sector

logger = logging.getLogger(__name__)


class AStockDataProvider:
    r"""a-stock-data 风格细粒度端点 Provider — DataRouter 的 'a_stock_data' provider。

    ## 端点契约（统一为 list[dict] / dict）

    本 provider 暴露 10 个端点，对应 `DataRouter.DEFAULT_METHOD_TABLE` 中所有
    `provider == "a_stock_data"` 的方法。所有端点都是 `async def`，
    内部用 `asyncio.to_thread` 包装同步的 a-stock-data 工具函数。

    **返回值类型统一为 Python 原生类型**（`list[dict]` / `dict` / `None`），
    避免 DataFrame 的 `ambiguous truth` 报错，便于 facade 透传与组合。

    | method                | 返回类型       | 数据源          | 用途                       |
    | --------------------- | -------------- | --------------- | -------------------------- |
    | `get_kline`           | `list[dict]`   | mootdx          | 个股 K 线                  |
    | `get_quote_5level`    | `dict \| None` | mootdx          | 实时五档盘口               |
    | `get_tencent_quote`   | `dict[str, dict]` | 腾讯财经 HTTP | 实时估值（PE/PB/市值）     |
    | `get_concept_blocks`  | `dict`         | 东财 slist      | 个股所属板块 / 概念 / 地域 |
    | `get_industry_ranking`| `list[dict]`   | 东财 clist      | 行业涨跌排名               |
    | `get_fund_flow_minute`| `list[dict]`   | 东财 push2 fflow | 分钟级资金流              |
    | `get_dragon_tiger`    | `dict`         | 东财 datacenter | 龙虎榜聚合（records+seats+institution）|
    | `get_stock_info`      | `dict`         | 东财 stock/get  | 个股基本面                 |
    | `get_finance_snapshot`| `dict \| None` | mootdx 财务     | 季报快照（EPS/ROE/净利）   |
    | `get_stock_news`      | `list[dict]`   | 东财 search-api-web | 个股新闻流             |

    ## 失败语义

    - **永不静默降级**：失败直接 raise（外层 `DataRouter` 决定是否 fallback）。
    - **超时控制**：`DataRouter` 的 `asyncio.wait_for` 强制单端点超时。
    - **无重试**：底层 a-stock-data 工具是 sync 的，重试交给外层 router。

    ## 与 AkshareProvider 的区别

    - **粒度**：AStockDataProvider 是细粒度（10 端点 dict/list 返回），
      AkshareProvider 是粗粒度（28 端点 DataFrame 返回 + 4 个 list/dict 聚合）。
    - **数据源**：AStockDataProvider 走 a-stock-data 风格（mootdx / 腾讯 / 东财），
      AkshareProvider 走 AkShare 聚合层（28 个原始接口）。
    - **使用场景**：AStockDataProvider 适合 facade 实时拉取（轻量、契约清晰），
      AkshareProvider 适合 ETL 批量入库（DataFrame 入库高效）。
    """

    # ── 行情 ──────────────────────────────────────────────────────────

    async def get_kline(self, code: str, frequency: int = 9, offset: int = 60) -> list[dict[str, Any]]:
        """mootdx K 线。frequency: 9=日线 0=5min 8=1min。offset: 根数。"""
        return await asyncio.to_thread(quote.fetch_kline, code, frequency, offset)

    async def get_quote_5level(self, code: str) -> dict[str, Any] | None:
        """mootdx 实时五档盘口。"""
        return await asyncio.to_thread(quote.fetch_quote_5level, code)

    async def get_tencent_quote(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        """腾讯实时估值（PE/PB/市值/换手/涨跌停）。"""
        return await asyncio.to_thread(quote.fetch_tencent_valuation, codes)

    # ── 板块 ──────────────────────────────────────────────────────────

    async def get_concept_blocks(self, code: str) -> dict[str, Any]:
        """个股所属板块 / 概念 / 地域（东财 slist）。"""
        return await asyncio.to_thread(sector.fetch_concept_blocks, code)

    async def get_industry_ranking(self, limit: int = 90) -> list[dict[str, Any]]:
        """行业板块涨跌排名（东财 clist）。"""
        return await asyncio.to_thread(sector.fetch_industry_ranking, limit)

    # ── 资金 ──────────────────────────────────────────────────────────

    async def get_fund_flow_minute(self, code: str) -> list[dict[str, Any]]:
        """盘中实时分钟级资金流（东财 push2 fflow）。"""
        return await asyncio.to_thread(capital.fetch_fund_flow_minute, code)

    async def get_dragon_tiger(
        self, code: str, trade_date: str | None = None, look_back: int = 30
    ) -> dict[str, Any]:
        """龙虎榜聚合：records + seats + institution（东财 datacenter）。"""
        return await asyncio.to_thread(capital.fetch_dragon_tiger, code, trade_date, look_back)

    # ── 基本面 ────────────────────────────────────────────────────────

    async def get_stock_info(self, code: str) -> dict[str, Any]:
        """个股基本面：行业/股本/市值/上市日（东财 push2 stock/get）。"""
        return await asyncio.to_thread(fundamentals.fetch_stock_info, code)

    async def get_finance_snapshot(self, code: str) -> dict[str, Any] | None:
        """mootdx 季报快照：EPS/ROE/净利/主营收入。"""
        return await asyncio.to_thread(fundamentals.fetch_finance_snapshot, code)

    # ── 新闻 ──────────────────────────────────────────────────────────

    async def get_stock_news(self, code: str, page_size: int = 20) -> list[dict[str, Any]]:
        """东财个股新闻流（search-api-web JSONP）。"""
        return await asyncio.to_thread(news.fetch_stock_news, code, page_size)
