"""P0-T8/T9 provider_failover service + endpoint 单元测试。

覆盖：
- _stats_to_dict 容错
- check_and_failover 触发条件（primary 失败率 < threshold / total < min_total 不触发）
- check_and_failover 恢复条件
- force_recover
- apply_failover_to_chain 重排
- HTTP 端点：overview / history / state 404 / recover
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import app
from data_pipeline.data_router import EndpointStats
from services import provider_failover as pf


@pytest.fixture(autouse=True)
def _reset_pf_state():
    pf.reset()
    yield
    pf.reset()


def _make_stat(method: str, provider: str, *, total: int, success: int, last_error: str = "") -> EndpointStats:
    s = EndpointStats(method=method, provider=provider)
    failure = total - success
    for _ in range(success):
        s.record(success=True, latency_ms=20.0)
    for i in range(failure):
        s.record(success=False, latency_ms=200.0, error=f"err-{i}")
    s.last_error = last_error
    return s


def _make_router(methods: list[str], providers_per_method: int = 2) -> MagicMock:
    """Mock router：每个 method 注册 N 个 provider，stats 全部空（由测试再注入）。"""
    router = MagicMock()
    method_table: dict[str, dict[str, MagicMock]] = {}
    for method in methods:
        method_table[method] = {f"prov_{i}": MagicMock() for i in range(providers_per_method)}
    router._method_table = method_table
    router._stats = {}
    return router


# ── 1. _stats_to_dict ─────────────────────────────


def test_stats_to_dict_none():
    assert pf._stats_to_dict(None) == {}


def test_stats_to_dict_endpoint_stats():
    s = _make_stat("get_x", "akshare", total=10, success=7)
    out = pf._stats_to_dict(s)
    assert out["total"] == 10
    assert out["success"] == 7
    assert out["success_rate"] == 0.7
    assert "last_error" in out
    assert "p95_latency_ms" in out


# ── 2. check_and_failover 触发 / 恢复 ──────────────────────


def test_check_no_failover_when_all_healthy():
    """所有 provider success_rate=1.0 → 无 failover。"""
    router = _make_router(["get_x", "get_y"], providers_per_method=2)
    for m in ["get_x", "get_y"]:
        for p in ["prov_0", "prov_1"]:
            router._stats[f"{m}:{p}"] = _make_stat(m, p, total=20, success=20)
    changes = pf.check_and_failover(router)
    assert changes == []
    assert pf.get_active_failovers() == []


def test_check_skips_when_total_too_low():
    """total < min_total → 不评估。"""
    router = _make_router(["get_x"], providers_per_method=2)
    # primary 失败率 0% 但只有 3 次
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=3, success=0)
    router._stats["get_x:prov_1"] = _make_stat("get_x", "prov_1", total=3, success=3)
    changes = pf.check_and_failover(router, min_total=10)
    assert changes == []


def test_check_skips_single_provider_chain():
    """单 provider 链无法降级。"""
    router = _make_router(["get_x"], providers_per_method=1)
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=0)
    changes = pf.check_and_failover(router)
    assert changes == []


def test_check_triggers_failover_on_low_success_rate():
    """primary 失败率 30% < 0.5 触发。"""
    router = _make_router(["get_x"], providers_per_method=2)
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=6)  # 30%
    router._stats["get_x:prov_1"] = _make_stat("get_x", "prov_1", total=20, success=19)  # 95%
    changes = pf.check_and_failover(router, min_total=10)
    assert len(changes) == 1
    assert changes[0]["action"] == "failover"
    assert changes[0]["method"] == "get_x"
    assert changes[0]["primary"] == "prov_0"
    assert changes[0]["backup"] == "prov_1"
    active = pf.get_active_failovers()
    assert len(active) == 1
    assert active[0].method == "get_x"
    assert active[0].active is True


def test_check_does_not_retrigger_when_already_active():
    """已降级时不要反复 push history。"""
    router = _make_router(["get_x"], providers_per_method=2)
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=6)
    router._stats["get_x:prov_1"] = _make_stat("get_x", "prov_1", total=20, success=19)
    pf.check_and_failover(router, min_total=10)
    history_len_1 = len(pf.get_history())
    # 再 check 一次（primary 仍失败）→ 不应再次 push failover
    pf.check_and_failover(router, min_total=10)
    history_len_2 = len(pf.get_history())
    assert history_len_1 == history_len_2


def test_check_recovers_when_both_recover():
    """降级后 primary + backup 都 success_rate >= 0.85 → 恢复。"""
    router = _make_router(["get_x"], providers_per_method=2)
    # 初始 primary 坏
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=6)
    router._stats["get_x:prov_1"] = _make_stat("get_x", "prov_1", total=20, success=19)
    pf.check_and_failover(router, min_total=10)
    assert len(pf.get_active_failovers()) == 1

    # primary 恢复
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=18)
    changes = pf.check_and_failover(router, min_total=10, recovery_threshold=0.85)
    assert len(changes) == 1
    assert changes[0]["action"] == "recover"
    assert pf.get_active_failovers() == []
    # state 保留为 inactive 用于审计
    states = pf.get_all_states()
    assert len(states) == 1
    assert states[0].active is False


# ── 3. force_recover ─────────────────────────────


def test_force_recover_no_state_returns_false():
    assert pf.force_recover("get_x") is False


def test_force_recover_active_state():
    router = _make_router(["get_x"], providers_per_method=2)
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=6)
    router._stats["get_x:prov_1"] = _make_stat("get_x", "prov_1", total=20, success=19)
    pf.check_and_failover(router, min_total=10)
    assert pf.force_recover("get_x", reason="ops") is True
    assert pf.get_active_failovers() == []
    last = pf.get_history(limit=1)[0]
    assert last["action"] == "force_recover"
    assert last["reason"] == "ops"


# ── 4. apply_failover_to_chain 重排 ───────────────────────


def test_apply_failover_no_state_returns_original():
    chain = ["prov_0", "prov_1"]
    assert pf.apply_failover_to_chain("get_x", chain) == chain


def test_apply_failover_active_reorders():
    router = _make_router(["get_x"], providers_per_method=2)
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=6)
    router._stats["get_x:prov_1"] = _make_stat("get_x", "prov_1", total=20, success=19)
    pf.check_and_failover(router, min_total=10)
    chain = ["prov_0", "prov_1"]
    new_chain = pf.apply_failover_to_chain("get_x", chain)
    assert new_chain == ["prov_1", "prov_0"]


def test_apply_failover_idempotent():
    """已经重排过的 chain 再调一次应保持不变。"""
    router = _make_router(["get_x"], providers_per_method=2)
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=6)
    router._stats["get_x:prov_1"] = _make_stat("get_x", "prov_1", total=20, success=19)
    pf.check_and_failover(router, min_total=10)
    chain = ["prov_1", "prov_0"]  # 已经是重排后的
    out = pf.apply_failover_to_chain("get_x", chain)
    assert out == ["prov_1", "prov_0"]


def test_apply_failover_missing_provider_returns_original():
    """chain 中缺 primary/backup（异常情况）→ 不动。"""
    router = _make_router(["get_x"], providers_per_method=2)
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=6)
    router._stats["get_x:prov_1"] = _make_stat("get_x", "prov_1", total=20, success=19)
    pf.check_and_failover(router, min_total=10)
    chain = ["other_prov", "another"]  # 不含 primary / backup
    out = pf.apply_failover_to_chain("get_x", chain)
    assert out == chain


# ── 5. get_failover_overview ─────────────────────────


def test_overview_empty():
    out = pf.get_failover_overview()
    assert out["active_count"] == 0
    assert out["tracked_methods"] == 0
    assert out["active"] == []
    assert out["all"] == []


def test_overview_with_active():
    router = _make_router(["get_x", "get_y"], providers_per_method=2)
    router._stats["get_x:prov_0"] = _make_stat("get_x", "prov_0", total=20, success=6)
    router._stats["get_x:prov_1"] = _make_stat("get_x", "prov_1", total=20, success=19)
    router._stats["get_y:prov_0"] = _make_stat("get_y", "prov_0", total=20, success=20)
    router._stats["get_y:prov_1"] = _make_stat("get_y", "prov_1", total=20, success=20)
    pf.check_and_failover(router, min_total=10)
    out = pf.get_failover_overview()
    assert out["active_count"] == 1
    assert out["tracked_methods"] == 1  # 只 get_x
    assert out["active"][0]["method"] == "get_x"


# ── 6. HTTP 端点 ────────────────────────────────


def test_endpoint_overview_no_auth_state():
    async def _get():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.get("/api/health/failover")

    r = asyncio.run(_get())
    assert r.status_code == 200
    body = r.json()
    assert body["active_count"] == 0
    assert body["tracked_methods"] == 0


def test_endpoint_history_empty():
    async def _get():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.get("/api/health/failover/history?limit=10")

    r = asyncio.run(_get())
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["limit"] == 10


def test_endpoint_state_404_when_missing():
    async def _get():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.get("/api/health/failover/get_unknown")

    r = asyncio.run(_get())
    assert r.status_code == 404


def test_endpoint_force_recover_returns_false_when_no_state():
    async def _post():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.post("/api/health/failover/get_x/recover")

    r = asyncio.run(_post())
    assert r.status_code == 200
    body = r.json()
    assert body["recovered"] is False
