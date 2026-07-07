"""test_dashboard_capital_flow — P2-T2 资金流端点单测。

覆盖：
- GET /api/dashboard/stock/{code}/capital_flow
  - 个股资金流列表渲染
  - 大盘 main_force / northbound 各取 1 笔
  - summary 含 main_force_net / super_large_net / large_net
  - security_id 反查
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from core.identifiers import MARKET_ENTITY_ID


def _make_capital_flow_row(*, sec_uuid=None, flow_type="individual_main_force",
                          business_date=None, net=100.0, gross_in=50.0, gross_out=30.0,
                          entity_type="security"):
    row = MagicMock()
    row.business_date = business_date or date(2025, 7, 7)
    row.flow_type = flow_type
    row.net_inflow = Decimal(str(net))
    row.gross_inflow = Decimal(str(gross_in))
    row.gross_outflow = Decimal(str(gross_out))
    row.entity_id = sec_uuid or uuid4()
    row.entity_type = entity_type
    return row


@pytest.fixture
def client():
    return TestClient(app)


class TestStockCapitalFlow:
    def test_returns_summary_with_4_fields(self, client, monkeypatch) -> None:
        sec_uuid = uuid4()
        sec_row = MagicMock()
        sec_row.security_id = sec_uuid  # 用真实 UUID，避免被 mock 序列化
        sec_row.name = "贵州茅台"
        sec_row.symbol = "600519"

        ind_row = _make_capital_flow_row(
            sec_uuid=sec_uuid,
            net=1_234_567.0, gross_in=500_000.0, gross_out=200_000.0,
            business_date=date(2025, 7, 7),
        )
        market_row = _make_capital_flow_row(
            sec_uuid=MARKET_ENTITY_ID,
            flow_type="main_force",
            net=50_000_000.0,
            entity_type="market",
        )
        nb_row = _make_capital_flow_row(
            sec_uuid=MARKET_ENTITY_ID,
            flow_type="northbound",
            net=2_000_000.0,
            entity_type="market",
        )

        # 用 await_count 计数器来区分 4 个 select 调用
        # 调用顺序：sec → individual → market_main → market_northbound
        call_counter = {"n": 0}

        async def fake_execute(stmt):
            call_counter["n"] += 1
            n = call_counter["n"]
            m = MagicMock()
            if n == 1:
                # DimSecurity 反查
                m.scalar_one_or_none.return_value = sec_row
            elif n == 2:
                # 个股资金流
                m.scalars.return_value.all.return_value = [ind_row]
            elif n == 3:
                # market main_force
                m.scalars.return_value.first.return_value = market_row
            else:
                # market northbound
                m.scalars.return_value.first.return_value = nb_row
            return m

        from apps.api.routers import dashboard as dash_mod
        async def fake_get_db():
            sess = MagicMock()
            sess.execute = AsyncMock(side_effect=fake_execute)
            return sess

        app.dependency_overrides[dash_mod.get_db] = fake_get_db
        try:
            r = client.get("/api/dashboard/stock/600519/capital_flow?days=30")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["code"] == "600519"
            assert data["security_id"] == str(sec_uuid)
            assert data["name"] == "贵州茅台"
            assert data["summary"]["main_force_net"] == pytest.approx(1_234_567.0)
            assert data["summary"]["super_large_net"] == pytest.approx(500_000.0)
            assert data["summary"]["large_net"] == pytest.approx(200_000.0)
            assert data["market"]["main_force"]["net_inflow"] == pytest.approx(50_000_000.0)
            assert data["market"]["northbound"]["net_inflow"] == pytest.approx(2_000_000.0)
            assert len(data["individual"]) == 1
            assert data["individual"][0]["date"] == "2025-07-07"
        finally:
            app.dependency_overrides.clear()
