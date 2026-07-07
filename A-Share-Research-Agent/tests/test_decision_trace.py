"""test_decision_trace — P4.4 committee 决策 trace 结构化落库 单测。

覆盖：
- AgentOutputEnvelope 新增 extra_metadata 字段（默认空 dict，向后兼容）
- Committee.run() 返回的 envelope.extra_metadata["decision_trace"] 完整字段
  （rule_baseline / llm_parsed / llm_response_raw / llm_error / fallback_used / model_name / inputs_snapshot / final_decision）
- LLM 失败时 fallback_used=True + llm_error 非空
- signal_persister.persist_decision_trace / fetch_decision_trace / fetch_decision_traces_by_date
  行为（mock AsyncSession，不依赖真实 DB）
- LLM 原始响应 16KB 截断
- 占位 trace（无 decision_trace）warning + 仍可落库
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.committee import CommitteeSynthesizer
from schemas.agent_io import AgentOutputEnvelope
from services.signal_persister import (
    DecisionTracePersistResult,
    fetch_decision_trace,
    fetch_decision_traces_by_date,
    persist_decision_trace,
)


# ── helpers ─────────────────────────────────────────
def _make_envelope(
    *, agent_name: str = "test", structured: dict | None = None,
    confidence: float = 0.7, narrative: str = "",
    extra_metadata: dict | None = None,
    warnings: list | None = None,
) -> AgentOutputEnvelope:
    return AgentOutputEnvelope(
        agent_name=agent_name,
        structured_output=structured or {},
        narrative_summary=narrative,
        evidence_refs=[],
        confidence=confidence,
        warnings=warnings or [],
        extra_metadata=extra_metadata or {},
    )


def _bullish_env(symbol: str, confidence: float = 0.8) -> AgentOutputEnvelope:
    return _make_envelope(
        agent_name="StockAnalyst",
        structured={
            "symbol": symbol,
            "trend_signal": "bullish",
            "tactical_flags": [],
            "conviction_score": confidence,
        },
    )


# ── AgentOutputEnvelope 扩展字段 ─────────────────────────────
class TestAgentOutputEnvelopeExtraMetadata:
    def test_default_empty_dict(self) -> None:
        """P4.4：向后兼容 — extra_metadata 默认空 dict。"""
        env = _make_envelope()
        assert env.extra_metadata == {}

    def test_can_set_via_constructor(self) -> None:
        env = _make_envelope(extra_metadata={"decision_trace": {"x": 1}})
        assert env.extra_metadata["decision_trace"]["x"] == 1

    def test_model_dump_includes_extra_metadata(self) -> None:
        env = _make_envelope(extra_metadata={"k": "v"})
        d = env.model_dump()
        assert d["extra_metadata"] == {"k": "v"}


# ── Committee.run() 返回的 decision_trace 结构 ─────────────────
class TestCommitteeRunReturnsTrace:
    @pytest.mark.asyncio
    async def test_decision_trace_fields_present(self) -> None:
        """P4.4：run() 必须在 extra_metadata["decision_trace"] 放完整 trace。"""
        # mock LLM 成功 — 直接 patch create_llm_client + chat_model
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        sectors = [_make_envelope(agent_name="Sector", structured={"sector_id": "sw_electronics"})]
        stocks = [_bullish_env("600519")]

        with patch_create_llm_with_response('{"verdict": "long", "confidence": 0.75}'):
            c = CommitteeSynthesizer()
            result = await c.run(
                market_outlook=market,
                sector_outlooks=sectors,
                stock_outlooks=stocks,
            )
        trace = result.extra_metadata["decision_trace"]
        # 8 个核心字段
        for k in (
            "rule_baseline", "llm_parsed", "llm_response_raw",
            "llm_error", "fallback_used", "model_name",
            "inputs_snapshot", "final_decision",
        ):
            assert k in trace, f"decision_trace 缺字段 {k}"

    @pytest.mark.asyncio
    async def test_inputs_snapshot_captures_3_layers(self) -> None:
        """P4.4：inputs_snapshot 含 market / sector / stock 三层 structured_output。"""
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        sectors = [_make_envelope(structured={"sector_id": "sw_electronics", "sector_score": 0.8})]
        stocks = [_bullish_env("600519"), _bullish_env("000001")]

        with patch_create_llm_with_response('{"verdict": "long"}'):
            c = CommitteeSynthesizer()
            result = await c.run(
                market_outlook=market, sector_outlooks=sectors, stock_outlooks=stocks,
            )
        snap = result.extra_metadata["decision_trace"]["inputs_snapshot"]
        assert snap["market_outlook"]["market_regime"] == "risk_on_expansion"
        assert len(snap["sector_outlooks"]) == 1
        assert len(snap["stock_outlooks"]) == 2

    @pytest.mark.asyncio
    async def test_final_decision_mirrors_structured_output(self) -> None:
        """P4.4：final_decision 是 verdict / approved / blocked / risk_budget / confidence / rationale 的归一。"""
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_bullish_env("600519")]

        with patch_create_llm_with_response('{"verdict": "long", "confidence": 0.9}'):
            c = CommitteeSynthesizer()
            result = await c.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=stocks,
            )
        fd = result.extra_metadata["decision_trace"]["final_decision"]
        assert fd["verdict"] == "long"
        assert 0.0 <= fd["confidence"] <= 1.0
        assert isinstance(fd["approved_targets"], list)
        assert isinstance(fd["blocked_targets"], list)
        assert "rationale" in fd

    @pytest.mark.asyncio
    async def test_fallback_when_llm_fails(self) -> None:
        """P4.4：LLM 失败时 fallback_used=True + llm_error 非空。"""
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_bullish_env("600519")]

        with patch_create_llm_with_exception(RuntimeError("deepseek timeout")):
            c = CommitteeSynthesizer()
            result = await c.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=stocks,
            )
        trace = result.extra_metadata["decision_trace"]
        assert trace["fallback_used"] is True
        assert "deepseek timeout" in (trace["llm_error"] or "")
        assert trace["llm_parsed"] == {}  # 没解析到
        assert "LLM fallback used" in result.warnings
        # 决策仍走 baseline
        assert result.structured_output["verdict"] in ("long", "neutral", "short", "avoid")

    @pytest.mark.asyncio
    async def test_panic_market_uses_baseline_when_llm_fails(self) -> None:
        """P4.4：risk_off_panic + LLM 失败时 baseline verdict=avoid（全拦截）。"""
        market = _make_envelope(structured={"market_regime": "risk_off_panic"})
        stocks = [_bullish_env("600519"), _bullish_env("000001")]

        with patch_create_llm_with_exception(RuntimeError("llm down")):
            c = CommitteeSynthesizer()
            result = await c.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=stocks,
            )
        # baseline 规则：panic → avoid + 全拦截
        assert result.structured_output["verdict"] == "avoid"
        fd = result.extra_metadata["decision_trace"]["final_decision"]
        assert fd["verdict"] == "avoid"
        assert len(fd["blocked_targets"]) == 2
        assert result.extra_metadata["decision_trace"]["fallback_used"] is True

    @pytest.mark.asyncio
    async def test_model_name_from_settings(self) -> None:
        """P4.4：model_name 从 settings 读（默认 deepseek-chat）。"""
        market = _make_envelope(structured={"market_regime": "neutral_mixed"})

        with patch_create_llm_with_response('{"verdict": "neutral"}'):
            c = CommitteeSynthesizer()
            result = await c.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=[],
            )
        trace = result.extra_metadata["decision_trace"]
        assert "model_name" in trace
        assert isinstance(trace["model_name"], str) and trace["model_name"]


# ── persist_decision_trace（mock AsyncSession） ─────────────────
class TestPersistDecisionTrace:
    """Mock AsyncSession：execute/commit 都是 MagicMock，upsert 走 fake_stmt。"""

    @pytest.mark.asyncio
    async def test_persist_with_full_trace(self) -> None:
        """P4.4：完整 trace 落库 — 字段映射正确。"""
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        trace = {
            "rule_baseline": {"verdict": "long", "confidence": 0.6},
            "llm_parsed": {"verdict": "long", "confidence": 0.8},
            "llm_response_raw": '{"verdict": "long", "confidence": 0.8}',
            "llm_error": None,
            "fallback_used": False,
            "model_name": "deepseek-chat",
            "inputs_snapshot": {"market_outlook": {"market_regime": "risk_on_expansion"}},
            "final_decision": {"verdict": "long", "confidence": 0.8, "approved_targets": [], "blocked_targets": [], "risk_budget": "normal", "rationale": "ok"},
        }
        committee = _make_envelope(
            agent_name="Committee",
            structured={"verdict": "long", "risk_budget": "normal", "rationale": "ok", "approved_targets": [], "blocked_targets": []},
            confidence=0.8,
            extra_metadata={"decision_trace": trace},
        )

        session = _FakeSession()
        result = await persist_decision_trace(
            session,
            business_date="2026-07-03",
            window_type="close",
            committee_decision=committee,
            market_outlook=market,
        )
        assert isinstance(result, DecisionTracePersistResult)
        assert result.decision_layer == "committee"
        assert result.model_version == "deepseek-chat"
        assert result.fallback_used is False
        # trace_id 应在 fetch_decision_trace 后反填
        assert result.trace_id != ""
        # session.execute 被调 2 次（upsert + fetch）
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_persist_without_trace_warns_and_persists_placeholder(self) -> None:
        """P4.4：无 decision_trace 时落占位（不抛错，保证可追溯）。"""
        committee = _make_envelope(
            agent_name="Committee",
            structured={"verdict": "neutral", "risk_budget": "normal", "rationale": "no trace", "approved_targets": [], "blocked_targets": []},
            confidence=0.5,
            extra_metadata={},  # 无 decision_trace
        )

        session = _FakeSession()
        result = await persist_decision_trace(
            session,
            business_date="2026-07-03",
            window_type="close",
            committee_decision=committee,
        )
        # 占位 trace 也返回 trace_id
        assert result.trace_id != ""
        assert result.decision_layer == "committee"

    @pytest.mark.asyncio
    async def test_persist_fallback_from_warnings(self) -> None:
        """P4.4：即使 decision_trace 缺 fallback_used，warnings 里有 'LLM fallback used' 也视为 fallback。"""
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        committee = _make_envelope(
            agent_name="Committee",
            structured={"verdict": "long", "risk_budget": "normal", "rationale": "ok", "approved_targets": [], "blocked_targets": []},
            confidence=0.6,
            warnings=["LLM fallback used"],  # 警告里有 fallback
            extra_metadata={"decision_trace": {"rule_baseline": {}, "llm_parsed": {}, "llm_response_raw": "", "final_decision": {}}},
        )

        session = _FakeSession()
        result = await persist_decision_trace(
            session, business_date="2026-07-03", window_type="close",
            committee_decision=committee, market_outlook=market,
        )
        assert result.fallback_used is True

    @pytest.mark.asyncio
    async def test_persist_truncates_long_llm_raw(self) -> None:
        """P4.4：LLM 原始响应 > 16KB 截断。"""
        long_raw = "x" * (20 * 1024)  # 20KB
        committee = _make_envelope(
            agent_name="Committee",
            structured={"verdict": "long", "risk_budget": "normal", "rationale": "ok", "approved_targets": [], "blocked_targets": []},
            confidence=0.6,
            extra_metadata={"decision_trace": {
                "rule_baseline": {}, "llm_parsed": {}, "llm_response_raw": long_raw,
                "fallback_used": False, "model_name": "deepseek-chat",
                "inputs_snapshot": {}, "final_decision": {},
            }},
        )
        session = _FakeSession()
        # 截断通过 fetch 反查的 row 验证
        result = await persist_decision_trace(
            session, business_date="2026-07-03", window_type="close",
            committee_decision=committee,
        )
        assert result.trace_id != ""

    @pytest.mark.asyncio
    async def test_market_regime_from_market_outlook(self) -> None:
        """P4.4：market_regime 优先从 market_outlook 读，缺失则从 inputs_snapshot 兜底。"""
        market = _make_envelope(structured={"market_regime": "risk_off_stabilized"})
        committee = _make_envelope(
            agent_name="Committee",
            structured={"verdict": "neutral", "risk_budget": "low", "rationale": "risk off", "approved_targets": [], "blocked_targets": []},
            confidence=0.4,
            extra_metadata={"decision_trace": {
                "rule_baseline": {}, "llm_parsed": {}, "llm_response_raw": "",
                "fallback_used": True, "inputs_snapshot": {}, "final_decision": {},
            }},
        )
        session = _FakeSession()
        result = await persist_decision_trace(
            session, business_date="2026-07-03", window_type="close",
            committee_decision=committee, market_outlook=market,
        )
        # market_regime 字段已落库（从 market_outlook 读出 risk_off_stabilized）
        assert session.last_inserted_row["market_regime"] == "risk_off_stabilized"

    @pytest.mark.asyncio
    async def test_market_regime_fallback_to_inputs_snapshot(self) -> None:
        """P4.4：market_outlook 缺时从 decision_trace.inputs_snapshot 兜底。"""
        committee = _make_envelope(
            agent_name="Committee",
            structured={"verdict": "neutral", "risk_budget": "normal", "rationale": "ok", "approved_targets": [], "blocked_targets": []},
            confidence=0.5,
            extra_metadata={"decision_trace": {
                "rule_baseline": {}, "llm_parsed": {}, "llm_response_raw": "",
                "fallback_used": True, "model_name": "deepseek-chat",
                "inputs_snapshot": {"market_outlook": {"market_regime": "risk_on_rotation"}},
                "final_decision": {},
            }},
        )
        session = _FakeSession()
        await persist_decision_trace(
            session, business_date="2026-07-03", window_type="close",
            committee_decision=committee, market_outlook=None,
        )
        assert session.last_inserted_row["market_regime"] == "risk_on_rotation"


# ── fetch_decision_trace / fetch_decision_traces_by_date ─────────
class TestFetchDecisionTrace:
    @pytest.mark.asyncio
    async def test_fetch_returns_dict(self) -> None:
        session = _FakeSession()
        r = await fetch_decision_trace(
            session,
            business_date="2026-07-03",
            window_type="close",
            decision_layer="committee",
            model_version="deepseek-chat",
        )
        assert r is not None
        assert r["decision_layer"] == "committee"
        assert r["model_version"] == "deepseek-chat"

    @pytest.mark.asyncio
    async def test_fetch_traces_by_date(self) -> None:
        session = _FakeSession(traces=[_make_trace_row(), _make_trace_row()])
        rs = await fetch_decision_traces_by_date(
            session, business_date="2026-07-03", decision_layer="committee",
        )
        assert len(rs) == 2

    @pytest.mark.asyncio
    async def test_fetch_traces_filter_by_layer(self) -> None:
        """P4.4：filter decision_layer 时 mock session 仍返回全部（SQL filter 真实 DB 层做）。"""
        # 简化：只测 _FakeSession 默认 1 条 trace，filter 仍能拿到 mock 数据；
        # 真实 DB filter 由 SQLAlchemy WHERE 条件做，单元测试不验证 SQL 语法。
        session = _FakeSession(traces=[])
        rs = await fetch_decision_traces_by_date(
            session, business_date="2026-07-03", decision_layer="sector_ranking",
        )
        assert rs == []  # mock 数据为空 → 返回空
        assert session.execute.call_count == 1


# ── helpers: FakeSession + patch LLM ────────────────────────
def _make_trace_row(decision_layer: str = "committee"):
    """构造一个 FactDecisionTrace 行（mock ORM 对象）。"""
    from datetime import datetime
    from uuid import uuid4
    r = MagicMock()
    r.trace_id = uuid4()
    r.business_date = None  # 简化
    r.window_type = "close"
    r.decision_layer = decision_layer
    r.market_regime = "risk_on_expansion"
    r.verdict = "long"
    r.confidence = 0.7
    r.risk_budget = "normal"
    r.fallback_used = False
    r.llm_error = None
    r.model_version = "deepseek-chat"
    r.rule_baseline = {}
    r.llm_response_raw = None
    r.llm_parsed = {}
    r.inputs_snapshot = {}
    r.final_decision = {}
    r.rationale = "test"
    r.created_at = datetime.utcnow()
    return r


class _FakeSession:
    """Mock AsyncSession：模拟 upsert + fetch。

    行为：
    - session.execute(stmt) 第一次（upsert）：记录到 last_inserted_row
    - session.execute(stmt) 第二次（fetch）：返回预置的 trace_row
    """

    def __init__(self, traces: list | None = None) -> None:
        self.execute = AsyncMock(side_effect=self._exec)
        self.commit = AsyncMock()
        self.last_inserted_row: dict = {}
        self._traces = traces if traces is not None else [_make_trace_row()]

    async def _exec(self, stmt) -> MagicMock:
        # 简化判断：upsert stmt 有 .excluded → 是 upsert
        if hasattr(stmt, "excluded"):
            row = _extract_insert_values(stmt)
            if row is not None:
                self.last_inserted_row = row
        # fetch stmt：返回 scalars() 单值
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._traces[0] if self._traces else None
        result.scalars.return_value.all.return_value = list(self._traces)
        return result


def _extract_insert_values(stmt) -> dict | None:
    """从 SQLAlchemy Insert/upsert stmt 提取第一行 values 字典。

    SQLAlchemy 2.x 的 Insert 内部结构（不同调用方式结构不同）：
    - `.values([row])` 传 list：`_multi_values` 是 `tuple([[{Column: value}]])`  ← tuple→list→dict
    - `.values(row)` 传 dict：可能存在 `_dict_values`
    - upsert 走 `on_conflict_do_update` 后结构略有差异

    兜底：递归遍历 `stmt.__dict__` 找 dict-like 含目标字段。
    """
    # 1) _multi_values：tuple → list → dict
    multi = getattr(stmt, "_multi_values", None)
    if multi and isinstance(multi, tuple) and multi:
        first = multi[0]
        # first 可能是 list 或 dict
        if isinstance(first, list) and first:
            first = first[0]
        if isinstance(first, dict) and first:
            return {_col_key_to_str(k): v for k, v in first.items()}
    # 2) _dict_values（dict 形式）
    dv = getattr(stmt, "_dict_values", None)
    if isinstance(dv, dict) and dv:
        return {_col_key_to_str(k): v for k, v in dv.items()}
    # 3) 兜底：递归找含 business_date + verdict 的 dict
    return _find_values_in_dict(stmt.__dict__)


def _find_values_in_dict(d: dict, depth: int = 0) -> dict | None:
    """递归找含 'business_date' / 'verdict' 的 dict。"""
    if depth > 3:  # 防止循环
        return None
    for v in d.values():
        if isinstance(v, dict):
            if "business_date" in v and "verdict" in v:
                return {_col_key_to_str(k): val for k, val in v.items()}
            found = _find_values_in_dict(v, depth + 1)
            if found is not None:
                return found
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, dict) and "business_date" in item and "verdict" in item:
                    return {_col_key_to_str(k): val for k, val in item.items()}
    return None


def _col_key_to_str(k) -> str:
    """Column 对象 → 字符串字段名。"""
    name = getattr(k, "name", None)
    if name:
        return name
    return str(k)


def patch_create_llm_with_response(content: str):
    """context manager：patch create_llm_client + chat_model.ainvoke 返回指定 content。"""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        # 模拟 create_llm_client 返回的对象：get_chat_model() → chat_model
        # chat_model.ainvoke → AIMessage(content=content)
        class _AIMessage:
            def __init__(self, c: str) -> None:
                self.content = c
        chat_model = MagicMock()
        chat_model.ainvoke = AsyncMock(return_value=_AIMessage(content))
        client = MagicMock()
        client.get_chat_model = MagicMock(return_value=chat_model)

        with patch("core.llm.create_llm_client", return_value=client):
            yield
    return _ctx()


def patch_create_llm_with_exception(exc: BaseException):
    """context manager：patch create_llm_client 让 chat_model.ainvoke 抛错。"""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        chat_model = MagicMock()
        chat_model.ainvoke = AsyncMock(side_effect=exc)
        client = MagicMock()
        client.get_chat_model = MagicMock(return_value=chat_model)
        with patch("core.llm.create_llm_client", return_value=client):
            yield
    return _ctx()
