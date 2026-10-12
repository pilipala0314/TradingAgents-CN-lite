"""eastmoney get_industry_ranking 字段映射单测（无网络，mock 东财响应）。

字段码语义已真机验证（2026-10-12 经隧道代理实测 clist）：
f128=领涨股名称 → lead_stock；f140=领涨股代码 → lead_code；f136=领涨股涨跌幅 → lead_change_pct。
此前 f140 被误映射为 lead_stock（拿到的是代码，不是名称）。

翻页：EM clist 单页 pz 上限 100、行业板块共 ~496 个 → provider 按 pn 翻页抓全。
"""
import asyncio

import pytest

from datasource.providers import eastmoney as em


def _board(code: str, name: str) -> dict:
    return {
        "f12": code, "f14": name, "f2": 1234.5, "f3": 2.35, "f8": 1.8,
        "f104": 80, "f105": 20, "f128": "中芯国际", "f136": 12.34, "f140": "688981",
    }


@pytest.mark.unit
class TestEastMoneyIndustryRankingFields:
    def test_field_codes_mapping(self, monkeypatch):
        captured: dict = {}

        async def fake_em_get(url, params):
            captured["fields"] = params.get("fields", "")
            return {"data": {"total": 1, "diff": [_board("BK1036", "半导体")]}}

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

    def test_pagination_across_pages(self, monkeypatch):
        """pz 上限 100：limit=250 时应翻 3 页且不重复不提前停。"""
        calls: list[int] = []

        async def fake_em_get(url, params):
            pn = int(params["pn"])
            calls.append(pn)
            # 模拟 total=220、每页 100：p1/p2 各 100 条，p3 20 条
            start = (pn - 1) * 100
            count = {1: 100, 2: 100, 3: 20}.get(pn, 0)
            return {
                "data": {
                    "total": 220,
                    "diff": [_board(f"BK{start + i:04d}", f"板块{start + i}") for i in range(count)],
                }
            }

        monkeypatch.setattr(em, "_em_get", fake_em_get)
        rows = asyncio.run(em.EastMoneyProvider().get_industry_ranking(limit=250))

        assert calls == [1, 2, 3]
        assert len(rows) == 220
        assert len({r["board_code"] for r in rows}) == 220  # 无重复
        assert rows[0]["board_name"] == "板块0"
        assert rows[-1]["board_name"] == "板块219"

    def test_pagination_stops_when_total_reached(self, monkeypatch):
        """total < limit 时按 total 停，不多翻页。"""
        calls: list[int] = []

        async def fake_em_get(url, params):
            calls.append(int(params["pn"]))
            return {"data": {"total": 3, "diff": [_board(f"BK{i:04d}", f"板块{i}") for i in range(3)]}}

        monkeypatch.setattr(em, "_em_get", fake_em_get)
        rows = asyncio.run(em.EastMoneyProvider().get_industry_ranking(limit=500))
        assert calls == [1]
        assert len(rows) == 3