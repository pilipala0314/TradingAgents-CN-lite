"""test_decision_drilldown — P0-T10 决策钻取单测。"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from services.decision_drilldown_service import (
    _extract_evidence_refs,
    _extract_role,
    _format_trace,
)

logger = logging.getLogger(__name__)


class TestExtractRole:
    def test_approved(self) -> None:
        final = {"approved_targets": ["600519", "000858"], "blocked_targets": []}
        assert _extract_role(final, "600519") == "approved"

    def test_blocked(self) -> None:
        final = {"approved_targets": [], "blocked_targets": ["000001"]}
        assert _extract_role(final, "000001") == "blocked"

    def test_unrelated(self) -> None:
        final = {"approved_targets": ["600519"], "blocked_targets": []}
        assert _extract_role(final, "000001") is None

    def test_handles_none(self) -> None:
        assert _extract_role({}, "600519") is None
        assert _extract_role({"approved_targets": None, "blocked_targets": None}, "600519") is None


class TestExtractEvidenceRefs:
    def test_extracts_evidence_for_matching_symbol(self) -> None:
        snapshot = {
            "stock_outlook": [
                {
                    "symbol": "600519",
                    "evidence_refs": [
                        {"type": "rule", "rule_id": "PE_OK", "value": 28.5},
                    ],
                },
                {"symbol": "000858", "evidence_refs": [{"type": "block"}]},
            ]
        }
        evs = _extract_evidence_refs(snapshot, "600519")
        assert len(evs) == 1
        assert evs[0]["rule_id"] == "PE_OK"

    def test_returns_empty_for_no_match(self) -> None:
        snapshot = {"stock_outlook": [{"symbol": "999999"}]}
        assert _extract_evidence_refs(snapshot, "600519") == []

    def test_handles_empty_stock_outlook(self) -> None:
        assert _extract_evidence_refs({}, "600519") == []
        assert _extract_evidence_refs({"stock_outlook": None}, "600519") == []


class TestFormatTrace:
    def test_formats_approved_trace(self) -> None:
        trace = MagicMock()
        trace.trace_id = uuid4()
        trace.business_date = date(2026, 7, 7)
        trace.window_type = "close"
        trace.verdict = "long"
        trace.confidence = 0.85
        trace.risk_budget = "normal"
        trace.rationale = "估值合理 + 资金流入"
        trace.fallback_used = False
        trace.model_version = "deepseek-chat"
        trace.market_regime = "risk_on_expansion"
        trace.llm_error = None
        trace.created_at = None
        trace.rule_baseline = {"verdict": "long", "rationale": "rule baseline"}
        trace.final_decision = {
            "approved_targets": ["600519"],
            "blocked_targets": ["000001"],
            "risk_budget": "normal",
        }
        trace.inputs_snapshot = {
            "stock_outlook": [
                {
                    "symbol": "600519",
                    "evidence_refs": [{"type": "rule", "rule_id": "PE_OK"}],
                },
            ]
        }

        result = _format_trace(trace, "600519")

        assert result["verdict"] == "long"
        assert result["role"] == "approved"
        assert result["confidence"] == 0.85
        assert "估值合理" in result["rationale"]
        assert result["fallback_used"] is False
        assert result["model_version"] == "deepseek-chat"
        assert result["market_regime"] == "risk_on_expansion"
        assert len(result["evidence_refs"]) == 1
        assert result["evidence_refs"][0]["rule_id"] == "PE_OK"
        assert result["approved_targets"] == ["600519"]
        assert result["blocked_targets"] == ["000001"]
        assert result["rule_baseline_verdict"] == "long"

    def test_formats_blocked_trace(self) -> None:
        trace = MagicMock()
        trace.trace_id = uuid4()
        trace.business_date = date(2026, 7, 7)
        trace.window_type = "close"
        trace.verdict = "avoid"
        trace.confidence = 0.92
        trace.risk_budget = "low"
        trace.rationale = "PE 过高 + 套牢盘"
        trace.fallback_used = True
        trace.model_version = None
        trace.market_regime = None
        trace.llm_error = "LLM timeout"
        trace.created_at = None
        trace.rule_baseline = {}
        trace.final_decision = {
            "approved_targets": [],
            "blocked_targets": ["000001"],
        }
        trace.inputs_snapshot = {}

        result = _format_trace(trace, "000001")
        assert result["verdict"] == "avoid"
        assert result["role"] == "blocked"
        assert result["fallback_used"] is True
        assert result["llm_error"] == "LLM timeout"
        assert result["evidence_refs"] == []


class TestFetchStockDecisions:
    @pytest.mark.asyncio
    async def test_returns_security_id_and_matched_traces(self) -> None:
        from services.decision_drilldown_service import fetch_stock_decisions

        # 构造 session mocks
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        # 模拟 security 行
        security = MagicMock()
        security.security_id = uuid4()
        security.name = "贵州茅台"

        # 模拟 trace 行
        trace = MagicMock()
        trace.trace_id = uuid4()
        trace.business_date = date(2026, 7, 7)
        trace.window_type = "close"
        trace.verdict = "long"
        trace.confidence = 0.85
        trace.risk_budget = "normal"
        trace.rationale = "OK"
        trace.fallback_used = False
        trace.model_version = "deepseek-chat"
        trace.market_regime = "risk_on_expansion"
        trace.llm_error = None
        trace.created_at = None
        trace.rule_baseline = {}
        trace.final_decision = {
            "approved_targets": ["600519"],
            "blocked_targets": ["000001"],
        }
        trace.inputs_snapshot = {"stock_outlook": [{"symbol": "600519", "evidence_refs": []}]}

        # 模拟 execute 返回值
        security_result = MagicMock()
        security_result.scalar_one_or_none = MagicMock(return_value=security)
        trace_result = MagicMock()
        trace_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[trace])))

        execute_results = [security_result, trace_result]
        session.execute = AsyncMock(side_effect=lambda *args, **kw: execute_results.pop(0))

        result = await fetch_stock_decisions(
            session, code="600519", lookback_days=30, limit=10,
        )

        assert result["code"] == "600519"
        assert result["security_id"] is not None
        assert result["name"] == "贵州茅台"
        assert result["matched_count"] == 1
        assert len(result["traces"]) == 1
        assert result["traces"][0]["role"] == "approved"

    @pytest.mark.asyncio
    async def test_returns_empty_traces_for_unknown_code(self) -> None:
        from services.decision_drilldown_service import fetch_stock_decisions

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)

        security_result = MagicMock()
        security_result.scalar_one_or_none = MagicMock(return_value=None)
        trace_result = MagicMock()
        trace_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))

        execute_results = [security_result, trace_result]
        session.execute = AsyncMock(side_effect=lambda *args, **kw: execute_results.pop(0))

        result = await fetch_stock_decisions(session, code="999999", limit=10)

        assert result["code"] == "999999"
        assert result["security_id"] is None
        assert result["name"] is None
        assert result["matched_count"] == 0
        assert result["traces"] == []
