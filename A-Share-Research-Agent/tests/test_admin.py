"""P0-T7 scheduler status endpoint 单元测试。

覆盖：
- collect_scheduler_status 无 scheduler → running=False / 空 jobs
- collect_scheduler_status mock scheduler → jobs 完整字段
- _format_next_run 边界（None / tz-naive / tz-aware）
- /admin/scheduler/status 端点：未注册时 200 + 空；注册后 jobs 渲染
- main.py lifespan 会调用 set_scheduler / clear_scheduler
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import app
from services import scheduler_status_service as sss


def _run_async(coro):
    """同步 helper：跑 async coroutine。"""
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_scheduler_ref():
    """每个测试前后清空全局 registry，避免测试污染。"""
    sss.clear_scheduler()
    yield
    sss.clear_scheduler()


# ── 1. collect_scheduler_status 边界 ─────────────────────────


def test_collect_status_no_scheduler_returns_empty():
    sss.clear_scheduler()
    result = sss.collect_scheduler_status()
    assert result["running"] is False
    assert result["job_count"] == 0
    assert result["jobs"] == []
    assert result["timezone"] == ""
    assert "fetched_at" in result


def test_collect_status_with_mock_scheduler_full():
    """Mock APScheduler + 2 jobs（含 next_run_time / trigger / coalesce）"""
    mock_sched = MagicMock()
    mock_sched.timezone = "Asia/Shanghai"

    now = datetime.now(timezone.utc)
    next_run = now + timedelta(hours=3)
    job1 = MagicMock()
    job1.id = "daily_close_etl"
    job1.name = "_safe_dispatch_yesterday"
    job1.trigger = "cron[day_of_week='mon-fri', hour='17', minute='0']"
    job1.next_run_time = next_run
    job1.coalesce = True
    job1.max_instances = 1
    job1.func = _safe_dispatch_yesterday
    job1.func_ref = None

    job2 = MagicMock()
    job2.id = "daily_research_flow"
    job2.name = "_safe_run_research"
    job2.trigger = "cron[day_of_week='mon-fri', hour='17', minute='30']"
    job2.next_run_time = None  # 边界：无 next_run
    job2.coalesce = True
    job2.max_instances = 1
    job2.func = _safe_run_research
    job2.func_ref = None

    mock_sched.get_jobs.return_value = [job1, job2]
    sss.set_scheduler(mock_sched)

    result = sss.collect_scheduler_status()
    assert result["running"] is True
    assert result["timezone"] == "Asia/Shanghai"
    assert result["job_count"] == 2
    assert len(result["jobs"]) == 2
    j1 = result["jobs"][0]
    assert j1["id"] == "daily_close_etl"
    assert j1["coalesce"] is True
    assert j1["max_instances"] == 1
    assert j1["next_run_time"] is not None
    assert j1["next_run_in_sec"] is not None
    assert 0 < j1["next_run_in_sec"] < 3600 * 4  # 3h 略小
    j2 = result["jobs"][1]
    assert j2["id"] == "daily_research_flow"
    assert j2["next_run_time"] is None
    assert j2["next_run_in_sec"] is None


def test_collect_status_get_jobs_raises_returns_empty():
    """Scheduler.get_jobs 抛错 → 容错返回空 jobs，不炸。"""
    mock_sched = MagicMock()
    mock_sched.timezone = "Asia/Shanghai"
    mock_sched.get_jobs.side_effect = RuntimeError("scheduler broken")
    sss.set_scheduler(mock_sched)
    result = sss.collect_scheduler_status()
    assert result["running"] is True  # scheduler 注册了就算 running
    assert result["job_count"] == 0
    assert result["jobs"] == []


# ── 2. _format_next_run 边界 ─────────────────────────


def test_format_next_run_none():
    assert sss._format_next_run(None) is None


def test_format_next_run_tz_aware():
    dt = datetime(2026, 7, 8, 17, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    out = sss._format_next_run(dt)
    assert out is not None
    assert "2026-07-08" in out
    assert "+08:00" in out


def test_format_next_run_naive_assumes_utc():
    dt = datetime(2026, 7, 8, 17, 0, 0)  # naive
    out = sss._format_next_run(dt)
    assert out is not None
    assert "2026-07-08" in out


# ── 3. /admin/scheduler/status 端点 ─────────────────────────


def test_endpoint_no_scheduler_returns_empty_200():
    """ASGITransport 不触发 lifespan，registry 保持空 → running=False。"""
    async def _get():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            return await c.get("/admin/scheduler/status")

    r = asyncio.run(_get())
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["job_count"] == 0
    assert body["jobs"] == []


def test_endpoint_with_mock_scheduler():
    """注册 mock scheduler 后端点返回完整 jobs。"""
    sss.clear_scheduler()
    mock_sched = MagicMock()
    mock_sched.timezone = "Asia/Shanghai"
    now = datetime.now(timezone.utc)
    job = MagicMock()
    job.id = "daily_close_etl"
    job.name = "_safe_dispatch_yesterday"
    job.trigger = "cron[hour='17']"
    job.next_run_time = now + timedelta(hours=2)
    job.coalesce = True
    job.max_instances = 1
    job.func = _safe_dispatch_yesterday
    job.func_ref = None
    mock_sched.get_jobs.return_value = [job]
    sss.set_scheduler(mock_sched)

    async def _get():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            return await c.get("/admin/scheduler/status")

    r = asyncio.run(_get())
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is True
    assert body["job_count"] == 1
    assert body["jobs"][0]["id"] == "daily_close_etl"
    assert body["jobs"][0]["coalesce"] is True
    assert body["jobs"][0]["next_run_in_sec"] is not None


# ── 4. main.py lifespan 集成（mock 掉真实 scheduler） ──────────


def test_main_lifespan_registers_and_clears_scheduler():
    """验证 lifespan 启动时会调 set_scheduler，关闭时会调 clear_scheduler。

    patch 目标为 main.py 模块里的 _set_sched / _clear_sched 别名（被 lifespan 调用）。
    """
    with patch("apps.api.main.start_etl_scheduler") as mock_etl, \
         patch("apps.api.main.register_research_jobs"), \
         patch("apps.api.main._set_sched") as mock_set, \
         patch("apps.api.main._clear_sched") as mock_clear, \
         patch("apps.scheduler.etl_scheduler.start_scheduler", create=True):
        from fastapi.testclient import TestClient
        mock_sched_inst = MagicMock()
        mock_etl.return_value = mock_sched_inst
        with TestClient(app) as client:
            # lifespan 启动后 _set_sched 被调
            mock_set.assert_called_with(mock_sched_inst)
        # lifespan 退出后 _clear_sched 被调
        mock_clear.assert_called()


# ── helpers（job.func mock 用） ─────────────────────────


def _safe_dispatch_yesterday() -> None:
    pass


def _safe_run_research() -> None:
    pass
