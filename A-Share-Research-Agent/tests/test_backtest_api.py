"""回测 API 单测（mock 引擎，避免触发真网络拉 300 只股票）。

覆盖：
- GET /v1/backtest/strategies — 4 个内置策略
- GET /v1/backtest/pool/hs300 — 沪深 300 列表
- GET /v1/backtest/default-config — 默认配置
- POST /v1/backtest/run — 跑回测（mock engine.run）
- 错误路径：未知 universe / 未知策略 / 引擎异常
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app


@pytest.fixture(autouse=True)
def _clear_universe_cache() -> None:
    """每个测试都清空 universes 进程内缓存，避免跨测试污染。"""
    from data_pipeline.universes import clear_cache
    clear_cache()
    yield
    clear_cache()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ── 静态端点 ────────────────────────────────────────────

def test_strategies_endpoint(client: TestClient) -> None:
    r = client.get("/v1/backtest/strategies")
    assert r.status_code == 200
    data = r.json()
    names = {s["name"] for s in data["strategies"]}
    assert names == {
        "momentum_20d",
        "mean_reversion_5d",
        "value_ma250",
        "multi_factor_mom_vol",
    }
    for s in data["strategies"]:
        assert "description" in s and s["description"]


def test_default_config(client: TestClient) -> None:
    r = client.get("/v1/backtest/default-config")
    assert r.status_code == 200
    data = r.json()
    assert data["strategy_name"] == "momentum_20d"
    assert data["universe"] == "hs300"
    assert data["rebalance_freq"] == "weekly"
    assert data["top_n"] == 20
    # 日期是字符串
    assert isinstance(data["start_date"], str)
    assert isinstance(data["end_date"], str)


def test_pool_hs300(client: TestClient) -> None:
    """沪深 300 端点：mock DataRouter 返 300 只。"""
    import pandas as pd

    from data_pipeline.providers.facade import DataRouter  # noqa: F401

    fake_codes = [f"{i:06d}" for i in range(1, 301)]
    fake_df = pd.DataFrame(
        {
            "品种代码": fake_codes,
            "品种名称": [f"股票{i}" for i in range(300)],
            "纳入日期": ["2005-01-01"] * 300,
        }
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)
    # patch 两处 get_router（endpoint + backtest_engine 都用 facade.get_router）
    with patch("apps.api.routers.backtest.get_router", return_value=fake_router), \
         patch("data_pipeline.providers.facade.get_router", return_value=fake_router), \
         patch("services.backtest_engine.get_router", return_value=fake_router):
        r = client.get("/v1/backtest/pool/hs300")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 300
    assert len(data["codes"]) == 300


def test_pool_hs300_failure(client: TestClient) -> None:
    """DataRouter 抛错时返 502。"""
    fake_router = MagicMock()
    fake_router.route = AsyncMock(side_effect=RuntimeError("router down"))
    with patch("apps.api.routers.backtest.get_router", return_value=fake_router), \
         patch("data_pipeline.providers.facade.get_router", return_value=fake_router), \
         patch("services.backtest_engine.get_router", return_value=fake_router):
        r = client.get("/v1/backtest/pool/hs300")
    assert r.status_code == 502
    # DataRouter 失败被 resolve_universe 包装成 UniverseError，endpoint 走 502
    assert "router down" in r.json()["detail"]


# ── P2.6 universe 端点 ─────────────────────────

def test_universes_endpoint(client: TestClient) -> None:
    """GET /v1/backtest/universes：列出 6 个 preset。"""
    r = client.get("/v1/backtest/universes")
    assert r.status_code == 200
    data = r.json()
    names = {u["preset"] for u in data["universes"]}
    assert names == {"hs300", "csi500", "csi1000", "gem", "star", "all_a"}


def test_pool_unknown_preset_400(client: TestClient) -> None:
    """未知 preset → 400。"""
    r = client.get("/v1/backtest/pool/not_a_preset")
    assert r.status_code == 400
    assert "未知 universe" in r.json()["detail"]


def test_pool_csi500(client: TestClient) -> None:
    """csi500 端点：mock router 返 500 只中证 500 成分股。"""
    import pandas as pd
    fake_df = pd.DataFrame(
        {
            "品种代码": [f"{i:06d}" for i in range(1, 501)],
            "品种名称": [f"x{i}" for i in range(500)],
            "纳入日期": ["2010-01-01"] * 500,
        }
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)
    with patch("apps.api.routers.backtest.get_router", return_value=fake_router), \
         patch("data_pipeline.providers.facade.get_router", return_value=fake_router), \
         patch("services.backtest_engine.get_router", return_value=fake_router):
        r = client.get("/v1/backtest/pool/csi500")
    assert r.status_code == 200
    data = r.json()
    assert data["preset"] == "csi500"
    assert data["count"] == 500
    assert len(data["codes"]) == 500


# ── P2.3 异步端点：mock 异步 session ─────────

class _FakeAsyncCtx:
    """mock async with: __aenter__/__aexit__"""

    def __init__(self, session) -> None:
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *args):
        return None


class _FakeAsyncSessionFactory:
    """mock AsyncSessionLocal() → async with → session"""

    def __init__(self, session) -> None:
        self._s = session
        self.call_count = 0

    def __call__(self):
        self.call_count += 1
        return _FakeAsyncCtx(self._s)


def _make_run_row_dict(
    *, status: str = "pending", business_date: str = "2025-07-07",
    job_id: str = "00000000-0000-0000-0000-000000000001",
    strategy: str = "momentum_20d",
) -> dict:
    return {
        "run_id": "row-1",
        "job_id": job_id,
        "business_date": business_date,
        "status": status,
        "config_hash": "abc123def4567890",
        "config_json": {"strategy_name": strategy, "symbols": ["600519"]},
        "result_json": None,
        "error_message": None,
        "cost_total_pct": None,
        "benchmark_alpha": None,
        "benchmark_beta": None,
        "attribution_json": None,
        "started_at": None,
        "finished_at": None,
        "created_at": "2025-07-07T10:00:00",
    }


def _orm_row_from_dict(d: dict) -> SimpleNamespace:
    """把 dict 转成 ORM-shaped SimpleNamespace（适配 _row_to_dict）。"""
    bd = d["business_date"]
    if isinstance(bd, str):
        bd = date.fromisoformat(bd)
    ca = d.get("created_at")
    if isinstance(ca, str):
        ca = datetime.fromisoformat(ca)
    return SimpleNamespace(
        id=d["run_id"],
        job_id=d["job_id"],
        business_date=bd,
        status=d["status"],
        config_hash=d["config_hash"],
        config_json=d["config_json"],
        result_json=d.get("result_json"),
        error_message=d.get("error_message"),
        cost_total_pct=d.get("cost_total_pct"),
        benchmark_alpha=d.get("benchmark_alpha"),
        benchmark_beta=d.get("benchmark_beta"),
        attribution_json=d.get("attribution_json"),
        started_at=d.get("started_at"),
        finished_at=d.get("finished_at"),
        created_at=ca,
    )


def test_run_async_returns_job_id(client: TestClient) -> None:
    """POST /v1/backtest/run/async 立即返 job_id（不真跑）。"""
    import pandas as pd
    fake_df = pd.DataFrame(
        {"品种代码": ["600519"], "品种名称": ["茅台"], "纳入日期": ["2020-01-01"]}
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)
    fake_session = MagicMock()
    # session.execute / commit 是 AsyncMock
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()
    factory = _FakeAsyncSessionFactory(fake_session)

    # fetch_by_hash_and_date 返 ORM-shaped 对象
    from datetime import date
    from types import SimpleNamespace
    fake_row = SimpleNamespace(
        id="row-1", job_id="00000000-0000-0000-0000-000000000001", business_date=date(2025, 7, 7),
        status="pending", config_hash="abc", config_json={}, result_json=None,
        error_message=None, cost_total_pct=None, benchmark_alpha=None,
        benchmark_beta=None, attribution_json=None,
        started_at=None, finished_at=None, created_at=None,
    )
    res_obj = MagicMock()
    res_obj.scalar_one_or_none.return_value = fake_row
    res_obj.scalars.return_value.all.return_value = [fake_row]
    fake_session.execute.return_value = res_obj

    with patch("apps.api.routers.backtest.get_router", return_value=fake_router), \
         patch("data_pipeline.providers.facade.get_router", return_value=fake_router), \
         patch("services.backtest_engine.get_router", return_value=fake_router), \
         patch("apps.api.routers.backtest.AsyncSessionLocal", factory, create=True), \
         patch("core.database.AsyncSessionLocal", factory, create=True):
        r = client.post(
            "/v1/backtest/run/async",
            json={
                "strategy_name": "momentum_20d",
                "universe": "hs300",
                "start_date": "2025-01-01",
                "end_date": "2025-03-31",
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert "job_id" in data and len(data["job_id"]) == 36  # UUID4
    assert data["status"] == "pending"
    assert "config_hash" in data
    # execute 至少被调 1 次
    assert fake_session.execute.await_count >= 1


def test_get_run_by_job_id_not_found(client: TestClient) -> None:
    """GET /v1/backtest/runs/{job_id} 不存在 → 404。"""
    fake_session = MagicMock()
    fake_session.execute = AsyncMock()
    # 返 scalars() 拿不到
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    fake_session.execute.return_value = result
    factory = _FakeAsyncSessionFactory(fake_session)
    with patch("apps.api.routers.backtest.AsyncSessionLocal", factory, create=True), \
         patch("core.database.AsyncSessionLocal", factory, create=True):
        r = client.get("/v1/backtest/runs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_get_run_by_job_id_found(client: TestClient) -> None:
    """GET /v1/backtest/runs/{job_id} 存在 → 200。"""
    fake_session = MagicMock()
    fake_session.execute = AsyncMock()
    row_dict = _make_run_row_dict(status="completed")
    # fetch_by_job_id 走 scalar_one_or_none
    result = MagicMock()
    result.scalar_one_or_none.return_value = _orm_row_from_dict(row_dict)
    fake_session.execute.return_value = result
    factory = _FakeAsyncSessionFactory(fake_session)
    with patch("apps.api.routers.backtest.AsyncSessionLocal", factory, create=True), \
         patch("core.database.AsyncSessionLocal", factory, create=True):
        r = client.get("/v1/backtest/runs/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "completed"
    assert data["job_id"] == "00000000-0000-0000-0000-000000000001"


def test_list_runs(client: TestClient) -> None:
    """GET /v1/backtest/runs：返回历史列表。"""
    fake_session = MagicMock()
    fake_session.execute = AsyncMock()
    rows = [_orm_row_from_dict(_make_run_row_dict(job_id="j1")),
            _orm_row_from_dict(_make_run_row_dict(job_id="j2"))]
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    fake_session.execute.return_value = result
    factory = _FakeAsyncSessionFactory(fake_session)
    with patch("apps.api.routers.backtest.AsyncSessionLocal", factory, create=True), \
         patch("core.database.AsyncSessionLocal", factory, create=True):
        r = client.get("/v1/backtest/runs")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    assert len(data["runs"]) == 2


def test_get_attribution_not_found(client: TestClient) -> None:
    """GET /v1/backtest/runs/{job_id}/attribution：run 不存在 → 404。"""
    fake_session = MagicMock()
    fake_session.execute = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    fake_session.execute.return_value = result
    factory = _FakeAsyncSessionFactory(fake_session)
    with patch("apps.api.routers.backtest.AsyncSessionLocal", factory, create=True), \
         patch("core.database.AsyncSessionLocal", factory, create=True):
        r = client.get("/v1/backtest/runs/missing/attribution")
    assert r.status_code == 404


def test_get_attribution_no_attribution_data(client: TestClient) -> None:
    """run 存在但没 attribution_json → 404。"""
    fake_session = MagicMock()
    fake_session.execute = AsyncMock()
    row = _make_run_row_dict(status="completed")
    # attribution_json = None
    result = MagicMock()
    result.scalar_one_or_none.return_value = _orm_row_from_dict(row)
    fake_session.execute.return_value = result
    factory = _FakeAsyncSessionFactory(fake_session)
    with patch("apps.api.routers.backtest.AsyncSessionLocal", factory, create=True), \
         patch("core.database.AsyncSessionLocal", factory, create=True):
        r = client.get("/v1/backtest/runs/00000000-0000-0000-0000-000000000001/attribution")
    assert r.status_code == 404
    assert "无归因" in r.json()["detail"]


def test_get_attribution_success(client: TestClient) -> None:
    """run + attribution 都有 → 200。"""
    fake_session = MagicMock()
    fake_session.execute = AsyncMock()
    row = _make_run_row_dict(status="completed")
    row["attribution_json"] = {"rebalance": {"turnover": 0.5}}
    result = MagicMock()
    result.scalar_one_or_none.return_value = _orm_row_from_dict(row)
    fake_session.execute.return_value = result
    factory = _FakeAsyncSessionFactory(fake_session)
    with patch("apps.api.routers.backtest.AsyncSessionLocal", factory, create=True), \
         patch("core.database.AsyncSessionLocal", factory, create=True):
        r = client.get("/v1/backtest/runs/00000000-0000-0000-0000-000000000001/attribution")
    assert r.status_code == 200
    data = r.json()
    assert data["attribution"]["rebalance"]["turnover"] == 0.5


# ── POST /run ────────────────────────────────────────────

def _mock_run_result() -> MagicMock:
    """构造 mock BacktestResult。"""
    r = MagicMock()
    r.to_dict.return_value = {
        "config": {
            "strategy_name": "momentum_20d",
            "symbols": ["600519"],
            "start_date": "2025-01-01",
            "end_date": "2025-03-31",
            "initial_capital": 1_000_000.0,
            "top_n": 20,
            "rebalance_freq": "weekly",
        },
        "metrics": {
            "total_return": 0.05,
            "annualized_return": 0.20,
            "annualized_volatility": 0.15,
            "sharpe_ratio": 1.2,
            "calmar_ratio": 0.8,
            "max_drawdown": 0.10,
            "win_rate": 0.55,
            "profit_loss_ratio": 1.1,
            "n_periods": 60,
            "n_wins": 33,
            "n_losses": 27,
            "best_day": 0.03,
            "worst_day": -0.02,
        },
        "rebalance_dates": ["2025-01-06", "2025-01-13"],
        "trading_dates": ["2025-01-01", "2025-01-02"],
        "trades": [{"date": "2025-01-06", "sold": [], "bought": ["600519"], "scores_n": 1, "selected_n": 1}],
        "skipped_symbols": [],
        "nav": [1.0, 1.01, 1.02],
        "nav_points": 3,
        "returns_points": 2,
    }
    return r


def test_run_happy_path(client: TestClient) -> None:
    """正常路径：mock engine.run 返结果。"""
    import pandas as pd

    fake_codes = [f"{i:06d}" for i in range(1, 11)]
    fake_df = pd.DataFrame(
        {
            "品种代码": fake_codes,
            "品种名称": [f"股票{i}" for i in range(10)],
            "纳入日期": ["2005-01-01"] * 10,
        }
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)

    with patch("services.backtest_engine.get_router", return_value=fake_router), \
         patch("services.backtest_engine.BacktestEngine.run", return_value=_mock_run_result()):
        r = client.post(
            "/v1/backtest/run",
            json={
                "strategy_name": "momentum_20d",
                "universe": "hs300",
                "start_date": "2025-01-01",
                "end_date": "2025-03-31",
                "top_n": 20,
                "rebalance_freq": "weekly",
                "initial_capital": 1_000_000.0,
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["metrics"]["total_return"] == 0.05
    assert data["config"]["strategy_name"] == "momentum_20d"
    assert data["nav"][0] == 1.0


def test_run_unknown_universe(client: TestClient) -> None:
    r = client.post(
        "/v1/backtest/run",
        json={
            "strategy_name": "momentum_20d",
            "universe": "all_a_shares",  # 暂不支持
            "start_date": "2025-01-01",
            "end_date": "2025-03-31",
        },
    )
    assert r.status_code == 400
    assert "hs300" in r.json()["detail"]


def test_run_unknown_strategy(client: TestClient) -> None:
    import pandas as pd

    fake_df = pd.DataFrame(
        {
            "品种代码": ["600519"],
            "品种名称": ["贵州茅台"],
            "纳入日期": ["2005-01-01"],
        }
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)
    with patch("services.backtest_engine.get_router", return_value=fake_router):
        r = client.post(
            "/v1/backtest/run",
            json={
                "strategy_name": "nope_unknown",
                "universe": "hs300",
                "start_date": "2025-01-01",
                "end_date": "2025-03-31",
            },
        )
    assert r.status_code == 400
    assert "未知策略" in r.json()["detail"]


def test_run_invalid_dates(client: TestClient) -> None:
    r = client.post(
        "/v1/backtest/run",
        json={
            "strategy_name": "momentum_20d",
            "universe": "hs300",
            "start_date": "2025-12-31",
            "end_date": "2025-01-01",
        },
    )
    assert r.status_code == 400
    assert "start_date 必须早于" in r.json()["detail"]


def test_run_engine_exception(client: TestClient) -> None:
    """引擎抛错时返 500。"""
    import pandas as pd

    fake_df = pd.DataFrame(
        {
            "品种代码": ["600519"],
            "品种名称": ["贵州茅台"],
            "纳入日期": ["2005-01-01"],
        }
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)
    with patch("services.backtest_engine.get_router", return_value=fake_router), \
         patch("services.backtest_engine.BacktestEngine.run", side_effect=RuntimeError("boom")):
        r = client.post(
            "/v1/backtest/run",
            json={
                "strategy_name": "momentum_20d",
                "universe": "hs300",
                "start_date": "2025-01-01",
                "end_date": "2025-03-31",
            },
        )
    assert r.status_code == 500
    assert "boom" in r.json()["detail"]


def test_run_default_dates(client: TestClient) -> None:
    """不传日期时用默认值（end=今日, start=1 年前）。"""
    import pandas as pd

    fake_df = pd.DataFrame(
        {
            "品种代码": ["600519"],
            "品种名称": ["贵州茅台"],
            "纳入日期": ["2005-01-01"],
        }
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)
    with patch("services.backtest_engine.get_router", return_value=fake_router), \
         patch("services.backtest_engine.BacktestEngine.run", return_value=_mock_run_result()) as mock_run:
        r = client.post(
            "/v1/backtest/run",
            json={
                "strategy_name": "momentum_20d",
                "universe": "hs300",
            },
        )
    assert r.status_code == 200
    # 验证传入引擎的 cfg 日期范围
    args, _kwargs = mock_run.call_args
    cfg = args[0]
    assert cfg.end_date == date.today()
    assert cfg.start_date == date.today().replace(year=date.today().year - 1)
