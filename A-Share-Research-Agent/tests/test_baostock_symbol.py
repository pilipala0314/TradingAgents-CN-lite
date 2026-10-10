"""baostock 符号转换纯函数单测（无需安装 baostock SDK，模块级不触发 SDK 导入）。"""
import pytest

from datasource.providers.baostock import _to_baostock_symbol


@pytest.mark.unit
class TestToBaostockSymbol:
    def test_bare(self):
        assert _to_baostock_symbol("600519") == "sh.600519"
        assert _to_baostock_symbol("000001") == "sz.000001"
        assert _to_baostock_symbol("430047") == "bj.430047"

    def test_prefix(self):
        assert _to_baostock_symbol("SH600519") == "sh.600519"
        assert _to_baostock_symbol("sz000001") == "sz.000001"

    def test_suffix(self):
        # 回归：历史上 "600519.SH" 被原样透传导致静默查空
        assert _to_baostock_symbol("600519.SH") == "sh.600519"
        assert _to_baostock_symbol("000001.sz") == "sz.000001"

    def test_native(self):
        assert _to_baostock_symbol("sh.600519") == "sh.600519"
        assert _to_baostock_symbol("SZ.000001") == "sz.000001"

    def test_whitespace(self):
        assert _to_baostock_symbol("  600519  ") == "sh.600519"
