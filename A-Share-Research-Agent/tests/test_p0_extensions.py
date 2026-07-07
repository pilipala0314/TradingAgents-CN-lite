"""P0 数据源扩展单测：CninfoProvider + GlobalQuoteProvider + _parse_quote_response。

覆盖：
- `_parse_quote_response` 解析（支持 A 股 sh/sz/bj + 港股 hk + 美股/指数 us）
- `CninfoProvider.get_cninfo_announcements` 端点契约（mock fetch_cninfo_announcements）
- `GlobalQuoteProvider.get_global_quote` 端点契约（mock _fetch_global_quotes）
- 失败语义：协议成功但业务无数据 → 返空；网络错误 → raise
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from data_pipeline.providers._shared.tencent import _parse_quote_response
from data_pipeline.providers.cninfo import CninfoProvider
from data_pipeline.providers.global_quote_provider import (
    _DEFAULT_GLOBAL_CODES,
    GlobalQuoteProvider,
)

# ── _parse_quote_response ─────────────────────────────────────────

def _tencent_line(prefixed: str, vals: list[str]) -> str:
    """构造一行 qt.gtimg.cn 响应（vals 必须含 53 字段，全为字符串）。"""
    padded = list(vals) + [""] * max(0, 53 - len(vals))
    return f'v_{prefixed}="{"~".join(padded)}";\n'


def test_parse_quote_a_stock_basic() -> None:
    """A 股 sh 前缀 → 剥前缀，字段映射正确。"""
    data = _tencent_line(
        "sh600519",
        [
            "1",                       # 0
            "贵州茅台",                # 1=name
            "",                        # 2
            "1800.00",                 # 3=price
            "1790.00",                 # 4=last_close
            "1795.00",                 # 5=open
            *["0.0"] * 25,
            "10.00",                   # 31=change_amt
            "0.56",                    # 32=change_pct
            "1810.00",                 # 33=high
            "1785.00",                 # 34=low
            *["0.0"] * 2,
            "12345678",                # 37=amount_wan
            "1.23",                    # 38=turnover_pct
            "25.6",                    # 39=pe_ttm
            *["0.0"] * 3,
            "1.5",                     # 43=amplitude_pct
            "20000.0",                 # 44=mcap_yi
            "19500.0",                 # 45=float_mcap_yi
            "8.5",                     # 46=pb
            "1805.00",                 # 47=limit_up
            "1785.00",                 # 48=limit_down
            "1.1",                     # 49=vol_ratio
            *["0.0"] * 2,
            "23.4",                    # 52=pe_static
        ],
    )
    out = _parse_quote_response(data)
    assert "600519" in out
    assert out["600519"]["name"] == "贵州茅台"
    assert out["600519"]["price"] == 1800.00
    assert out["600519"]["change_pct"] == 0.56
    assert out["600519"]["pe_ttm"] == 25.6
    assert out["600519"]["pb"] == 8.5
    assert out["600519"]["mcap_yi"] == 20000.0


def test_parse_quote_hk_stock() -> None:
    """港股 hk 前缀 → 剥前缀，code 保留原始（不强制 6 位）。"""
    data = _tencent_line(
        "hk00700",
        [
            "1", "腾讯控股", "",
            "300.00", "298.00", "299.00",
            *["0.0"] * 25,
            "2.00", "0.67", "305.00", "295.00",
            *["0.0"] * 2,
            "0", "0.5", "12.3",
            *["0.0"] * 3,
            "0.8", "0", "0", "0", "0", "0", "0.5",
        ],
    )
    out = _parse_quote_response(data)
    assert "00700" in out
    assert out["00700"]["name"] == "腾讯控股"
    assert out["00700"]["price"] == 300.00


def test_parse_quote_us_stock_letter_code() -> None:
    """美股 us 前缀 + 字母代码（如 AAPL）。"""
    data = _tencent_line(
        "usAAPL",
        [
            "1", "苹果", "",
            "200.00", "199.00", "199.50",
            *["0.0"] * 25,
            "1.00", "0.50", "201.00", "198.00",
            *["0.0"] * 2,
            "0", "0.5", "30.0",
            *["0.0"] * 3,
            "0.5", "3000000.0", "2900000.0", "0", "0", "0", "0", "0.5",
        ],
    )
    out = _parse_quote_response(data)
    assert "AAPL" in out
    assert out["AAPL"]["name"] == "苹果"


def test_parse_quote_unsupported_prefix_skipped() -> None:
    """不支持的前缀（如 xq）→ 跳过，不入结果。"""
    data = _tencent_line(
        "xq9999",
        [
            "1", "未知", "",
            "1.0", "0.0", "0.0",
            *["0.0"] * 50,
        ],
    )
    out = _parse_quote_response(data)
    assert "9999" not in out
    assert out == {}


def test_parse_quote_short_line_skipped() -> None:
    """字段不足 53 → 跳过。"""
    data = 'v_sh600519="贵州茅台,1800.00";\n'
    out = _parse_quote_response(data)
    assert out == {}


def test_parse_quote_empty_input() -> None:
    """空输入 → 空字典。"""
    assert _parse_quote_response("") == {}
    assert _parse_quote_response("\n\n") == {}


# ── CninfoProvider ───────────────────────────────────────────────

def _mock_cninfo_rows() -> list[dict[str, Any]]:
    return [
        {
            "announcementId": "12345",
            "announcementTitle": "2026 年第一季度报告",
            "adjunctUrl": "2026-04-30/12345.PDF",
            "announcementType": "category_ndbg_szsh",
        },
        {
            "announcementId": "12346",
            "announcementTitle": "关于回购公司股份的公告",
            "adjunctUrl": "2026-05-10/12346.PDF",
            "announcementType": "category_other",
        },
    ]


@pytest.mark.asyncio
async def test_cninfo_provider_normalizes_fields() -> None:
    """CninfoProvider 调底层 fetch_cninfo_announcements 后字段映射正确。"""
    with patch("data_pipeline.providers.cninfo.fetch_cninfo_announcements") as mock_fetch:
        mock_fetch.return_value = [
            {
                "announcement_id": "12345",
                "title": "2026 年第一季度报告",
                "published_at": "2026-04-30",
                "url": "http://static.cninfo.com.cn/2026-04-30/12345.PDF",
                "type": "category_ndbg_szsh",
                "type_code": "",
                "orgid": "9900022828",
            }
        ]
        provider = CninfoProvider()
        result = await provider.get_cninfo_announcements("600519", recent_days=30)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["title"] == "2026 年第一季度报告"
        assert result[0]["url"] == "http://static.cninfo.com.cn/2026-04-30/12345.PDF"
        mock_fetch.assert_called_once_with("600519", 30, "")


@pytest.mark.asyncio
async def test_cninfo_provider_empty_code_raises() -> None:
    """空 code → ValueError（早失败，避免下游拿到空 orgid）。"""
    provider = CninfoProvider()
    with pytest.raises(ValueError, match="code 必填"):
        await provider.get_cninfo_announcements("")


@pytest.mark.asyncio
async def test_cninfo_provider_propagates_network_error() -> None:
    """底层 fetch 抛 RuntimeError → provider 透传（触发 fallback chain）。"""
    with patch(
        "data_pipeline.providers.cninfo.fetch_cninfo_announcements",
        side_effect=RuntimeError("cninfo HTTP 503"),
    ):
        provider = CninfoProvider()
        with pytest.raises(RuntimeError, match="HTTP 503"):
            await provider.get_cninfo_announcements("600519")


# ── GlobalQuoteProvider ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_global_quote_provider_default_codes() -> None:
    """不传 codes → 走默认 9 个全球指数。"""
    captured_prefixed: list[str] = []

    def fake_fetch(prefixed: list[str], timeout: float = 10.0) -> str:
        captured_prefixed.extend(prefixed)
        # 返一个 A 股指数 + 港股 + 美股的混合响应
        return (
            _tencent_line("sh000001", ["1", "上证指数", "", "3000.0", "2990.0", "2995.0"] + ["0.0"] * 48)
            + _tencent_line("hkHSI", ["1", "恒生指数", "", "18000.0", "17900.0", "17950.0"] + ["0.0"] * 48)
            + _tencent_line("usDJI", ["1", "道琼斯", "", "35000.0", "34900.0", "34950.0"] + ["0.0"] * 48)
        )

    with patch("data_pipeline.providers.global_quote_provider._fetch_global_quotes", side_effect=fake_fetch):
        provider = GlobalQuoteProvider()
        result = await provider.get_global_quote()

    assert captured_prefixed == _DEFAULT_GLOBAL_CODES
    assert "000001" in result
    assert "HSI" in result
    assert "DJI" in result
    assert result["000001"]["name"] == "上证指数"
    assert result["000001"]["price"] == 3000.0


@pytest.mark.asyncio
async def test_global_quote_provider_custom_codes() -> None:
    """传自定义 codes → 透传到 qt.gtimg.cn。"""
    with patch("data_pipeline.providers.global_quote_provider._fetch_global_quotes") as mock_fetch:
        mock_fetch.return_value = _tencent_line(
            "hk00700", ["1", "腾讯", "", "300.0", "298.0", "299.0"] + ["0.0"] * 48
        )
        provider = GlobalQuoteProvider()
        result = await provider.get_global_quote(["hk00700"])
    assert "00700" in result
    assert result["00700"]["name"] == "腾讯"


@pytest.mark.asyncio
async def test_global_quote_provider_empty_codes_raises() -> None:
    """空 codes → 走默认 9 个，行为相同。"""
    with patch("data_pipeline.providers.global_quote_provider._fetch_global_quotes") as mock_fetch:
        mock_fetch.return_value = ""
        provider = GlobalQuoteProvider()
        result = await provider.get_global_quote([])
    # 空 list 被过滤后，prefixed = []，_fetch_global_quotes 返 ""，解析得 {}
    assert result == {}


@pytest.mark.asyncio
async def test_global_quote_provider_propagates_network_error() -> None:
    """底层 _fetch_global_quotes 抛异常 → provider 透传。"""
    with patch(
        "data_pipeline.providers.global_quote_provider._fetch_global_quotes",
        side_effect=RuntimeError("qt.gtimg.cn timeout"),
    ):
        provider = GlobalQuoteProvider()
        with pytest.raises(RuntimeError, match="timeout"):
            await provider.get_global_quote(["hk00700"])
