"""test_signals_api — /v1/research/signals/{latest,today,by_id} 端点单测。

覆盖：
- /signals/latest 返 4 类分组 + summary + business_date
- /signals/today 返今日 4 类
- /signals/{id} 返单条 / 400 / 404
- /report 端点返回 persist 字段
"""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from models.outputs import FactDecisionTrace, FactStrategyOutput
from services.signal_persister import (
    STRATEGY_APPROVED,
    STRATEGY_BLOCKED,
    STRATEGY_MARKET,
    STRATEGY_SECTOR,
)


# ── helpers ──────────────────────────────────────────────────────
def _make_row(
    entity_type: str,
    strategy_type: str,
    direction: str = "long",
    confidence: float = 0.7,
    business_date: date = date(2026, 7, 3),
    payload: dict | None = None,
) -> FactStrategyOutput:
    return FactStrategyOutput(
        strategy_id=uuid.uuid4(),
        entity_type=entity_type,
        entity_id=uuid.uuid4(),
        business_date=business_date,
        window_type="close",
        horizon="medium",
        direction=direction,
        strategy_type=strategy_type,
        confidence=confidence,
        supporting_evidence=[],
        invalid_conditions=[],
        output_payload=payload or {},
        output_version=1,
    )


class _FakeAsyncResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return MagicMock(all=lambda: self._rows or [])

    def scalar_one_or_none(self):
        return self._scalar


def _make_session(rows: list[FactStrategyOutput] | None = None) -> AsyncMock:
    """构造一个 fake session：execute 返 _FakeAsyncResult。"""
    session = AsyncMock()
    session.execute.return_value = _FakeAsyncResult(rows=rows or [])
    return session


def _make_trace_row(
    *,
    business_date: date = date(2026, 7, 3),
    decision_layer: str = "committee",
    verdict: str = "long",
    confidence: float = 0.8,
    fallback_used: bool = False,
    llm_error: str | None = None,
    model_version: str = "deepseek-chat",
    market_regime: str = "risk_on_expansion",
    rationale: str = "基于多因子共振，维持偏多",
    rule_baseline: dict | None = None,
    llm_parsed: dict | None = None,
    inputs_snapshot: dict | None = None,
    final_decision: dict | None = None,
) -> FactDecisionTrace:
    return FactDecisionTrace(
        trace_id=uuid.uuid4(),
        business_date=business_date,
        window_type="close",
        decision_layer=decision_layer,
        market_regime=market_regime,
        verdict=verdict,
        confidence=confidence,
        risk_budget="normal",
        fallback_used=fallback_used,
        llm_error=llm_error,
        model_version=model_version,
        rule_baseline=rule_baseline or {"verdict": verdict, "confidence": 0.6},
        llm_response_raw='{"verdict":"long"}',
        llm_parsed=llm_parsed or {"verdict": verdict, "confidence": confidence},
        inputs_snapshot=inputs_snapshot or {
            "market_outlook": {"market_regime": market_regime},
            "sector_outlooks": [{"sector_id": "sw_electronics"}],
            "stock_outlooks": [{"symbol": "600519"}],
        },
        final_decision=final_decision or {
            "verdict": verdict,
            "confidence": confidence,
            "approved_targets": [{"symbol": "600519"}],
            "blocked_targets": [],
            "risk_budget": "normal",
            "rationale": rationale,
        },
        rationale=rationale,
    )


# ── 端点测试（dependency override） ─────────────────────────────
def _override_get_db(session: AsyncMock):
    async def _gen():
        yield session
    return _gen


@pytest.fixture
def client():
    return TestClient(app)


class TestSignalsLatest:
    def test_returns_4_groups_and_summary(self, client, monkeypatch):
        rows = [
            _make_row("market", STRATEGY_MARKET, "long", 0.8, payload={"verdict": "long", "risk_budget": "normal"}),
            _make_row("sector", STRATEGY_SECTOR, "long", 0.85, payload={"sector_name": "电子", "mainline_flag": True, "sector_stage": "confirmed"}),
            _make_row("stock", STRATEGY_APPROVED, "long", 0.75, payload={"symbol": "000636", "stock_snapshot": {}}),
            _make_row("stock", STRATEGY_BLOCKED, "avoid", 0.8, payload={"symbol": "999999"}, ),
        ]
        session = _make_session(rows=rows)
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            # 传 business_date 跳过 max_date query（避免 mock 不一致）
            r = client.get("/v1/research/signals/latest?business_date=2026-07-03")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["business_date"] == "2026-07-03"
        assert set(data["groups"].keys()) == {"market", "sector", "approved", "blocked"}
        assert data["summary"]["market"] == 1
        assert data["summary"]["sector"] == 1
        assert data["summary"]["approved"] == 1
        assert data["summary"]["blocked"] == 1

    def test_empty_groups(self, client, monkeypatch):
        session = _make_session(rows=[])
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.get("/v1/research/signals/latest?business_date=2026-07-03")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert data["summary"] == {"market": 0, "sector": 0, "approved": 0, "blocked": 0}


