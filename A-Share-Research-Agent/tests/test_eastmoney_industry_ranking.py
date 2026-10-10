"""eastmoney get_industry_ranking 字段映射单测（无网络，mock 东财响应）。

锁定东财字段码语义（社区实测口径，东财封 IP 期间无法真机验证）：
f128=领涨股名称 → lead_stock；f140=领涨股代码 → lead_code；f136=领涨股涨跌幅 → lead_change_pct。
此前 f140 被误映射为 lead_stock（拿到的是代码，不是名称）。
"""
import asyncio

import pytest

from datasource.providers import eastmoney as em


@pytest.mark.unit
class TestEastMoneyIndustryRankingFields:
    def test_field_codes_mapping(self, monkeypatch):
        captured: dict = {}

        async def fake_em_get(url, params):
            captured["fields"] = params.get("fields", "")
            return {
                "data": {
                    "diff": [
                        {
                            "f12": "BK1036",
                            "f14": "半导体",
                            "f2": 1234.5,
                            "f3": 2.35,
                            "f8": 1.8,
                            "f104": 80,
                            "f105": 20,
                            "f128": "中芯国际",
                            "f136": 12.34,
                            "f140": "688981",
                        }
                    ]
                }
            }

        monkeypatch.setattr(em, "_em_get", fake_em_get)
        rows = asyncio.run(em.EastMoneyProvider().get_industry_ranking(limit=10))

        assert len(rows) == 1
        r = rows[0]
        assert r["board_code"] == "BK1036"
        assert r["board_name"] == "半导体"
        assert r["change_pct"] == 2.35
        assert r["lead_stock"] == "中芯国际"
        assert r["lead_code"] == "688981"
        assert r["lead_change_pct"] == 12.34
        # 请求字段需包含三个领涨股字段
        assert {"f128", "f136", "f140"} <= set(captured["fields"].split(","))