"""_impl 失败语义单测（无网络）：网络失败/响应缺 data 必须 raise，不静默返空。

覆盖 audit「静默返空」在 a_stock_data 通道的残留点：
- _impl/sector.py: fetch_industry_ranking / fetch_concept_blocks
- _impl/fundamentals.py: fetch_stock_info
（此前吞异常返回 []/{}，导致无重试机会且根因丢失）
"""
import pytest

from datasource.providers._impl import fundamentals as fund_mod
from datasource.providers._impl import sector as sector_mod


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _boom(*args, **kwargs):
    raise ConnectionError("RemoteDisconnected")


@pytest.mark.unit
class TestSectorFailureSemantics:
    def test_industry_ranking_network_error_raises(self, monkeypatch):
        monkeypatch.setattr(sector_mod, "em_get", _boom)
        with pytest.raises(RuntimeError):
            sector_mod.fetch_industry_ranking(10)

    def test_industry_ranking_missing_data_raises(self, monkeypatch):
        monkeypatch.setattr(sector_mod, "em_get", lambda *a, **k: _FakeResp({"data": None}))
        with pytest.raises(RuntimeError):
            sector_mod.fetch_industry_ranking(10)

    def test_industry_ranking_ok_maps_fields(self, monkeypatch):
        payload = {
            "data": {
                "total": 1,
                "diff": [
                    {"f12": "BK1036", "f14": "半导体", "f3": 2.35, "f128": "中芯国际", "f136": 12.34}
                ]
            }
        }
        monkeypatch.setattr(sector_mod, "em_get", lambda *a, **k: _FakeResp(payload))
        rows = sector_mod.fetch_industry_ranking(10)
        assert len(rows) == 1
        r = rows[0]
        assert r["board_code"] == "BK1036"
        assert r["board_name"] == "半导体"
        assert r["lead_stock"] == "中芯国际"  # f128=领涨股名称
        assert r["lead_change_pct"] == 12.34  # f136=领涨股涨跌幅

    def test_concept_blocks_network_error_raises(self, monkeypatch):
        monkeypatch.setattr(sector_mod, "em_get", _boom)
        with pytest.raises(RuntimeError):
            sector_mod.fetch_concept_blocks("600519")


@pytest.mark.unit
class TestFundamentalsFailureSemantics:
    def test_stock_info_network_error_raises(self, monkeypatch):
        monkeypatch.setattr(fund_mod, "em_get", _boom)
        with pytest.raises(RuntimeError):
            fund_mod.fetch_stock_info("600519")

    def test_stock_info_missing_data_raises(self, monkeypatch):
        monkeypatch.setattr(fund_mod, "em_get", lambda *a, **k: _FakeResp({"data": None}))
        with pytest.raises(RuntimeError):
            fund_mod.fetch_stock_info("600519")