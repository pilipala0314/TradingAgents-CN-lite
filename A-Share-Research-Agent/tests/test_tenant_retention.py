"""test_tenant_retention — P5.3 多租户 + 数据保留单测。"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

from services.retention import (
    DEFAULT_POLICY,
    get_policy,
)
from services.tenant import (
    DEFAULT_TENANT,
    current_tenant_id,
)

# ── current_tenant_id ──────────────────────────


def test_current_tenant_id_default() -> None:
    os.environ.pop("ASHARE_TENANT", None)
    assert current_tenant_id() == DEFAULT_TENANT


def test_current_tenant_id_explicit() -> None:
    assert current_tenant_id("acme") == "acme"


def test_current_tenant_id_strip_whitespace() -> None:
    assert current_tenant_id("  acme  ") == "acme"


def test_current_tenant_id_empty_returns_default() -> None:
    assert current_tenant_id("") == DEFAULT_TENANT
    assert current_tenant_id("   ") == DEFAULT_TENANT


def test_current_tenant_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASHARE_TENANT", "from-env")
    assert current_tenant_id() == "from-env"


def test_current_tenant_id_explicit_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASHARE_TENANT", "from-env")
    assert current_tenant_id("from-arg") == "from-arg"


# ── get_policy ────────────────────────────


def test_default_policy_keys() -> None:
    pol = get_policy()
    assert "fact_market_bar" in pol
    assert "fact_event_raw" in pol
    assert pol["fact_event_raw"]["retain_days"] == 90


def test_policy_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETENTION_FACT_EVENT_RAW_DAYS", "30")
    pol = get_policy()
    assert pol["fact_event_raw"]["retain_days"] == 30


def test_policy_env_invalid_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETENTION_FACT_EVENT_RAW_DAYS", "abc")
    pol = get_policy()
    assert pol["fact_event_raw"]["retain_days"] == DEFAULT_POLICY["fact_event_raw"]["retain_days"]


# ── get_retention_status / run_retention（无 DB 路径）─────


@pytest.mark.asyncio
async def test_get_retention_status_no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 不可用时返 0 行数但不抛错。"""
    from services import retention
    # 模拟 _count_table 全失败
    async def fake_count(name: str) -> int:
        return -1
    monkeypatch.setattr(retention, "_count_table", fake_count)
    s = await retention.get_retention_status()
    assert "policy" in s
    assert "tables" in s
    # -1 表示 DB 失败
    for tbl in s["tables"]:
        assert s["tables"][tbl]["current_rows"] == -1


@pytest.mark.asyncio
async def test_run_retention_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """dry_run 模式：模拟每张表都待删一些行；记录调用参数。"""
    from services import retention
    calls: list[tuple[str, int, int, bool]] = []

    async def fake_delete(name: str, days: int, min_rows: int, dry_run: bool) -> int:
        calls.append((name, days, min_rows, dry_run))
        return 100
    monkeypatch.setattr(retention, "_delete_older_than", fake_delete)
    r = await retention.run_retention(dry_run=True)
    assert r["dry_run"] is True
    assert len(calls) == len(DEFAULT_POLICY)
    # 验证每次调用都传了 dry_run=True
    assert all(c[3] is True for c in calls)
    # 每张表都返了 deleted count
    for tbl in DEFAULT_POLICY:
        assert r["deleted"][tbl] == 100


@pytest.mark.asyncio
async def test_run_retention_collects_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """部分表失败 → errors 列表里有这些表。"""
    from services import retention

    async def fake_delete(name: str, days: int, min_rows: int, dry_run: bool) -> int:
        if "raw" in name:
            return -1  # 失败
        return 0
    monkeypatch.setattr(retention, "_delete_older_than", fake_delete)
    r = await retention.run_retention(dry_run=True)
    assert "fact_event_raw" in r["errors"]
