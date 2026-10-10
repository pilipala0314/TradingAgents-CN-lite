"""SinaProvider — 新浪财经行情 fallback 源。

数据源：`hq.sinajs.cn`（最稳定的免费行情 API，无反爬）。

端点契约（方法名与 akshare / a_stock_data 对齐，便于 DataSource fallback）：
- `get_tencent_quote` → 实时行情（多只，逗号分隔的 symbol 列表）
- `get_daily_bars`     → K 线（日 / 周 / 月），返回 pd.DataFrame（与 akshare/tushare 对齐）
- `get_kline`          → 通达信风格 K 线兼容端点（返回 list[dict]）
- `get_minute_bars`    → 分钟 K 线
- `get_stock_info`     → 个股基础信息（名称 / 行业 / 板块）

实时行情类返回 `dict`；K 线类统一返回 `pd.DataFrame`（列：date/open/high/low/close/volume）。

失败语义：永不静默降级；失败直接 raise（DataSource 决定 fallback）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import pandas as pd

from datasource.errors import ProviderError
from datasource.providers._http import get_shared_client
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

# 新浪行情常量
_SINA_HQ_URL = "https://hq.sinajs.cn/list={symbols}"
_SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
# 新浪板块数据端点（不依赖东财，作为东财被 WAF 封锁时的独立源）
_SINA_SECTOR_LIST_URL = "https://money.finance.sina.com.cn/q/view/newSinaHy.php"
_SINA_SECTOR_FLOW_URL = "https://money.finance.sina.com.cn/q/view/newFLJK.php?param={param}"


# ── 新浪板块数据解析 ──────────────────────────────────────────────
# 格式（newSinaHy / newFLJK）：{"key":"key,板块名,个股数,平均价,涨跌幅,涨跌额,成交量,成交额,领涨股代码,领涨股涨跌幅,领涨股现价,领涨股涨跌额,领涨股名称", ...}
# 输出字段名与 eastmoney / a_stock_data 板块接口对齐（board_code/board_name/change_pct/lead_stock）。
_SECTOR_FLOW_INDICES = {
    "member_count": 2, "avg_price": 3, "change_pct": 4, "change": 5,
    "volume": 6, "amount": 7,
    "lead_code": 8, "lead_change_pct": 9, "lead_price": 10, "lead_change": 11,
    "lead_stock": 12,
}
_SECTOR_INT_FIELDS = frozenset({"member_count", "volume"})
_SECTOR_FLOAT_FIELDS = frozenset({
    "avg_price", "change_pct", "change", "amount",
    "lead_change_pct", "lead_price", "lead_change",
})


def _to_int(v: Any) -> int:
    """安全整数转换（含 "19.0"/千分位等脏数据）。"""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _to_float(v: Any) -> float:
    """安全浮点转换。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_sina_sector(payload: str) -> list[dict[str, Any]]:
    """解析新浪板块列表/资金流响应（newSinaHy / newFLJK）。

    返回: [{board_code, board_name, member_count, avg_price, change_pct, change,
            volume, amount, lead_code, lead_change_pct, lead_price, lead_change, lead_stock}]
    格式异常/空响应返回 []（失败语义由调用方处理）。
    """
    import json

    start = payload.find("{")
    if start < 0:
        return []
    try:
        # raw_decode：只解析第一个 JSON 对象，尾随 JS 代码/多对象不会污染结果
        raw, _ = json.JSONDecoder().raw_decode(payload[start:])
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, dict):
        return []
    out: list[dict[str, Any]] = []
    for code, val in raw.items():
        parts = str(val).split(",")
        if len(parts) < 13:
            continue
        row: dict[str, Any] = {"board_code": code, "board_name": parts[1]}
        for field, idx in _SECTOR_FLOW_INDICES.items():
            v = parts[idx] if idx < len(parts) else ""
            if field in _SECTOR_INT_FIELDS:
                row[field] = _to_int(v)
            elif field in _SECTOR_FLOAT_FIELDS:
                row[field] = _to_float(v)
            else:
                row[field] = str(v or "")
        out.append(row)
    return out

# 市场代码映射：6/90→sh, 0/3→sz, 4/8/92→bj
def _to_sina_symbol(symbol: str) -> str:
    s = symbol.lower().replace(".sh", "").replace(".sz", "").replace(".bj", "")
    # 去除已有的 sh/sz/bj 前缀（避免 "shsh600519" 这种重复）
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s.startswith("92"):  # 北交所 920 段（先于沪 9 判，避免误归 sh）
        return f"bj{s}"
    if s.startswith(("4", "8")):
        return f"bj{s}"
    if s.startswith(("6", "90")):  # 90 = 沪市 B 股 900 段
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