class TestSignalsToday:
    def test_returns_today_date(self, client, monkeypatch):
        session = _make_session(rows=[])
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.get("/v1/research/signals/today")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        # 业务日期为今日（不验具体值，验格式 + keys 齐）
        assert "business_date" in data
        assert set(data["groups"].keys()) == {"market", "sector", "approved", "blocked"}


class TestSignalsById:
    def test_invalid_uuid_returns_400(self, client):
        r = client.get("/v1/research/signals/not-a-uuid")
        assert r.status_code == 400
        assert "invalid uuid" in r.json()["detail"]

    def test_not_found_returns_404(self, client, monkeypatch):
        session = _make_session(rows=[])
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.get(f"/v1/research/signals/{uuid.uuid4()}")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 404

    def test_found_returns_signal_dict(self, client, monkeypatch):
        row = _make_row("market", STRATEGY_MARKET, "long", 0.8,
                        payload={"verdict": "long", "risk_budget": "normal"})
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        session = AsyncMock()
        session.execute.return_value = result
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.get(f"/v1/research/signals/{row.strategy_id}")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["signal"]["strategy_id"] == str(row.strategy_id)
        assert data["signal"]["strategy_type"] == STRATEGY_MARKET


class TestDecisionTraces:
    def test_latest_returns_summary_and_rows(self, client):
        rows = [
            _make_trace_row(),
            _make_trace_row(
                verdict="neutral",
                confidence=0.55,
                fallback_used=True,
                llm_error="timeout",
                llm_parsed={},
                rationale="",
                inputs_snapshot={
                    "market_outlook": {"market_regime": "neutral_mixed"},
                    "sector_outlooks": [],
                    "stock_outlooks": [],
                },
            ),
        ]
        session = _make_session(rows=rows)
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.get("/v1/research/decision-traces/latest?business_date=2026-07-03&limit=10")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["business_date"] == "2026-07-03"
        assert len(data["traces"]) == 2
        assert data["summary"]["total"] == 2
        assert data["summary"]["fallback_count"] == 1
        assert data["summary"]["llm_error_count"] == 1
        assert data["summary"]["model_versions"] == ["deepseek-chat"]
        assert data["summary"]["input_snapshot_complete_rate"] == 0.5

    def test_trace_by_id_returns_scientific_checks(self, client):
        row = _make_trace_row()
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        session = AsyncMock()
        session.execute.return_value = result
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.get(f"/v1/research/decision-traces/{row.trace_id}")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["trace"]["trace_id"] == str(row.trace_id)
        checks = data["trace"]["scientific_checks"]
        assert checks["input_snapshot_complete"] is True
        assert checks["rule_baseline_present"] is True
        assert checks["llm_parse_success"] is True
        assert checks["final_decision_present"] is True
        assert checks["rationale_present"] is True

    def test_trace_by_id_invalid_uuid_returns_400(self, client):
        r = client.get("/v1/research/decision-traces/not-a-uuid")
        assert r.status_code == 400
        assert "invalid uuid" in r.json()["detail"]


