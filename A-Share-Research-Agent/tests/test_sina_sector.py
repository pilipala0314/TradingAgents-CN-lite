"""新浪板块解析单测（无网络，纯函数）。"""
import pytest

from datasource.providers.sina import _parse_sina_sector


def _payload(rows):
    """构造 newSinaHy 格式响应。"""
    items = ",".join(f'"{k}":"{v}"' for k, v in rows.items())
    return f'var S_Finance_bankuai_sinaindustry = {{{items}}};'


SAMPLE = _payload(
    {
        "new_blhy": "new_blhy,玻璃行业,19,15.91,-0.44,-2.70,598418073,13674354096,sh600629,10.012,18.02,1.64,华建集团",
        "new_dzxx": "new_dzxx,电子信息,42,12.30,1.25,0.15,120000000,9999999999,sz300059,9.88,20.10,1.80,东方财富",
    }
)


@pytest.mark.unit
class TestParseSinaSector:
    def test_parse_basic(self):
        rows = _parse_sina_sector(SAMPLE)
        assert len(rows) == 2

    def test_field_extraction(self):
        rows = _parse_sina_sector(SAMPLE)
        glass = [r for r in rows if r["board_name"] == "玻璃行业"][0]
        assert glass["board_code"] == "new_blhy"
        assert glass["change_pct"] == -0.44
        assert glass["avg_price"] == pytest.approx(15.91)
        assert glass["amount"] == pytest.approx(13674354096.0)
        assert glass["lead_stock"] == "华建集团"
        assert glass["lead_code"] == "sh600629"
        assert glass["lead_change_pct"] == pytest.approx(10.012)

    def test_member_count(self):
        rows = _parse_sina_sector(SAMPLE)
        glass = [r for r in rows if r["board_name"] == "玻璃行业"][0]
        assert glass["member_count"] == 19  # 个股数（此前未解析，审计 P3 修复）

    def test_trailing_js_ignored(self):
        """raw_decode 只取第一个 JSON 对象，尾随 JS 不污染解析。"""
        payload = (
            'var x = {"a":"a,b,1,1,1,1,1,1,1,1,1,1,c"};'
            ' var y = {"z":"z,q,1,1,1,1,1,1,1,1,1,1,r"};'
        )
        rows = _parse_sina_sector(payload)
        assert len(rows) == 1
        assert rows[0]["board_code"] == "a"

    def test_dirty_number_does_not_raise(self):
        """脏数字（非数值）不抛错，归零处理。"""
        payload = '{"k":"k,板块,abc,--,1.5,x,9,10,sh1,1,2,3,股"}'
        rows = _parse_sina_sector(payload)
        assert len(rows) == 1
        assert rows[0]["member_count"] == 0
        assert rows[0]["change_pct"] == 1.5

    def test_malformed_payload(self):
        assert _parse_sina_sector("") == []
        assert _parse_sina_sector("not json") == []
        assert _parse_sina_sector("{}") == []

    def test_short_row_skipped(self):
        bad = _payload({"k": "k,只有一个"})
        assert _parse_sina_sector(bad) == []
