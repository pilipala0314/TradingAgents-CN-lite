"""ticker 归一化与市场前缀纯函数单测。"""
import pytest

from datasource.providers._shared.ticker import em_market_code, market_prefix, normalize_ticker


@pytest.mark.unit
class TestNormalizeTicker:
    def test_bare_6digit(self):
        assert normalize_ticker("688017") == "688017"

    def test_upper_prefix(self):
        assert normalize_ticker("SH688017") == "688017"
        assert normalize_ticker("SZ000001") == "000001"
        assert normalize_ticker("BJ832000") == "832000"

    def test_lower_prefix(self):
        assert normalize_ticker("sh688017") == "688017"
        assert normalize_ticker("sz000001") == "000001"
        assert normalize_ticker("bj832000") == "832000"

    def test_suffix_dot(self):
        # 回归：历史上 "688017.SH" 被错误返回为 "688017SH"
        assert normalize_ticker("688017.SH") == "688017"
        assert normalize_ticker("688017.sh") == "688017"
        assert normalize_ticker("000001.SZ") == "000001"
        assert normalize_ticker("832000.BJ") == "832000"

    def test_empty(self):
        assert normalize_ticker("") == ""
        assert normalize_ticker(None) == ""  # falsy 输入防御

    def test_whitespace(self):
        assert normalize_ticker("  600519  ") == "600519"


@pytest.mark.unit
class TestMarketPrefix:
    def test_sh(self):
        assert market_prefix("600519") == "sh"
        assert market_prefix("900901") == "sh"

    def test_sz(self):
        assert market_prefix("000001") == "sz"
        assert market_prefix("300750") == "sz"

    def test_bj(self):
        assert market_prefix("430047") == "bj"
        assert market_prefix("832000") == "bj"
        # 92 段必须先于 9→sh 判断
        assert market_prefix("920001") == "bj"


@pytest.mark.unit
class TestEmMarketCode:
    def test_sh_is_1(self):
        assert em_market_code("600519") == 1

    def test_sz_is_0(self):
        assert em_market_code("000001") == 0

    def test_bj_is_0(self):
        # 东财北交所 secid 复用市场段 0
        assert em_market_code("832000") == 0
