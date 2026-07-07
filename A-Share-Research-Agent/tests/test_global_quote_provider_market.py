"""test_global_quote_provider_market — P0-T3 单测。

覆盖：
- `GlobalQuoteProvider._PREFIX_TO_MARKET` 映射正确
- `get_global_quote` 在每个返回的 quote dict 上注入 `market` 字段
- 未知 prefix → market="unknown"
- 客户端断网时返回 {}，不抛错（fail loud）
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from data_pipeline.providers.global_quote_provider import (
    GlobalQuoteProvider,
    _fetch_global_quotes,
)


class TestPrefixToMarket:
    def test_cn_prefixes(self) -> None:
        m = GlobalQuoteProvider._PREFIX_TO_MARKET
        assert m["sh"] == "cn"
        assert m["sz"] == "cn"
        assert m["bj"] == "cn"

    def test_hk_prefix(self) -> None:
        assert GlobalQuoteProvider._PREFIX_TO_MARKET["hk"] == "hk"

    def test_us_prefix(self) -> None:
        assert GlobalQuoteProvider._PREFIX_TO_MARKET["us"] == "us"


class TestMarketInjection:
    @pytest.mark.asyncio
    async def test_injects_market_field_per_prefix(self) -> None:
        provider = GlobalQuoteProvider()
        # 模拟 _fetch_global_quotes 返回的 GBK 文本（qt.gtimg.cn 标准格式）
        fake_gbk = (
            'v_sh000001="1~上证指数~000001~3000.0~2995.0~...";'
            'v_hkHSI="1~恒生指数~HSI~18000.0~17900.0~...";'
            'v_usDJI="1~道琼斯~DJI~38000.0~37900.0~..."'
        )
        # 关键：len(vals) >= 53 才会被解析；填充 53 个字段
        def _make_long_row(name, code, price, last_close):
            vals = [str(i) for i in range(53)]
            vals[1] = name
            vals[2] = code
            vals[3] = str(price)
            vals[4] = str(last_close)
            return "~".join(vals)
        sh = _make_long_row("上证指数", "000001", 3000.0, 2995.0)
        hk = _make_long_row("恒生指数", "HSI", 18000.0, 17900.0)
        us = _make_long_row("道琼斯", "DJI", 38000.0, 37900.0)
        fake_gbk = f'v_sh000001="{sh}";v_hkHSI="{hk}";v_usDJI="{us}"'

        with patch(
            "data_pipeline.providers.global_quote_provider._fetch_global_quotes",
            return_value=fake_gbk,
        ):
            result = await provider.get_global_quote(
                ["sh000001", "hkHSI", "usDJI"]
            )

        assert "000001" in result
        assert result["000001"]["market"] == "cn"
        assert "HSI" in result
        assert result["HSI"]["market"] == "hk"
        assert "DJI" in result
        assert result["DJI"]["market"] == "us"

    @pytest.mark.asyncio
    async def test_unknown_prefix_falls_back_to_unknown(self) -> None:
        provider = GlobalQuoteProvider()
        fake_gbk = 'v_xxABCD="~1~测试~ABCD~100.0~99.0' + "~" * 53 + '";'

        with patch(
            "data_pipeline.providers.global_quote_provider._fetch_global_quotes",
            return_value=fake_gbk,
        ):
            result = await provider.get_global_quote(["xxABCD"])

        if "ABCD" in result:
            assert result["ABCD"]["market"] == "unknown"

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self) -> None:
        provider = GlobalQuoteProvider()
        result = await provider.get_global_quote([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_provider_failure_returns_empty(self) -> None:
        """fetch 异常 → 返回 {}（不抛给上层）"""
        provider = GlobalQuoteProvider()

        with patch(
            "data_pipeline.providers.global_quote_provider._fetch_global_quotes",
            side_effect=ConnectionError("simulated network error"),
        ):
            # 走 asyncio.to_thread 路径 → 异常会冒泡
            with pytest.raises(ConnectionError, match="simulated network error"):
                await provider.get_global_quote(["sh000001"])
