"""akshare provider 纯函数单测（模块级 import akshare 失败时不崩溃，ak=None）。"""
import pandas as pd
import pytest

from datasource.providers.akshare import (
    AkshareProvider,
    _extract_market_prefixed_symbol,
    _filter_by_symbol,
)


@pytest.mark.unit
class TestExtractMarketPrefixedSymbol:
    def test_sh(self):
        assert _extract_market_prefixed_symbol("600519") == "SH600519"

    def test_sz(self):
        assert _extract_market_prefixed_symbol("000001") == "SZ000001"
        assert _extract_market_prefixed_symbol("300750") == "SZ300750"

    def test_bj(self):
        assert _extract_market_prefixed_symbol("430047") == "BJ430047"

    def test_already_prefixed(self):
        assert _extract_market_prefixed_symbol("SH600519") == "SH600519"


@pytest.mark.unit
class TestFilterBySymbol:
    def test_chinese_column(self):
        df = pd.DataFrame({"股票代码": ["600519", "600000", "600519"]})
        out = _filter_by_symbol(df, "600519")
        assert len(out) == 2
        assert (out["股票代码"] == "600519").all()

    def test_english_column(self):
        df = pd.DataFrame({"symbol": ["000001", "000002"]})
        out = _filter_by_symbol(df, "000001")
        assert len(out) == 1

    def test_suffix_input(self):
        # 带后缀输入应先剥离市场段
        df = pd.DataFrame({"代码": ["600519"]})
        assert len(_filter_by_symbol(df, "600519.SH")) == 1

    def test_no_code_column_returns_original(self):
        df = pd.DataFrame({"a": [1]})
        assert _filter_by_symbol(df, "600519") is df


@pytest.mark.unit
class TestIsNonRetryableError:
    def test_keyword_hit(self):
        assert AkshareProvider._is_non_retryable_error(ValueError("只能获取最近30个交易日的数据")) is True
        assert AkshareProvider._is_non_retryable_error(ValueError("参数错误")) is True
        assert AkshareProvider._is_non_retryable_error(ValueError("不存在")) is True

    def test_transient(self):
        assert AkshareProvider._is_non_retryable_error(ConnectionError("timeout")) is False
        assert AkshareProvider._is_non_retryable_error(ValueError("unknown message")) is False


@pytest.mark.unit
class TestAkshareEmptyDataBug:
    """回归：akshare 空数据 bug 必须原样抛 TypeError，让调用方 except TypeError 返回空表。

    若被 _run_with_retry 包装成 ProviderError，调用方的兜底分支就失效了
    （曾导致龙虎榜「无数据」被当成「拉取失败」）。
    """

    def test_none_subscriptable_detected(self):
        exc = TypeError("'NoneType' object is not subscriptable")
        assert AkshareProvider._is_akshare_empty_data_bug(exc) is True

    def test_other_type_error_not_flagged(self):
        assert AkshareProvider._is_akshare_empty_data_bug(TypeError("bad type")) is False

    def test_non_type_error_not_flagged(self):
        assert AkshareProvider._is_akshare_empty_data_bug(ValueError("not subscriptable")) is False

    def test_run_with_retry_reraises_empty_data_typeerror(self):
        """空数据 bug 必须原样透出 TypeError（不被包装成 ProviderError）。"""
        import asyncio

        from datasource.errors import ProviderError

        p = AkshareProvider()

        def _boom():
            raise TypeError("'NoneType' object is not subscriptable")

        async def main():
            return await p._run_with_retry(_boom)

        with pytest.raises(TypeError):
            asyncio.run(main())
        # 反向确认：非空数据 bug 的异常会被包装成 ProviderError
        def _other():
            raise ValueError("some real error")

        async def main2():
            return await p._run_with_retry(_other)

        with pytest.raises(ProviderError):
            asyncio.run(main2())
