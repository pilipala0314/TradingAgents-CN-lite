"""GlobalQuoteProvider — 国际/全球行情 Provider（港股 / 美股 / 指数 / 大宗）。

底层复用 `_shared/tencent.py::_parse_quote_response()` 解析逻辑（避免重复造轮子），
HTTP 入口仍是腾讯财经 `qt.gtimg.cn`（零鉴权、不封 IP、GBK 编码）。

DataSource 端点：
- `get_global_quote(codes: list[str])` — 批量拉取
    codes 应为带前缀的腾讯财经代码：
    - 港股：`hk00700`（腾讯）/ `hk09988`（阿里）等
    - 美股：`usAAPL` / `usTSLA` / `usBIDU` 等
    - 指数：`sh000001`（上证）/ `sz399001`（深成）/ `sh000300`（沪深300）
    - 大宗：`hf_GC`（COMEX 黄金）/ `hf_CL`（WTI 原油）等（暂不实现）
    - ETF：`sh510050`（50ETF）等（直接走 A 股 sh 前缀）

    返回：`{code: {name, price, change_pct, high, low, ...}}`，code 已剥前缀。
"""
from __future__ import annotations

import asyncio
import logging
import urllib.request
from typing import Any

from datasource.providers._shared.tencent import _parse_quote_response
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

QT_GTIMG_URL = "https://qt.gtimg.cn/q="
_UA = "Mozilla/5.0"


def _fetch_global_quotes(prefixed_codes: list[str], timeout: float = 10.0) -> str:
    """同步拉一次腾讯财经 qt.gtimg.cn，返回 raw GBK 字符串。"""
    if not prefixed_codes:
        return ""
    url = QT_GTIMG_URL + ",".join(prefixed_codes)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _UA)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.read().decode("gbk", errors="ignore")


@register_provider("global_quote")
class GlobalQuoteProvider:
    """国际/全球行情 Provider — DataSource 的 'global_quote' provider。"""

    # 前缀 → 市场名（P0-T3 用于前端按市场分组）
    _PREFIX_TO_MARKET: dict[str, str] = {
        "sh": "cn", "sz": "cn", "bj": "cn",
        "hk": "hk", "us": "us",
        "hf": "other",  # 海外期货归到"其他 / 大宗"
    }

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "global_quote"

    async def health_check(self) -> bool:
        """依赖检查：走标准 HTTP，无额外依赖。"""
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "global_quote" in provs)

    async def get_global_quote(
        self, codes: list[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """批量拉取国际/全球行情。

        Args:
            codes: 带前缀的代码列表（默认 9 个常见指数 + 大宗占位）

        Returns:
            {code: {name, price, change_pct, market, ...}}（code 已剥前缀）
            每个 dict 新增 `market` 字段（"cn"/"hk"/"us"），用于前端分组渲染。
        """
        if codes is None:
            codes = _DEFAULT_GLOBAL_CODES
        # 过滤空 + 去重
        prefixed = [c.strip() for c in codes if c and c.strip()]
        if not prefixed:
            return {}
        # 建立 code → market 映射（用于注入 market 字段）
        # 前缀拆分的两种格式：
        #   - "sh000001" / "hkHSI" / "usAAPL" → 前缀 + 代码连续
        #   - "hf_GC" / "hf_CL" → 前缀 + 下划线 + 代码（期货）
        code_to_market: dict[str, str] = {}
        for p in prefixed:
            matched_market: str | None = None
            matched_code: str = p
            for prefix, market in self._PREFIX_TO_MARKET.items():
                if p.startswith(prefix + "_"):  # 期货格式
                    matched_market = market
                    matched_code = p[len(prefix) + 1:]
                    break
                if p.startswith(prefix):  # 紧凑格式
                    matched_market = market
                    matched_code = p[len(prefix):]
                    break
            if matched_market is not None and matched_code:
                code_to_market[matched_code] = matched_market
        data = await asyncio.to_thread(_fetch_global_quotes, prefixed, self._timeout)
        result = _parse_quote_response(data)
        # 注入 market 字段
        for code, q in result.items():
            q["market"] = code_to_market.get(code, "unknown")
        return result


# 默认全球指数（5 A 股 + 1 港股 + 2 美股 + 6 海外期货）
_DEFAULT_GLOBAL_CODES: list[str] = [
    # A 股指数
    "sh000001",   # 上证指数
    "sz399001",   # 深证成指
    "sz399006",   # 创业板指
    "sh000300",   # 沪深 300
    "sh000688",   # 科创 50
    # 港股
    "hkHSI",      # 恒生指数
    # 美股
    "usDJI",      # 道琼斯
    "usIXIC",     # 纳斯达克
    # 海外期货（hf_）— 黄金/原油/白银/大豆/天然气/铜
    "hf_GC",      # COMEX 黄金
    "hf_CL",      # WTI 原油
    "hf_SI",      # COMEX 白银
    "hf_S",       # 美大豆
    "hf_NG",      # 天然气
    "hf_HG",      # 美铜
]
