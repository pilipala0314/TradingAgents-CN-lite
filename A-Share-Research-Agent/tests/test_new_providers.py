"""Sina / Netease / HK Southbound / ResearchRAG 4 个新 Provider 的单测。

测试目标：
- 接口契约（symbol 转换、返回值字段）
- 失败语义（无静默降级）
- 内存分词 / 打分逻辑
- HTTP 不外发（httpx.MockTransport 注入）
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from data_pipeline.providers.hk_southbound_provider import HKSouthboundProvider
from data_pipeline.providers.netease_provider import (
    NeteaseProvider,
    _to_netease_symbol,
)
from data_pipeline.providers.research_rag_provider import (
    ResearchRAGProvider,
    _score_chunk,
    _tokenize,
)
from data_pipeline.providers.sina_provider import (
    SinaProvider,
    _filter_bars_by_date,
    _parse_hq_line,
    _to_sina_symbol,
)

# ── Sina symbol 转换 ──────────────────────────────────────────────

class TestSinaSymbol:
    def test_bare_sh(self) -> None:
        assert _to_sina_symbol("600519") == "sh600519"

    def test_bare_sz(self) -> None:
        assert _to_sina_symbol("000636") == "sz000636"
        assert _to_sina_symbol("300750") == "sz300750"

    def test_bare_bj(self) -> None:
        assert _to_sina_symbol("430047") == "bj430047"
        assert _to_sina_symbol("830799") == "bj830799"

    def test_with_dot_prefix(self) -> None:
        assert _to_sina_symbol("sh600519") == "sh600519"
        assert _to_sina_symbol("sz000636") == "sz000636"
        assert _to_sina_symbol("bj830799") == "bj830799"

    def test_lowercase_input(self) -> None:
        assert _to_sina_symbol("SH600519") == "sh600519"
        assert _to_sina_symbol("SZ000636") == "sz000636"


# ── Netease symbol 转换 ──────────────────────────────────────────

class TestNeteaseSymbol:
    def test_sh(self) -> None:
        assert _to_netease_symbol("600519") == "0600519"

    def test_sz(self) -> None:
        assert _to_netease_symbol("000636") == "1000636"
        assert _to_netease_symbol("300750") == "1300750"

    def test_with_prefix(self) -> None:
        assert _to_netease_symbol("sh600519") == "0600519"
        assert _to_netease_symbol("sz000636") == "1000636"

    def test_bj_fallback(self) -> None:
        assert _to_netease_symbol("430047").startswith("0")


# ── Sina 行情行解析 ──────────────────────────────────────────────

class TestSinaParseLine:
    def test_parse_hq_line_ok(self) -> None:
        line = (
            'var hq_str_sh600519="贵州茅台,1800.00,1790.00,1810.00,1820.00,1780.00,'
            '1805.00,1811.00,12345678,1234567890.0,100,200,300,400,500,600,700,800,'
            '900,1000,1100,1200,1300,1400,1500,1600,1700,1800,1900,2000,2024-01-15,09:30:00,2100";'
        )
        out = _parse_hq_line(line)
        assert out is not None
        assert out["name"] == "贵州茅台"
        assert out["open"] == 1800.0
        assert out["current"] == 1810.0
        assert out["volume"] == 12345678
        assert out["date"] == "2024-01-15"

    def test_parse_hq_line_short(self) -> None:
        assert _parse_hq_line('var hq_str_sh600519="short";') is None

    def test_parse_hq_line_no_quote(self) -> None:
        assert _parse_hq_line("var hq_str_sh600519=no_quote;") is None


# ── Sina 端到端（httpx MockTransport） ──────────────────────────

def _sina_hq_response_body() -> str:
    line1 = (
        'var hq_str_sh600519="贵州茅台,1800.00,1790.00,1810.00,1820.00,1780.00,'
        '1805.00,1811.00,12345678,1234567890.0,100,200,300,400,500,600,700,800,'
        '900,1000,1100,1200,1300,1400,1500,1600,1700,1800,1900,2000,2024-01-15,09:30:00,2100";'
    )
    line2 = (
        'var hq_str_sz000636="风华高科,15.00,14.90,15.10,15.20,14.80,'
        '15.05,15.11,1234567,12345678.0,10,20,30,40,50,60,70,80,'
        '90,100,110,120,130,140,150,160,170,180,190,200,2024-01-15,09:30:00,2100";'
    )
    return line1 + "\n" + line2 + "\n"


@pytest.mark.asyncio
async def test_sina_get_tencent_quote_via_mock() -> None:
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        assert request.headers.get("Referer") == "https://finance.sina.com.cn/"
        # Sina 实际返回 GBK 编码字节流，mock 也要发 GBK 才能验证 _fetch 的编码处理
        return httpx.Response(200, content=_sina_hq_response_body().encode("gbk"))

    provider = SinaProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    result = await provider.get_tencent_quote(["600519", "000636"])
    assert "sh600519" in captured_urls[0]
    assert "sz000636" in captured_urls[0]
    assert result["600519"]["name"] == "贵州茅台"
    assert result["000636"]["current"] == 15.10


@pytest.mark.asyncio
async def test_sina_get_daily_bars_via_mock() -> None:
    kline_data = [
        {"day": "2024-01-15", "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
        {"day": "2024-01-16", "open": 105, "high": 115, "low": 100, "close": 112, "volume": 2000},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "CN_MarketData.getKLineData" in str(request.url)
        assert "symbol=sh600519" in str(request.url)
        return httpx.Response(200, text=json.dumps(kline_data))

    provider = SinaProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    result = await provider.get_daily_bars("600519", scale=240, datalen=60)
    assert len(result) == 2
    assert result[0]["date"] == "2024-01-15"
    assert result[1]["close"] == 112


@pytest.mark.asyncio
async def test_sina_get_daily_bars_with_date_filter() -> None:
    """验证 start_date / end_date 透传后做 post-filter 截断。"""
    kline_data = [
        {"day": "2024-01-15", "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
        {"day": "2024-01-16", "open": 105, "high": 115, "low": 100, "close": 112, "volume": 2000},
        {"day": "2024-01-17", "open": 112, "high": 120, "low": 110, "close": 118, "volume": 3000},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps(kline_data))

    provider = SinaProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    # start_date / end_date 限定到 16 日
    result = await provider.get_daily_bars(
        "600519", scale=240, datalen=60,
        start_date="20240116", end_date="20240116",
    )
    assert len(result) == 1
    assert result[0]["date"] == "2024-01-16"

    # 仅 start_date 限制
    result = await provider.get_daily_bars(
        "600519", scale=240, datalen=60, start_date="20240116",
    )
    assert len(result) == 2
    assert result[0]["date"] == "2024-01-16"
    assert result[1]["date"] == "2024-01-17"

    # dashed 格式归一化
    result = await provider.get_daily_bars(
        "600519", scale=240, datalen=60,
        start_date="2024-01-16", end_date="2024-01-16",
    )
    assert len(result) == 1
    assert result[0]["date"] == "2024-01-16"


# ── Sina date filter helper ───────────────────────────────────

class TestSinaFilterBarsByDate:
    def test_no_filter_returns_all(self) -> None:
        bars = [{"date": "2024-01-15"}, {"date": "2024-01-16"}]
        assert _filter_bars_by_date(bars, None, None) == bars

    def test_start_only(self) -> None:
        bars = [
            {"date": "2024-01-14"}, {"date": "2024-01-15"},
            {"date": "2024-01-16"}, {"date": "2024-01-17"},
        ]
        out = _filter_bars_by_date(bars, "20240115", None)
        assert [b["date"] for b in out] == ["2024-01-15", "2024-01-16", "2024-01-17"]

    def test_end_only(self) -> None:
        bars = [
            {"date": "2024-01-14"}, {"date": "2024-01-15"},
            {"date": "2024-01-16"},
        ]
        out = _filter_bars_by_date(bars, None, "20240115")
        assert [b["date"] for b in out] == ["2024-01-14", "2024-01-15"]

    def test_range(self) -> None:
        bars = [
            {"date": "2024-01-14"}, {"date": "2024-01-15"},
            {"date": "2024-01-16"}, {"date": "2024-01-17"},
        ]
        out = _filter_bars_by_date(bars, "20240115", "20240116")
        assert [b["date"] for b in out] == ["2024-01-15", "2024-01-16"]

    def test_normalizes_formats(self) -> None:
        bars = [{"date": "2024-01-15"}]
        assert len(_filter_bars_by_date(bars, "2024/01/15", "2024-01-15")) == 1


@pytest.mark.asyncio
async def test_sina_get_stock_info_via_mock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sina_hq_response_body().encode("gbk"))

    provider = SinaProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    info = await provider.get_stock_info("600519")
    assert info["name"] == "贵州茅台"
    assert info["current"] == 1810.0


# ── Netease 端到端 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_netease_get_tencent_quote_via_mock() -> None:
    jsonp_body = (
        '_ntes_quote_callback({"0600519":{"code":"0600519","name":"贵州茅台",'
        '"open":1800,"yestclose":1790,"price":1810,"high":1820,"low":1780,'
        '"volume":12345,"turnover":12345678,"time":"2024-01-15 09:30:00"}});'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.money.126.net" in str(request.url)
        return httpx.Response(200, text=jsonp_body)

    provider = NeteaseProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    result = await provider.get_tencent_quote(["600519"])
    assert result["600519"]["name"] == "贵州茅台"
    assert result["600519"]["current"] == 1810.0
    assert result["600519"]["volume"] == 12345


@pytest.mark.asyncio
async def test_netease_get_tencent_quote_empty_raises() -> None:
    """无数据 → 抛错（不静默降级）。"""
    jsonp_body = '_ntes_quote_callback({});'  # 完全空响应（无任何 key）

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=jsonp_body)

    provider = NeteaseProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="无 600519 数据"):
        await provider.get_tencent_quote(["600519"])


@pytest.mark.asyncio
async def test_netease_get_daily_bars_via_mock() -> None:
    csv_body = (
        "日期,股票代码,名称,收盘,最高,最低,开盘,前收,涨跌额,涨跌幅,换手率,成交量,成交金额,总市值,流通市值\n"
        "2024-01-15,'0600519,贵州茅台,1810,1820,1780,1800,1790,20,1.12,0.5,12345,12345678,999,888\n"
        "2024-01-16,'0600519,贵州茅台,1820,1830,1800,1810,1810,10,0.55,0.4,10000,10000000,1000,889\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=csv_body)

    provider = NeteaseProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    result = await provider.get_daily_bars("600519")
    assert len(result) == 2
    assert result[0]["date"] == "2024-01-15"
    assert result[0]["close"] == 1810.0
    assert result[0]["volume"] == 12345


@pytest.mark.asyncio
async def test_netease_get_daily_bars_with_start_end() -> None:
    """验证 netease 把 start / end kwargs 透传到 URL params（修复 adapter 不重命名的 bug）。"""
    captured_url: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_url.append(str(request.url))
        return httpx.Response(200, text=(
            "日期,股票代码,名称,收盘,最高,最低,开盘,前收,涨跌额,涨跌幅,换手率,成交量,成交金额,总市值,流通市值\n"
            "2024-01-15,'0600519,贵州茅台,1810,1820,1780,1800,1790,20,1.12,0.5,12345,12345678,999,888\n"
        ))

    provider = NeteaseProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    await provider.get_daily_bars("600519", start="20240115", end="20240115")
    assert len(captured_url) == 1
    assert "start=20240115" in captured_url[0]
    assert "end=20240115" in captured_url[0]


# ── HKSouthbound 端到端 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_hk_southbound_get_flow_via_mock() -> None:
    sh_resp = {"data": {"f51": 1.0e9, "f52": 2.5e8, "f54": "腾讯控股", "f55": 3.5}, "dt": 1705276800}
    sz_resp = {"data": {"f51": 8.0e8, "f52": 1.5e8, "f54": "美团", "f55": 2.0}, "dt": 1705276800}
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        url = str(request.url)
        if "market=001" in url:
            return httpx.Response(200, json=sh_resp)
        if "market=002" in url:
            return httpx.Response(200, json=sz_resp)
        return httpx.Response(404)

    provider = HKSouthboundProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    result = await provider.get_southbound_flow()
    assert call_count["n"] == 2
    assert result["sh"]["market"] == "沪港通南向"
    assert result["sh"]["net_buy"] == 2.5e8
    assert result["sh"]["top_stock"] == "腾讯控股"
    assert result["sz"]["top_stock"] == "美团"


@pytest.mark.asyncio
async def test_hk_southbound_get_top_stocks_via_mock() -> None:
    body = {
        "data": [
            {"f12": "00700", "f14": "腾讯控股", "f2": 350.0, "f3": 2.5},
            {"f12": "09988", "f14": "阿里巴巴", "f2": 80.0, "f3": 1.8},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    provider = HKSouthboundProvider(timeout=2.0, transport=httpx.MockTransport(handler))
    result = await provider.get_top_stocks(market="001", topn=5)
    assert len(result) == 2
    assert result[0]["name"] == "腾讯控股"
    assert result[0]["chg_pct"] == 2.5


# ── ResearchRAG 内存分词 + 打分 ─────────────────────────────────

def test_tokenize_basic() -> None:
    """分词：中文按字 + 英文按词 + 过滤停用词。"""
    tokens = _tokenize("贵州茅台 业绩超预期 增长 AI 5G")
    # 中文单字保留
    assert "贵" in tokens
    assert "茅" in tokens
    assert "台" in tokens
    # 英文 / 缩写
    assert "ai" in tokens
    assert "5g" in tokens
    # 停用词不在
    assert "的" not in tokens
    assert "和" not in tokens


def test_tokenize_empty() -> None:
    assert _tokenize("") == []
    assert _tokenize("   ") == []


def test_tokenize_filter_single_letter() -> None:
    """过滤单字母（噪声），保留 'a' / 'i'。"""
    tokens = _tokenize("A B C 贵州 a i")
    assert "a" in tokens
    assert "i" in tokens
    assert "b" not in tokens
    assert "c" not in tokens


def test_score_chunk_basic() -> None:
    """打分：query 在 chunk 中出现越多分越高。"""
    chunk_text = "贵州茅台 业绩超预期 增长"
    chunk_tokens = _tokenize(chunk_text)
    s1 = _score_chunk(_tokenize("茅台 业绩"), chunk_tokens, chunk_text)
    s2 = _score_chunk(_tokenize("不相关 词"), chunk_tokens, chunk_text)
    assert s1 > s2
    assert s1 > 0


def test_score_chunk_empty() -> None:
    """空 query / chunk → 0 分（不抛错）。"""
    assert _score_chunk([], ["贵"], "贵州") == 0.0
    assert _score_chunk(["贵"], [], "") == 0.0


# ── ResearchRAG 端到端 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_rag_get_chunks_via_mock() -> None:
    # reportapi 端点真实响应：{"Success": true, "Data": [...]}
    body = {
        "Data": [
            {
                "infoCode": "rpt-001",
                "title": "贵州茅台 — 业绩超预期",
                "orgSName": "中金公司",
                "publishDate": 1705276800000,  # 毫秒时间戳
                "emRatingName": "买入",
                "industryName": "食品饮料",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "reportapi.eastmoney.com" in url
        assert "code=600519" in url
        return httpx.Response(200, json=body)

    provider = ResearchRAGProvider(page_size=10, ttl_sec=60, timeout=2.0, transport=httpx.MockTransport(handler))
    chunks = await provider.get_chunks("600519")
    assert len(chunks) == 1
    assert chunks[0]["title"] == "贵州茅台 — 业绩超预期"
    assert chunks[0]["org"] == "中金公司"
    assert chunks[0]["rating"] == "买入"


@pytest.mark.asyncio
async def test_research_rag_search_relevance() -> None:
    """RAG 检索：按 query 相关度排序。"""
    body = {
        "Data": [
            {
                "infoCode": "r1", "title": "贵州茅台 业绩超预期 增长",
                "orgSName": "A", "publishDate": 1705276800000,
                "emRatingName": "买入", "industryName": "白酒",
            },
            {
                "infoCode": "r2", "title": "AI 行业研究",
                "orgSName": "B", "publishDate": 1705363200000,
                "emRatingName": "中性", "industryName": "科技",
            },
            {
                "infoCode": "r3", "title": "某科技公司",
                "orgSName": "C", "publishDate": 1705449600000,
                "emRatingName": "卖出", "industryName": "科技",
            },
        ]
    }

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=body)

    provider = ResearchRAGProvider(page_size=10, ttl_sec=60, timeout=2.0, transport=httpx.MockTransport(handler))
    # 先 warm 缓存
    await provider.get_chunks("600519")
    assert call_count["n"] == 1
    # 再 search（走缓存）
    results = await provider.search("茅台 业绩", top_k=3)
    assert call_count["n"] == 1  # 缓存命中：不再查
    assert len(results) >= 1
    assert "茅台" in results[0]["title"] or "茅台" in results[0]["content"]


@pytest.mark.asyncio
async def test_research_rag_search_empty_query() -> None:
    provider = ResearchRAGProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert await provider.search("") == []
    assert await provider.search("   ") == []
    assert await provider.search("的 和") == []  # 全停用词


@pytest.mark.asyncio
async def test_research_rag_get_chunks_empty_response() -> None:
    """无研报时返回空 list（不抛错）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Data": []})

    provider = ResearchRAGProvider(page_size=10, ttl_sec=60, timeout=2.0, transport=httpx.MockTransport(handler))
    chunks = await provider.get_chunks("999999")
    assert chunks == []


@pytest.mark.asyncio
async def test_research_rag_cache_ttl() -> None:
    """TTL 过期后重新拉取。"""
    body = {"result": {"data": []}}
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=body)

    provider = ResearchRAGProvider(
        page_size=10, ttl_sec=0, timeout=2.0, transport=httpx.MockTransport(handler)
    )
    await provider.get_chunks("600519")
    await provider.get_chunks("600519")
    # ttl=0 → 每次都重新拉
    assert call_count["n"] == 2