def _estimate_datalen(start_date: str | None, scale: int, datalen: int) -> int:
    """start_date 早于“最近 datalen 根”覆盖范围时，自动扩 datalen。

    sina K 线接口只支持“最近 N 根”，历史区间必须拉长后做 post-filter；
    否则默认 datalen=60 只覆盖最近两个月，历史区间过滤后必然为空。
    系数：日线 240 → 交易日 ≈ 自然日 × 0.7；分钟线 → 每自然日 240/scale 根 × 0.7。
    上限 2040（sina 单次返回量级），更早历史交由 fallback 链（baostock 等）兜底。
    """
    if not start_date:
        return datalen
    d = str(start_date).replace("-", "").replace("/", "")[:8]
    try:
        start = datetime.strptime(d, "%Y%m%d")
    except ValueError:
        return datalen
    days = (datetime.now() - start).days
    if days <= 0:
        return datalen
    if scale >= 240:  # 日/周/月线：按自然日 × 0.7 个交易日估算
        needed = int(days * 0.7) + 10
    else:             # 分钟线：每交易日 240/scale 根
        needed = int(days * (240 / max(scale, 1)) * 0.7) + 10
    return max(datalen, min(needed, 2040))


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


@register_provider("sina")
class SinaProvider:
    """新浪财经 Provider — DataSource 的 'sina' provider。

    Args:
        timeout: HTTP 超时（秒）
    """

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout
        # 新浪需要 Referer header 否则 403
        self._headers = {
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        }

    @property
    def name(self) -> str:
        return "sina"

    async def health_check(self) -> bool:
        """依赖检查：sina 走标准 HTTP，无额外依赖（网络可用性由路由层实测）。"""
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "sina" in provs)

    async def _fetch(self, url: str) -> str:
        """统一 GET 入口（复用全局 httpx 连接池，Keep-Alive）。

        **Sina 返回 GBK 编码**：httpx 默认按 charset header 推断，缺失时 fallback 到 UTF-8。
        对 Sina URL 强制 `resp.encoding = "gbk"` 兜底，避免股票名变乱码。
        """
        client = await get_shared_client()
        resp = await client.get(url, headers=self._headers, timeout=self.timeout)
        resp.raise_for_status()
        # Sina 强制 GBK（hq.sinajs.cn 实际 charset=GBK；K 线 JSON 接口是 UTF-8）
        if "sinajs.cn" in url or "finance.sina.com.cn" in url:
            resp.encoding = "gbk"
        return resp.text

    async def _fetch_history(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """拉取新浪 K 线 JSON 历史数据。"""
        import json

        url = f"{_SINA_KLINE_URL}?{urlencode(params)}"
        text = await self._fetch(url)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"Sina K 线 JSON 解析失败: {exc}", provider="sina", method="get_daily_bars", cause=exc,
            ) from exc
        if isinstance(data, dict):
            # 接口异常时返回 {symbol/msg} 结构，而不是 K 线数组
            raise ProviderError(
                f"Sina K 线接口异常返回: {str(data)[:200]}",
                provider="sina", method="get_daily_bars",
            )
        return data or []

    async def get_tencent_quote(self, symbols: list[str] | str) -> dict[str, dict[str, Any]]:
        """批量实时行情（与 a_stock_data 同 method，同 list[dict] 契约）。

        Args:
            symbols: 代码列表（['sh600519', 'sz000636']，已带市场前缀或裸 6 位）；
                     也兼容单个字符串（自动包装为列表）。

        Returns:
            dict[symbol -> {name, open, prev_close, current, high, low, volume, amount, date, time}]
            全部失败时抛 ProviderError（不静默返回空）。
        """
        if isinstance(symbols, str):
            symbols = [symbols]
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
        if not out:
            raise ProviderError(
                f"Sina 实时行情无数据: {symbols}", provider="sina", method="get_tencent_quote",
            )
        return out

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        scale: int = 240,
        datalen: int = 60,
        *,
        days: int | None = None,
    ) -> pd.DataFrame:
        """日 K 线（与 akshare / baostock / tushare 的 get_daily_bars 位置参数兼容）。

        Args:
            symbol:     6 位代码
            start_date: 起始日期（YYYYMMDD 或 YYYY-MM-DD，可选）— 启用后对结果做 post-filter
            end_date:   结束日期（同上，可选）
            scale:      周期（240=日 / 1680=周 / 43200=月）
            datalen:    返回条数
            days:       兼容上层调用（等价于 datalen，优先使用）

        Returns:
            pd.DataFrame，列：date / open / high / low / close / volume
            （与 akshare/baostock/tushare 的 get_daily_bars 返回类型对齐）。
            如果给了 start_date / end_date，结果会按 [start, end] 闭区间过滤。
        """
        if days is not None:
            datalen = days
        # 历史区间：自动扩 datalen 覆盖 start_date（新浪只支持“最近 N 根”）
        datalen = _estimate_datalen(start_date, int(scale), int(datalen))
        params = {
            "symbol": _to_sina_symbol(symbol),
            "scale": int(scale),
            "datalen": int(datalen),
        }
        data = await self._fetch_history(params)
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
            bars = _filter_bars_by_date(bars, start_date, end_date)
        return pd.DataFrame(bars)

    async def get_kline(
        self,
        code: str,
        frequency: int = 9,
        offset: int = 60,
    ) -> list[dict[str, Any]]:
        """get_kline 路由兼容端点（签名与 a_stock_data.get_kline 对齐）。

        Args:
            code:      6 位代码
            frequency: 通达信周期码（9=日 / 5/15/30/60=分钟），映射到 sina scale
            offset:    返回条数（映射到 sina datalen）

        Returns:
            list[dict]，每条含 date / open / high / low / close / volume（与 get_kline 契约一致）。
        """
        # 通达信 frequency → sina scale（240=日线）
        scale = {9: 240, 240: 240}.get(int(frequency), int(frequency) if int(frequency) in (5, 15, 30, 60) else 240)
        df = await self.get_daily_bars(code, scale=scale, datalen=max(int(offset), 1))
        return df.to_dict(orient="records")

    async def get_index_daily_bars(
        self,
        symbol: str,
        datalen: int = 500,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """指数日 K 线（sina 指数接口，需 sh/sz 前缀）。

        Args:
            symbol:     "000300" / "000300.SH" / "sh000300"（自动推断 sh/sz）
            datalen:    返回条数（默认 500 ≈ 2 年）
            start_date: 起始日期过滤
            end_date:   结束日期过滤

        Returns:
            pd.DataFrame，列：date / open / high / low / close / volume

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
            "datalen": _estimate_datalen(start_date, 240, int(datalen)),
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
            bars = _filter_bars_by_date(bars, start_date, end_date)
        return pd.DataFrame(bars)

    async def get_minute_bars(
        self,
        symbol: str,
        start_datetime: str | None = None,
        end_datetime: str | None = None,
        scale: int = 60,
        datalen: int = 240,
    ) -> pd.DataFrame:
        """分钟 K 线（与 akshare get_minute_bars 同 method 契约）。

        Args:
            symbol:         6 位代码
            start_datetime: 起始时间（"YYYYMMDD HH:MM:SS" 或 "YYYY-MM-DD"，可选）
            end_datetime:   结束时间（同上，可选）
            scale:          周期（5/15/30/60 分钟；sina 无 1 分钟接口）
            datalen:        返回条数
        """
        start_date = start_datetime[:10].replace("-", "") if start_datetime else None
        end_date = end_datetime[:10].replace("-", "") if end_datetime else None
        return await self.get_daily_bars(
            symbol, scale=scale, datalen=datalen,
            start_date=start_date, end_date=end_date,
        )

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
        raise ProviderError(
            f"Sina 个股信息为空: {symbol}", provider="sina", method="get_stock_info",
        )

    # ── 板块数据（不依赖东财的独立源）──────────────────────────────

    async def get_industry_ranking(self, limit: int = 90, **kwargs: Any) -> list[dict[str, Any]]:
        """行业板块涨跌排名（新浪，不依赖东财）。

        Args:
            limit: 返回条数

        Returns:
            [{board_code, board_name, member_count, avg_price, change_pct, change,
              volume, amount, lead_code, lead_change_pct, lead_price, lead_change, lead_stock}, ...]
            按涨跌幅降序。字段名与 eastmoney / a_stock_data 板块接口对齐。
        """
        text = await self._fetch(_SINA_SECTOR_LIST_URL)
        rows = _parse_sina_sector(text)
        rows.sort(key=lambda r: r.get("change_pct", 0.0), reverse=True)
        return rows[: max(int(limit or 90), 1)]

    async def get_sector_fund_flow(self, sector_name: str = "", **kwargs: Any) -> pd.DataFrame:
        """行业板块资金流（新浪 newFLJK，不依赖东财）。

        Args:
            sector_name: 板块名过滤（子串匹配）；空=返回全部

        Returns:
            pd.DataFrame，列与 akshare get_sector_fund_flow 对齐：
            sector / main_net_inflow / net_inflow / pct_change / amount / lead_name
            （保证 fallback 链上各源返回类型一致）。
        """
        text = await self._fetch(_SINA_SECTOR_FLOW_URL.format(param="industry"))
        rows = _parse_sina_sector(text)
        if sector_name:
            rows = [r for r in rows if sector_name in r.get("board_name", "")]
        # 新浪资金流字段口径：pct_change=板块涨跌幅；amount=成交额（非净流入）。
        # 新浪无「主力净流入」，net_inflow 置空并保留 amount 供指标层判断。
        return pd.DataFrame(
            [
                {
                    "sector": r.get("board_name", ""),
                    "main_net_inflow": None,
                    "net_inflow": None,
                    "pct_change": r.get("change_pct", 0.0),
                    "amount": r.get("amount", 0.0),
                    "lead_name": r.get("lead_stock", ""),
                }
                for r in rows
            ]
        )
