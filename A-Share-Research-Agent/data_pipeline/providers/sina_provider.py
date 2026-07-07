"""SinaProvider — 新浪财经行情 fallback 源。

数据源：`hq.sinajs.cn`（最稳定的免费行情 API，无反爬）。

端点契约（方法名与 akshare / a_stock_data 对齐，便于 DataRouter fallback）：
- `get_tencent_quote` → 实时行情（多只，逗号分隔的 symbol 列表）
- `get_daily_bars`     → K 线（日 / 周 / 月）
- `get_minute_bars`    → 分钟 K 线
- `get_stock_info`     → 个股基础信息（名称 / 行业 / 板块）

返回值统一为 `list[dict]` / `dict`（与 a_stock_data 对齐）。

失败语义：永不静默降级；失败直接 raise（DataRouter 决定 fallback）。
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# 新浪行情常量
_SINA_HQ_URL = "https://hq.sinajs.cn/list={symbols}"
_SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
_SINA_STOCK_INFO_URL = "https://hq.sinajs.cn/list={symbol}"  # 用于取名称

# 市场代码映射：6→sh, 0/3→sz, 4/8→bj
def _to_sina_symbol(symbol: str) -> str:
    s = symbol.lower().replace(".sh", "").replace(".sz", "").replace(".bj", "")
    # 去除已有的 sh/sz/bj 前缀（避免 "shsh600519" 这种重复）
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s.startswith(("4", "8")):
        return f"bj{s}"
    if s.startswith("6"):
        return f"sh{s}"
    return f"sz{s}"


def _parse_hq_line(line: str) -> dict[str, Any] | None:
    """解析单行 hq.sinajs.cn 响应。

    格式：var hq_str_sh600519="贵州茅台,1800.00,1790.00,1810.00,...";
    字段顺序（按新浪文档）：
      0: 名称, 1: 今开, 2: 昨收, 3: 当前, 4: 最高, 5: 最低,
      6: 买一价, 7: 卖一价, 8: 成交量(股), 9: 成交额(元),
      10: 买一量, 11: 卖一量, 30: 日期, 31: 时间
    """
    try:
        # 提取 "..." 内的内容
        start = line.find('"')
        end = line.rfind('"')
        if start < 0 or end <= start:
            return None
        payload = line[start + 1:end]
        fields = payload.split(",")
        if len(fields) < 32:
            return None
        return {
            "name": fields[0],
            "open": float(fields[1] or 0),
            "prev_close": float(fields[2] or 0),
            "current": float(fields[3] or 0),
            "high": float(fields[4] or 0),
            "low": float(fields[5] or 0),
            "bid1": float(fields[6] or 0),
            "ask1": float(fields[7] or 0),
            "volume": int(fields[8] or 0),
            "amount": float(fields[9] or 0),
            "date": fields[30],
            "time": fields[31],
        }
    except (ValueError, IndexError) as exc:
        logger.debug("Sina 解析单行失败: %s", exc)
        return None


def _filter_bars_by_date(
    bars: list[dict[str, Any]],
    start_date: str | None,
    end_date: str | None,
) -> list[dict[str, Any]]:
    """按 [start_date, end_date] 闭区间过滤 K 线。

    sina 的 day 字段形如 "2024-01-15"；start_date / end_date 支持
    "YYYYMMDD" 或 "YYYY-MM-DD"，自动归一化。任一为空则该端不限制。
    """
    def _norm(s: str) -> str:
        return s.replace("-", "").replace("/", "")

    lo = _norm(start_date) if start_date else ""
    hi = _norm(end_date) if end_date else ""
    out: list[dict[str, Any]] = []
    for bar in bars:
        d = _norm(str(bar.get("date", "")))
        if not d:
            continue
        if lo and d < lo:
            continue
        if hi and d > hi:
            continue
        out.append(bar)
    return out


class SinaProvider:
    """新浪财经 Provider — DataRouter 的 'sina' provider。

    Args:
        timeout:   HTTP 超时（秒）
        transport: 测试用 — httpx.MockTransport 实例（默认 None 走真实网络）
    """

    def __init__(self, timeout: float = 8.0, transport: httpx.BaseTransport | None = None) -> None:
        self.timeout = timeout
        self._transport = transport
        # 新浪需要 Referer header 否则 403
        self._headers = {
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        }

    async def _fetch(self, url: str) -> str:
        """统一 GET 入口（业务方应通过 ResiliencePolicy 加重试 / 限流）。

        **Sina 返回 GBK 编码**：httpx 默认按 charset header 推断，缺失时 fallback 到 UTF-8。
        对 Sina URL 强制 `resp.encoding = "gbk"` 兜底，避免股票名变乱码。
        """
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers, transport=self._transport) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            # Sina 强制 GBK（hq.sinajs.cn 实际 charset=GBK；K 线 JSON 接口是 UTF-8）
            if "sinajs.cn" in url or "finance.sina.com.cn" in url:
                resp.encoding = "gbk"
            return resp.text

    async def get_tencent_quote(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """批量实时行情（与 a_stock_data 同 method，同 list[dict] 契约）。

        Args:
            symbols: 代码列表（['sh600519', 'sz000636']，已带市场前缀或裸 6 位）

        Returns:
            dict[symbol -> {name, open, prev_close, current, high, low, volume, amount, date, time}]
        """
        sina_syms = [_to_sina_symbol(s) for s in symbols]
        url = _SINA_HQ_URL.format(symbols=",".join(sina_syms))
        text = await self._fetch(url)
        out: dict[str, dict[str, Any]] = {}
        for orig, _sina_sym, line in zip(symbols, sina_syms, text.splitlines(), strict=False):
            if not line.strip():
                continue
            d = _parse_hq_line(line)
            if d is not None:
                # 用传入的 symbol 作为 key（便于 facade 索引）
                key = orig if not orig.startswith(("sh", "sz", "bj")) else orig[2:]
                out[key] = d
        return out

    async def get_daily_bars(
        self,
        symbol: str,
        scale: int = 240,
        datalen: int = 60,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """日 K 线（与 akshare get_daily_bars 同 method）。

        Args:
            symbol:     6 位代码
            scale:      周期（240=日 / 1680=周 / 43200=月）
            datalen:    返回条数
            start_date: 起始日期（YYYYMMDD 或 YYYY-MM-DD，可选）— 启用后对结果做 post-filter
            end_date:   结束日期（同上，可选）

        Returns:
            list[dict]，每条含：date / open / high / low / close / volume。
            如果给了 start_date / end_date，结果会按 [start, end] 闭区间过滤。
        """
        params = {
            "symbol": _to_sina_symbol(symbol),
            "scale": scale,
            "datalen": datalen,
        }
        import json
        url = f"{_SINA_KLINE_URL}?{urlencode(params)}"
        text = await self._fetch(url)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Sina K 线 JSON 解析失败: {exc}") from exc
        bars: list[dict[str, Any]] = [
            {
                "date": str(d.get("day", "")),
                "open": float(d.get("open", 0)),
                "high": float(d.get("high", 0)),
                "low": float(d.get("low", 0)),
                "close": float(d.get("close", 0)),
                "volume": int(d.get("volume", 0)),
            }
            for d in (data or [])
        ]
        if start_date or end_date:
            return _filter_bars_by_date(bars, start_date, end_date)
        return bars

    async def get_index_daily_bars(
        self,
        symbol: str,
        datalen: int = 500,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """指数日 K 线（sina 指数接口，需 sh/sz 前缀）。

        Args:
            symbol:     "000300" / "000300.SH" / "sh000300"（自动推断 sh/sz）
            datalen:    返回条数（默认 500 ≈ 2 年）
            start_date: 起始日期过滤
            end_date:   结束日期过滤

        Returns:
            list[{date, open, high, low, close, volume}]

        适用：沪深 300（sh000300）/ 中证 500（sh000905）/ 创业板指（sz399006）等。
        """
        s = symbol.upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        if s.startswith(("SH", "SZ", "BJ")):
            sina_code = s.lower()
        elif s.startswith(("0", "3")):
            sina_code = f"sh{s}"  # A 股指数大多在沪市
        elif s.startswith(("4", "8")):
            sina_code = f"bj{s}"
        else:
            sina_code = f"sh{s}"
        params = {
            "symbol": sina_code,
            "scale": 240,
            "datalen": datalen,
        }
        data = await self._fetch_history(params)
        bars = [
            {
                "date": str(d.get("day", "")),
                "open": float(d.get("open", 0)),
                "high": float(d.get("high", 0)),
                "low": float(d.get("low", 0)),
                "close": float(d.get("close", 0)),
                "volume": int(d.get("volume", 0)),
            }
            for d in (data or [])
        ]
        if start_date or end_date:
            return _filter_bars_by_date(bars, start_date, end_date)
        return bars

    async def get_minute_bars(
        self,
        symbol: str,
        scale: int = 1,
        datalen: int = 240,
    ) -> list[dict[str, Any]]:
        """分钟 K 线。

        Args:
            symbol:  6 位代码
            scale:   周期（1=1min / 5=5min / 15=15min / 30=30min / 60=60min）
            datalen: 返回条数
        """
        return await self.get_daily_bars(symbol, scale=scale, datalen=datalen)

    async def get_stock_info(self, symbol: str) -> dict[str, Any]:
        """个股基础信息（名称 / 当日 OHLCV）。

        新浪 stock_info 端点用 hq.sinajs.cn 简化版（取 name 字段）。
        """
        url = _SINA_HQ_URL.format(symbols=_to_sina_symbol(symbol))
        text = await self._fetch(url)
        for line in text.splitlines():
            d = _parse_hq_line(line)
            if d is not None:
                return d
        raise ValueError(f"Sina 个股信息为空: {symbol}")
