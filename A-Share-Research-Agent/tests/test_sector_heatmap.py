"""P0-T12 sector_heatmap 单元测试。

覆盖：
- _normalize_bar_dict：日期 / 收盘 / 涨跌幅 / 成交额 多格式兼容
- _select_last_n_by_date：升序取末 N
- build_sector_heatmap：mock provider，验证 31 行业 × 5 日矩阵对齐
- 异常路径：list 失败 / 单行业失败容错
- HTTP 端点：/api/dashboard/sector/heatmap
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import app
from services.sector_heatmap_service import (
    _normalize_bar_dict,
    _select_last_n_by_date,
    build_sector_heatmap,
)


def _row(date_str: str, close: float, pct: float, amount: float = 1.0e8) -> dict:
    return {"日期": date_str, "收盘": close, "涨跌幅": pct, "成交额": amount}


def _make_mock_provider(industries: list[tuple[str, str]], bars_by_code: dict[str, list[dict]]):
    """Mock SWIndustryProvider：list + per-code bars。"""
    provider = MagicMock()
    list_df = pd.DataFrame(industries, columns=["code", "name"])
    provider.get_index_list = AsyncMock(return_value=list_df)

    async def _bars(symbol, start_date=None, end_date=None):
        return pd.DataFrame(bars_by_code.get(symbol, []))

    provider.get_daily_bars = _bars
    return provider


# ── 1. _normalize_bar_dict ─────────────────────────


def test_normalize_chinese_columns():
    out = _normalize_bar_dict(_row("2026-07-07", 100.5, 2.3))
    assert out["date"] == "2026-07-07"
    assert out["close"] == 100.5
    assert out["change_pct"] == 2.3
    assert out["amount"] == 1.0e8


def test_normalize_english_columns():
    out = _normalize_bar_dict({"date": "2026-07-07", "close": 99, "change_pct": -1.2, "amount": 5e7})
    assert out["date"] == "2026-07-07"
    assert out["change_pct"] == -1.2


def test_normalize_8digit_date():
    out = _normalize_bar_dict({"日期": "20260707", "收盘": 50, "涨跌幅": 0.5})
    assert out["date"] == "2026-07-07"


def test_normalize_missing_returns_none():
    assert _normalize_bar_dict({}) is None
    assert _normalize_bar_dict(None) is None
    assert _normalize_bar_dict({"日期": "2026-07-07"}) is None  # 缺 close / pct


# ── 2. _select_last_n_by_date ────────────────────────


def test_select_last_n_orders_and_takes_tail():
    raw_bars = [
        _row("2026-07-03", 100, 1.0),
        _row("2026-07-01", 99, -0.5),
        _row("2026-07-02", 99.5, 0.5),
        _row("2026-07-04", 101, 1.0),
    ]
    bars = [_normalize_bar_dict(b) for b in raw_bars]
    out = _select_last_n_by_date(bars, 2)
    assert [b["date"] for b in out] == ["2026-07-03", "2026-07-04"]


def test_select_last_n_zero_returns_all():
    raw_bars = [_row("2026-07-01", 1, 0)]
    bars = [_normalize_bar_dict(b) for b in raw_bars]
    assert _select_last_n_by_date(bars, 0) == bars


# ── 3. build_sector_heatmap ────────────────────────


@pytest.mark.asyncio
async def test_build_heatmap_31_industries_5_days():
    """31 行业 × 5 日 矩阵：每行业 5 bars，矩阵列对齐全局 dates。"""
    industries = [(f"80{1000+i}", f"行业{i}") for i in range(1, 32)]  # 31
    dates = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"]
    bars_by_code = {
        f"80{1000+i}": [_row(d, 100 + i + idx, idx * 0.5) for idx, d in enumerate(dates)]
        for i in range(1, 32)
    }
    provider = _make_mock_provider(industries, bars_by_code)

    result = await build_sector_heatmap(provider, n_days=5, lookback_calendar_days=15)
    assert result["industry_count"] == 31
    assert result["dates"] == dates
    assert len(result["matrix"]) == 31
    # 验证某一行对齐正确
    first = result["matrix"][0]
    assert first["code"] == "801001"
    assert len(first["values"]) == 5
    assert first["values"] == [0.0, 0.5, 1.0, 1.5, 2.0]


@pytest.mark.asyncio
async def test_build_heatmap_handles_sparse_industry():
    """某行业只有 3 个 bars → values 后 2 位为 None（对齐）。"""
    industries = [("801010", "农林牧渔"), ("801020", "采掘")]
    dates = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"]
    bars_by_code = {
        "801010": [_row(d, 100 + i, 0.5) for i, d in enumerate(dates[:3])],  # only 3
        "801020": [_row(d, 50 + i, 0.3) for i, d in enumerate(dates)],
    }
    provider = _make_mock_provider(industries, bars_by_code)

    result = await build_sector_heatmap(provider, n_days=5, lookback_calendar_days=15)
    assert result["industry_count"] == 2
    sparse = next(r for r in result["matrix"] if r["code"] == "801010")
    assert sparse["values"][:3] == [0.5, 0.5, 0.5]
    assert sparse["values"][3] is None
    assert sparse["values"][4] is None


@pytest.mark.asyncio
async def test_build_heatmap_survives_single_industry_failure():
    """单行业 get_daily_bars 抛错 → 该行 values 全 None，不影响其他。"""
    industries = [("801010", "农林牧渔"), ("801020", "采掘")]

    provider = MagicMock()
    list_df = pd.DataFrame(industries, columns=["code", "name"])
    provider.get_index_list = AsyncMock(return_value=list_df)

    async def _bars(symbol, start_date=None, end_date=None):
        if symbol == "801010":
            raise RuntimeError("akshare network error")
        return pd.DataFrame([_row("2026-07-01", 50, 0.5), _row("2026-07-02", 50, 0.3)])

    provider.get_daily_bars = _bars

    result = await build_sector_heatmap(provider, n_days=5, lookback_calendar_days=15)
    assert result["industry_count"] == 2
    failed = next(r for r in result["matrix"] if r["code"] == "801010")
    # 全部 None（无 bar）
    assert all(v is None for v in failed["values"])
    # OK 行正常
    ok = next(r for r in result["matrix"] if r["code"] == "801020")
    assert ok["values"][:2] == [0.5, 0.3]


@pytest.mark.asyncio
async def test_build_heatmap_list_failure_returns_empty():
    provider = MagicMock()
    provider.get_index_list = AsyncMock(side_effect=RuntimeError("list failed"))
    provider.get_daily_bars = AsyncMock(return_value=pd.DataFrame())

    result = await build_sector_heatmap(provider, n_days=5)
    assert result["industry_count"] == 0
    assert result["matrix"] == []
    assert "reason" in result
    assert "list failed" in result["reason"]


@pytest.mark.asyncio
async def test_build_heatmap_empty_list_returns_empty():
    provider = MagicMock()
    provider.get_index_list = AsyncMock(return_value=pd.DataFrame(columns=["code", "name"]))
    provider.get_daily_bars = AsyncMock(return_value=pd.DataFrame())

    result = await build_sector_heatmap(provider, n_days=5)
    assert result["industry_count"] == 0
    assert result["matrix"] == []


# ── 4. HTTP 端点 ────────────────────────────────


def test_endpoint_heatmap_returns_matrix():
    async def _get():
        with patch(
            "apps.api.routers.dashboard.SWIndustryProvider"
        ) as MockProvider:
            industries = [(f"80{1000+i}", f"行业{i}") for i in range(1, 32)]
            dates = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"]
            bars_by_code = {
                f"80{1000+i}": [_row(d, 100.0, idx * 0.5) for idx, d in enumerate(dates)]
                for i in range(1, 32)
            }
            mock_provider = _make_mock_provider(industries, bars_by_code)
            MockProvider.return_value = mock_provider

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                return await c.get("/api/dashboard/sector/heatmap?n_days=5")

    r = asyncio.run(_get())
    assert r.status_code == 200
    body = r.json()
    assert body["industry_count"] == 31
    assert len(body["matrix"]) == 31
    assert len(body["dates"]) == 5
    first = body["matrix"][0]
    assert first["code"] == "801001"
    assert len(first["values"]) == 5


def test_endpoint_heatmap_validates_query_params():
    async def _get():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.get("/api/dashboard/sector/heatmap?n_days=0")  # < 1 应 422

    r = asyncio.run(_get())
    assert r.status_code == 422
