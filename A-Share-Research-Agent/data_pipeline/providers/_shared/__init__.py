"""a-stock-data SKILL 协议层（L0）— 内部按职责拆分为子模块。

本目录将原 `data_pipeline/providers/_shared.py` (268 行大杂烩) 拆成 4 个职责单一的子模块：

- `ticker.py`     ticker 归一化 + 市场前缀（纯函数，无 I/O）
- `http_client.py` 东财 HTTP 节流 + Keep-Alive + Retry（em_get / em_post / session）
- `tdx_client.py`  mootdx 客户端工厂（规避 BESTIP.HQ 空串 bug）
- `tencent.py`    腾讯财经实时行情（PE/PB/市值/换手/涨跌停）
- `datacenter.py` 东财数据中心统一查询（龙虎榜/解禁/两融/...）

外部使用方继续 `from data_pipeline.providers._shared import ...` 即可，
本 `__init__.py` 做 re-export 保持 100% 向后兼容。

数据源优先级按 SKILL 原则：mootdx > 腾讯 > 东财（限流使用）。

> 注：本项目与上游 SKILL.md 在源码层面物理隔离；本目录是逐行移植的等价实现，
> 设计上与上游保持同步兼容，但适配了项目的 logger / retry 习惯。
"""
from __future__ import annotations

from .datacenter import DATACENTER_URL, eastmoney_datacenter

# 协议层（按职责拆分后的子模块）
from .http_client import (
    EM_MIN_INTERVAL,
    EM_SESSION,
    UA,
    em_get,
    em_post,
)
from .tdx_client import _TDX_SERVERS, tdx_client
from .tencent import tencent_quote
from .ticker import em_market_code, market_prefix, normalize_ticker

__all__ = [
    # datacenter
    "DATACENTER_URL",
    "EM_MIN_INTERVAL",
    "EM_SESSION",
    # http
    "UA",
    "_TDX_SERVERS",
    "eastmoney_datacenter",
    "em_get",
    "em_market_code",
    "em_post",
    "market_prefix",
    # ticker
    "normalize_ticker",
    # tdx
    "tdx_client",
    # tencent
    "tencent_quote",
]