class TestResearchAudit:
    def test_audit_latest_returns_consistency(self, client, monkeypatch):
        from apps.api.routers import research as research_router

        grouped = {
            "market": [{
                "strategy_id": str(uuid.uuid4()),
                "entity_type": "market",
                "entity_id": str(uuid.uuid4()),
                "business_date": "2026-07-03",
                "window_type": "close",
                "horizon": "medium",
                "direction": "long",
                "strategy_type": STRATEGY_MARKET,
                "confidence": 0.8,
                "max_position": None,
                "supporting_evidence": [],
                "invalid_conditions": [],
                "output_payload": {
                    "verdict": "long",
                    "risk_budget": "normal",
                    "market_regime": "risk_on_expansion",
                    "fallback_used": False,
                },
                "model_version": "deepseek-chat",
                "created_at": "2026-07-03T17:30:00",
            }],
            "sector": [],
            "approved": [{"strategy_id": str(uuid.uuid4())}],
            "blocked": [],
        }
        traces = [{
            "trace_id": str(uuid.uuid4()),
            "business_date": "2026-07-03",
            "window_type": "close",
            "decision_layer": "committee",
            "market_regime": "risk_on_expansion",
            "verdict": "long",
            "confidence": 0.8,
            "risk_budget": "normal",
            "fallback_used": False,
            "llm_error": None,
            "model_version": "deepseek-chat",
            "rule_baseline": {"verdict": "long"},
            "llm_response_raw": "{}",
            "llm_parsed": {"verdict": "long"},
            "inputs_snapshot": {
                "market_outlook": {"market_regime": "risk_on_expansion"},
                "sector_outlooks": [{"sector_id": "sw_electronics"}],
                "stock_outlooks": [{"symbol": "600519"}],
            },
            "final_decision": {
                "verdict": "long",
                "confidence": 0.8,
                "approved_targets": [{"symbol": "600519"}],
                "blocked_targets": [],
                "risk_budget": "normal",
            },
            "rationale": "一致",
            "created_at": "2026-07-03T17:30:00",
        }]
        backtests = [{
            "run_id": str(uuid.uuid4()),
            "job_id": str(uuid.uuid4()),
            "business_date": "2026-07-03",
            "status": "completed",
            "config_hash": "abc123",
            "config_json": {"strategy_name": "momentum_20d", "universe": "hs300"},
            "result_json": None,
            "error_message": None,
            "cost_total_pct": 0.02,
            "benchmark_alpha": 0.11,
            "benchmark_beta": 0.98,
            "attribution_json": {"rebalance": []},
            "started_at": "2026-07-03T18:00:00",
            "finished_at": "2026-07-03T18:05:00",
            "created_at": "2026-07-03T18:00:00",
        }]

        monkeypatch.setattr(research_router, "fetch_signals_latest", AsyncMock(return_value=grouped))
        monkeypatch.setattr(research_router, "fetch_decision_traces_by_date", AsyncMock(return_value=traces))
        monkeypatch.setattr(research_router, "list_backtest_runs", AsyncMock(return_value=backtests))

        session = AsyncMock()
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.get("/v1/research/audit/latest?business_date=2026-07-03")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["business_date"] == "2026-07-03"
        assert data["consistency"]["verdict_match"] is True
        assert data["consistency"]["risk_budget_match"] is True
        assert data["consistency"]["market_regime_match"] is True
        assert data["consistency"]["approved_count_match"] is True
        assert data["consistency"]["backtest_run_count"] == 1
        assert data["consistency"]["completed_backtest_count"] == 1
        assert data["consistency"]["issues"] == []

    def test_audit_latest_empty_returns_empty_consistency(self, client, monkeypatch):
        from apps.api.routers import research as research_router

        monkeypatch.setattr(
            research_router,
            "fetch_signals_latest",
            AsyncMock(return_value={"market": [], "sector": [], "approved": [], "blocked": []}),
        )
        monkeypatch.setattr(
            research_router,
            "fetch_decision_traces_by_date",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            research_router,
            "list_backtest_runs",
            AsyncMock(return_value=[]),
        )
        session = AsyncMock()
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.get("/v1/research/audit/latest?business_date=2026-07-03")
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["business_date"] == "2026-07-03"
        assert data["consistency"]["trace_present"] is False
        assert data["consistency"]["market_signal_present"] is False
        assert data["consistency"]["issues"]


