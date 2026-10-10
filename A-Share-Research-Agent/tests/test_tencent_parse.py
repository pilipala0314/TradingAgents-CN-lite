"""腾讯行情响应解析纯函数单测。"""
import pytest

from datasource.providers._shared.tencent import _parse_quote_response


def _make_line(key: str, fields: list, n: int = 53) -> str:
    """构造 53 字段标准行情行（不足补空串）。"""
    padded = [str(f) for f in fields] + [""] * (n - len(fields))
    return f'{key}="{"~".join(padded)}";'


@pytest.mark.unit
class TestParseQuoteResponse:
    def test_astock_53_fields(self):
        # 索引：1=name 3=price 4=last_close 5=open 32=change_pct 39=pe_ttm 46=pb
        data = [
            "1", "贵州茅台", "600519",
            "1800.00", "1790.00", "1795.00",  # 3=price 4=last_close 5=open
        ]
        line = _make_line("v_sh600519", data)
        parsed = _parse_quote_response(line)
        assert "600519" in parsed
        q = parsed["600519"]
        assert q["name"] == "贵州茅台"
        assert q["price"] == 1800.0
        assert q["last_close"] == 1790.0
        assert q["open"] == 1795.0

    def test_short_line_skipped(self):
        parsed = _parse_quote_response('v_sh600519="1~贵州茅台~600519";')
        assert parsed == {}

    def test_malformed_line_skipped(self):
        assert _parse_quote_response("garbage without equals") == {}
        assert _parse_quote_response('no_quote_marker="unclosed') == {}

    def test_hf_futures_14_fields(self):
        # 海外期货：14 字段逗号分隔，vals[0]=price 1=change_pct 13=name
        parsed = _parse_quote_response(
            'v_hf_GC="1980.5,0.85,1978.0,1975.0,1985.0,1974.0,20240101,0,0,0,0,0,0,纽约黄金";'
        )
        assert "GC" in parsed
        q = parsed["GC"]
        assert q["price"] == 1980.5
        assert q["change_pct"] == 0.85
        assert q["name"] == "纽约黄金 (COMEX Gold)"  # override 映射优先
