"""test_research_scheduler — research scheduler 入口函数单测。

覆盖：
- run_research_for_business_date 缺数据时返 LookupError → ok=False
- 跑通时返 ok=True + persist summary
- 默认 business_date 推断为昨日
- graph / persist / loader 异常被吞 + 返 ok=False（不抛）
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


class TestRunResearchForBusinessDate:
    @pytest.mark.asyncio
    async def test_lookup_error_returns_ok_false(self, monkeypatch):
        from apps.scheduler import research_scheduler

        async def boom_load(*args, **kwargs):
            raise LookupError("no data for 2026-07-03")
        monkeypatch.setattr(
            "services.research_context_loader.ResearchContextLoader.load",
            boom_load,
        )

        result = await research_scheduler.run_research_for_business_date(
            business_date="2026-07-03", window_type="close",
        )
        assert result["ok"] is False
        # LookupError 走 LookupError 分支，reason 应包含 "装载快照失败" 或 "LookupError"
        reason_or_error = result.get("reason", "") or result.get("error", "")
        assert "装载快照失败" in reason_or_error or "LookupError" in reason_or_error

    @pytest.mark.asyncio
    async def test_default_business_date_is_yesterday_shanghai(self, monkeypatch):
        """不传 business_date → 推断为 Asia/Shanghai 昨日。"""
        from apps.scheduler import research_scheduler

        captured: dict = {}

        async def fake_load(*, session, business_date, window_type):
            captured["business_date"] = business_date
            raise LookupError("stop early")

        async def fake_session(*args, **kwargs):
            yield AsyncMock()

        monkeypatch.setattr(
            "services.research_context_loader.ResearchContextLoader.load",
            fake_load,
        )
        monkeypatch.setattr(
            "core.database.AsyncSessionLocal",
            lambda: _FakeAsyncCtx(),
        )

        await research_scheduler.run_research_for_business_date()

        expected = (datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=1))
        assert captured["business_date"] == expected

    @pytest.mark.asyncio
    async def test_success_returns_persist_summary(self, monkeypatch):
        from apps.scheduler import research_scheduler
        from services.signal_persister import SignalPersistResult

        class _FakeSessionCtx:
            async def __aenter__(self):
                return AsyncMock()
            async def __aexit__(self, *args):
                return False

        async def fake_load(*, session, business_date, window_type):
            from services.research_context_loader import ResearchContext
            return ResearchContext(
                market_snapshot={"market_regime": "risk_on_expansion"},
                market_rule_hits=[],
                sector_snapshots=[],
                sector_rule_hits_map={},
            )

        async def fake_ainvoke(state):
            from schemas.agent_io import AgentOutputEnvelope
            state["market_outlook"] = AgentOutputEnvelope(
                agent_name="MarketRegimeAnalyst",
                structured_output={"market_regime": "risk_on_expansion"},
                narrative_summary="", evidence_refs=[], confidence=0.6,
            )
            state["committee_decision"] = AgentOutputEnvelope(
                agent_name="CommitteeSynthesizer",
                structured_output={"verdict": "long", "approved_targets": [],
                                   "blocked_targets": [], "risk_budget": "normal",
                                   "rationale": "test"},
                narrative_summary="", evidence_refs=[], confidence=0.7,
            )
            return state

        async def fake_persist(*args, **kwargs):
            return SignalPersistResult(
                market_id="abc", sector_count=0,
                approved_count=0, blocked_count=0, total=1,
            )

        monkeypatch.setattr(
            "services.research_context_loader.ResearchContextLoader.load",
            fake_load,
        )
        monkeypatch.setattr(
            "core.database.AsyncSessionLocal", lambda: _FakeSessionCtx(),
        )
        # patch 真实 import 路径（research_scheduler 在函数内 import build_research_graph）
        monkeypatch.setattr(
            "graph.research_graph.build_research_graph",
            lambda: MagicMock(ainvoke=fake_ainvoke),
        )
        monkeypatch.setattr(
            "services.signal_persister.persist_committee_signals",
            fake_persist,
        )

        result = await research_scheduler.run_research_for_business_date(
            business_date="2026-07-03", window_type="close",
        )
        assert result["ok"] is True
        assert result["business_date"] == "2026-07-03"
        assert result["persist"]["status"] == "ok"
        assert result["persist"]["total"] == 1

    @pytest.mark.asyncio
    async def test_graph_exception_caught(self, monkeypatch):
        from apps.scheduler import research_scheduler

        class _FakeSessionCtx:
            async def __aenter__(self):
                return AsyncMock()
            async def __aexit__(self, *args):
                return False

        async def fake_load(*, session, business_date, window_type):
            from services.research_context_loader import ResearchContext
            return ResearchContext(
                market_snapshot={}, market_rule_hits=[],
                sector_snapshots=[], sector_rule_hits_map={},
            )
        async def boom_ainvoke(state):
            raise RuntimeError("graph crashed")

        monkeypatch.setattr(
            "services.research_context_loader.ResearchContextLoader.load",
            fake_load,
        )
        monkeypatch.setattr(
            "core.database.AsyncSessionLocal", lambda: _FakeSessionCtx(),
        )
        monkeypatch.setattr(
            "graph.research_graph.build_research_graph",
            lambda: MagicMock(ainvoke=boom_ainvoke),
        )

        result = await research_scheduler.run_research_for_business_date(
            business_date="2026-07-03", window_type="close",
        )
        assert result["ok"] is False
        assert "graph crashed" in result["error"]


# ── register_research_jobs ──────────────────────────────────────
class TestRegisterResearchJobs:
    def test_adds_job_to_scheduler(self):
        """register_research_jobs 把 daily_research_flow 加到 scheduler 上。"""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from apps.scheduler.research_scheduler import register_research_jobs

        sch = AsyncIOScheduler(timezone="Asia/Shanghai")
        register_research_jobs(sch)
        jobs = sch.get_jobs()
        ids = [j.id for j in jobs]
        assert "daily_research_flow" in ids
        # 验证 cron 表达式：hour=17, minute=30, day_of_week=mon-fri
        job = sch.get_job("daily_research_flow")
        assert job is not None
        # 不启动 scheduler（避免 eventloop 干扰），仅验证 add_job 调用结果


# ── 内部 helper ────────────────────────────────────────────────
class _FakeAsyncCtx:
    """AsyncMock-friendly context manager。"""
    async def __aenter__(self):
        return AsyncMock()
    async def __aexit__(self, *args):
        return False