# ── /report 端点的 persist 字段 ────────────────────────────────
class TestReportPersistField:
    def test_report_returns_persist_summary(self, client, monkeypatch):
        """/report 跑完返回里有 'persist' 字段（status='ok' 或 'skipped' / 'failed'）。"""
        from graph.state import ResearchWorkflowState
        from schemas.agent_io import AgentOutputEnvelope

        market = AgentOutputEnvelope(
            agent_name="MarketRegimeAnalyst",
            structured_output={"market_regime": "risk_on_expansion"},
            narrative_summary="",
            evidence_refs=[],
            confidence=0.6,
        )
        committee = AgentOutputEnvelope(
            agent_name="CommitteeSynthesizer",
            structured_output={
                "verdict": "long",
                "approved_targets": [{"symbol": "000636", "confidence": 0.8}],
                "blocked_targets": [],
                "risk_budget": "normal",
                "rationale": "test",
            },
            narrative_summary="",
            evidence_refs=[],
            confidence=0.7,
        )
        fake_final: ResearchWorkflowState = {
            "business_date": "2026-07-03",
            "window_type": "close",
            "target_symbol": "",
            "market_snapshot": {},
            "market_rule_hits": [],
            "sector_snapshots": [],
            "sector_rule_hits_map": {},
            "stock_snapshots": [],
            "stock_rule_hits_map": {},
            "market_outlook": market,
            "sector_outlooks": [],
            "stock_outlooks": [],
            "committee_decision": committee,
            "final_report": "# A 股投研日报\n\ntest",
            "ranked_sectors": [],
            "halt_execution": False,
            "halt_reason": "",
        }

        # mock graph.ainvoke
        async def fake_ainvoke(state):
            return fake_final
        monkeypatch.setattr(
            "apps.api.routers.research.build_research_graph",
            lambda: MagicMock(ainvoke=fake_ainvoke),
        )

        # mock persist_committee_signals（patch routers.research 里 import 的名字）
        from services.signal_persister import SignalPersistResult
        async def fake_persist(*args, **kwargs):
            return SignalPersistResult(
                market_id=str(uuid.uuid4()),
                sector_count=0,
                approved_count=1,
                blocked_count=0,
                total=2,
            )
        monkeypatch.setattr(
            "apps.api.routers.research.persist_committee_signals",
            fake_persist,
        )

        session = AsyncMock()
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.post(
                "/v1/research/report",
                json={
                    "business_date": "2026-07-03",
                    "window_type": "close",
                    "market_snapshot": {},
                    "market_rule_hits": [],
                    "sector_snapshots": [],
                    "sector_rule_hits_map": {},
                    "stock_snapshots": [],
                    "stock_rule_hits_map": {},
                },
            )
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert "persist" in data
        assert data["persist"]["status"] == "ok"
        assert data["persist"]["approved_count"] == 1
        assert data["persist"]["total"] == 2

    def test_report_persist_failure_does_not_break(self, client, monkeypatch):
        """persist 抛异常时 report 仍返 200，persist.status='failed'。"""
        from graph.state import ResearchWorkflowState
        from schemas.agent_io import AgentOutputEnvelope

        market = AgentOutputEnvelope(
            agent_name="MarketRegimeAnalyst",
            structured_output={"market_regime": "neutral_mixed"},
            narrative_summary="", evidence_refs=[], confidence=0.5,
        )
        committee = AgentOutputEnvelope(
            agent_name="CommitteeSynthesizer",
            structured_output={"verdict": "neutral", "approved_targets": [],
                               "blocked_targets": [], "risk_budget": "normal", "rationale": "x"},
            narrative_summary="", evidence_refs=[], confidence=0.4,
        )
        fake_final: ResearchWorkflowState = {
            "business_date": "2026-07-03", "window_type": "close", "target_symbol": "",
            "market_snapshot": {}, "market_rule_hits": [],
            "sector_snapshots": [], "sector_rule_hits_map": {},
            "stock_snapshots": [], "stock_rule_hits_map": {},
            "market_outlook": market, "sector_outlooks": [], "stock_outlooks": [],
            "committee_decision": committee, "final_report": "# A 股投研日报",
            "ranked_sectors": [], "halt_execution": False, "halt_reason": "",
        }
        async def fake_ainvoke(state):
            return fake_final
        monkeypatch.setattr(
            "apps.api.routers.research.build_research_graph",
            lambda: MagicMock(ainvoke=fake_ainvoke),
        )
        # persist 抛异常 — patch routers.research 里的 import 名字
        async def boom_persist(*args, **kwargs):
            raise RuntimeError("DB down")
        monkeypatch.setattr(
            "apps.api.routers.research.persist_committee_signals",
            boom_persist,
        )

        session = AsyncMock()
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.post(
                "/v1/research/report",
                json={"business_date": "2026-07-03", "window_type": "close",
                      "market_snapshot": {}, "market_rule_hits": [],
                      "sector_snapshots": [], "sector_rule_hits_map": {},
                      "stock_snapshots": [], "stock_rule_hits_map": {}},
            )
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert data["persist"]["status"] == "failed"
        assert "DB down" in data["persist"]["error"]

    def test_report_skipped_when_no_committee(self, client, monkeypatch):
        from graph.state import ResearchWorkflowState

        fake_final: ResearchWorkflowState = {
            "business_date": "2026-07-03", "window_type": "close", "target_symbol": "",
            "market_snapshot": {}, "market_rule_hits": [],
            "sector_snapshots": [], "sector_rule_hits_map": {},
            "stock_snapshots": [], "stock_rule_hits_map": {},
            "market_outlook": None, "sector_outlooks": [], "stock_outlooks": [],
            "committee_decision": None, "final_report": "",
            "ranked_sectors": [], "halt_execution": True, "halt_reason": "panic",
        }
        async def fake_ainvoke(state):
            return fake_final
        monkeypatch.setattr(
            "apps.api.routers.research.build_research_graph",
            lambda: MagicMock(ainvoke=fake_ainvoke),
        )
        session = AsyncMock()
        app.dependency_overrides[__import__("core.database", fromlist=["get_db"]).get_db] = _override_get_db(session)
        try:
            r = client.post(
                "/v1/research/report",
                json={"business_date": "2026-07-03", "window_type": "close",
                      "market_snapshot": {}, "market_rule_hits": [],
                      "sector_snapshots": [], "sector_rule_hits_map": {},
                      "stock_snapshots": [], "stock_rule_hits_map": {}},
            )
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        data = r.json()
        assert data["halt"] is True
        assert data["persist"]["status"] == "skipped"
