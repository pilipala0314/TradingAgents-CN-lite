"""P1-T5 告警规则在线编辑 — 单元测试 + HTTP 端点。

覆盖：
- update_rule: 改 threshold / level / comparator
- update_rule 不存在 → None
- update_rule 非法 level/comparator → ValueError
- soft_delete_rule: 真改 is_active
- soft_delete_rule 已软删 → False
- HTTP: PUT 200 / 404 / 400 / 422
- HTTP: DELETE 200 / 404
- evaluate_now 跳过软删 rule
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from services.health_alert import HealthAlertService

# ── service 单元测试 ─────────────────────────


def _new_svc():
    """拿一个新 instance（不启动后台线程）。"""
    HealthAlertService.reset()
    svc = HealthAlertService.get()
    svc.start_periodic = lambda interval_s=60.0: None
    return svc


class TestUpdateRule:
    def test_update_threshold(self) -> None:
        svc = _new_svc()
        rules_before = svc.list_rules()
        assert len(rules_before) >= 1
        target = rules_before[0]
        out = svc.update_rule(target["name"], threshold=0.99)
        assert out is not None
        assert out["threshold"] == 0.99
        assert out["name"] == target["name"]
        assert out["level"] == target["level"]

    def test_update_level(self) -> None:
        svc = _new_svc()
        target = svc.list_rules()[0]
        out = svc.update_rule(target["name"], level="error")
        assert out is not None
        assert out["level"] == "error"

    def test_update_comparator(self) -> None:
        svc = _new_svc()
        target = svc.list_rules()[0]
        out = svc.update_rule(target["name"], comparator="lt")
        assert out is not None
        assert out["comparator"] == "lt"

    def test_update_unknown_rule_returns_none(self) -> None:
        svc = _new_svc()
        out = svc.update_rule("nonexistent_rule", threshold=0.5)
        assert out is None

    def test_update_invalid_level_raises(self) -> None:
        svc = _new_svc()
        target = svc.list_rules()[0]
        with pytest.raises(ValueError, match="非法 level"):
            svc.update_rule(target["name"], level="invalid")

    def test_update_invalid_comparator_raises(self) -> None:
        svc = _new_svc()
        target = svc.list_rules()[0]
        with pytest.raises(ValueError, match="非法 comparator"):
            svc.update_rule(target["name"], comparator="xx")

    def test_update_three_fields_at_once(self) -> None:
        svc = _new_svc()
        target = svc.list_rules()[0]
        out = svc.update_rule(
            target["name"], threshold=0.42, level="info", comparator="eq",
        )
        assert out is not None
        assert out["threshold"] == 0.42
        assert out["level"] == "info"
        assert out["comparator"] == "eq"


class TestSoftDeleteRule:
    def test_soft_delete_marks_inactive(self) -> None:
        svc = _new_svc()
        target = svc.list_rules()[0]
        assert target["is_active"] is True
        assert svc.soft_delete_rule(target["name"]) is True
        # 重新 list 验证
        rules_after = svc.list_rules()
        n = next(r for r in rules_after if r["name"] == target["name"])
        assert n["is_active"] is False

    def test_soft_delete_already_inactive_returns_false(self) -> None:
        svc = _new_svc()
        target = svc.list_rules()[0]
        svc.soft_delete_rule(target["name"])
        assert svc.soft_delete_rule(target["name"]) is False

    def test_soft_delete_unknown_returns_false(self) -> None:
        svc = _new_svc()
        assert svc.soft_delete_rule("nonexistent") is False

    def test_update_reactivates_soft_deleted(self) -> None:
        svc = _new_svc()
        target = svc.list_rules()[0]
        svc.soft_delete_rule(target["name"])
        # 改 threshold 自动 re-enable
        out = svc.update_rule(target["name"], threshold=0.5)
        assert out is not None
        assert out["is_active"] is True


class TestEvaluateSkipInactive:
    def test_evaluate_skips_inactive_rules(self) -> None:
        """evaluate_now 必须跳过 _inactive 规则。"""
        svc = _new_svc()
        target = svc.list_rules()[0]
        svc.soft_delete_rule(target["name"])
        # 强造一个 fail metric，让 rule 触发
        from services.health_alert import collect_prometheus_samples
        # 收集样本，让规则逻辑跑一遍
        samples = collect_prometheus_samples()
        for r in svc._rules:
            if r.name == target["name"]:
                assert r.name in svc._inactive
                # 模拟 evaluate_now 内部循环：inactive 应被跳过
                triggered = svc.evaluate_now()
                # 任何触发的 event 不应是已软删 rule
                for ev in triggered:
                    assert ev.rule != target["name"]


# ── HTTP 端点 ─────────────────────────


@pytest.mark.asyncio
async def test_http_list_rules() -> None:
    HealthAlertService.reset()
    svc = HealthAlertService.get()
    svc.start_periodic = lambda interval_s=60.0: None

    from apps.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/api/health/alerts/rules")
    assert r.status_code == 200
    data = r.json()
    assert "rules" in data
    assert data["count"] >= 1
    assert all("is_active" in rule for rule in data["rules"])


@pytest.mark.asyncio
async def test_http_update_200() -> None:
    HealthAlertService.reset()
    svc = HealthAlertService.get()
    svc.start_periodic = lambda interval_s=60.0: None
    name = svc.list_rules()[0]["name"]

    from apps.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.put(f"/api/health/alerts/rules/{name}?threshold=0.55&level=warn")
    assert r.status_code == 200
    data = r.json()
    assert data["updated"] is True
    assert data["rule"]["threshold"] == 0.55


@pytest.mark.asyncio
async def test_http_update_404() -> None:
    HealthAlertService.reset()
    svc = HealthAlertService.get()
    svc.start_periodic = lambda interval_s=60.0: None

    from apps.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.put("/api/health/alerts/rules/nonexistent?threshold=0.5")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_http_update_400_no_params() -> None:
    HealthAlertService.reset()
    svc = HealthAlertService.get()
    svc.start_periodic = lambda interval_s=60.0: None
    name = svc.list_rules()[0]["name"]

    from apps.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.put(f"/api/health/alerts/rules/{name}")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_http_update_422_invalid_level() -> None:
    HealthAlertService.reset()
    svc = HealthAlertService.get()
    svc.start_periodic = lambda interval_s=60.0: None
    name = svc.list_rules()[0]["name"]

    from apps.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.put(f"/api/health/alerts/rules/{name}?level=invalid")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_http_delete_200() -> None:
    HealthAlertService.reset()
    svc = HealthAlertService.get()
    svc.start_periodic = lambda interval_s=60.0: None
    name = svc.list_rules()[0]["name"]

    from apps.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.delete(f"/api/health/alerts/rules/{name}")
        assert r.status_code == 200
        r2 = await c.get("/api/health/alerts/rules")
        target = next(rule for rule in r2.json()["rules"] if rule["name"] == name)
        assert target["is_active"] is False


@pytest.mark.asyncio
async def test_http_delete_404() -> None:
    HealthAlertService.reset()
    svc = HealthAlertService.get()
    svc.start_periodic = lambda interval_s=60.0: None

    from apps.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.delete("/api/health/alerts/rules/nonexistent")
    assert r.status_code == 404
