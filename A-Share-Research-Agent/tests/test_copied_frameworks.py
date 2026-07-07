"""Tests covering the new LLM/prompt/data-router building blocks copied
from upstream TradingAgents / TradingAgents-CN(-lite) into the
self-contained A-Share-Research-Agent subproject.

These tests do **not** require a live LLM provider; they only verify
the in-process contracts so the migration does not regress silently.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_capabilities_table_recognises_thinking_models() -> None:
    from core.llm.capabilities import get_capabilities

    deepseek_chat = get_capabilities("deepseek-chat")
    assert deepseek_chat.preferred_structured_method == "function_calling"
    assert deepseek_chat.requires_reasoning_content_roundtrip is False

    deepseek_v4 = get_capabilities("deepseek-v4-flash")
    assert deepseek_v4.requires_reasoning_content_roundtrip is True
    assert deepseek_v4.supports_tool_choice is False

    minimax_m2 = get_capabilities("MiniMax-M2.7")
    assert minimax_m2.requires_reasoning_split is True
    assert minimax_m2.supports_tool_choice is False

    unknown = get_capabilities("some-unknown-model")
    assert unknown.preferred_structured_method == "function_calling"
    assert unknown.supports_json_mode is True


def test_normalize_message_content_handles_provider_blocks() -> None:
    from core.llm.base_client import normalize_message_content

    assert normalize_message_content("hello") == "hello"
    assert (
        normalize_message_content(
            [
                {"type": "reasoning", "text": "internal-think"},
                {"type": "text", "text": "visible"},
            ]
        )
        == "visible"
    )
    assert normalize_message_content([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"


def test_invoke_structured_or_text_falls_back() -> None:
    from core.llm import structured as structured_mod

    captured: dict[str, str] = {}

    class FailingStructured:
        async def ainvoke(self, messages):  # pragma: no cover - 显式触发
            raise RuntimeError("structured boom")

    class CapturingPlain:
        async def ainvoke(self, messages):
            captured["len"] = str(len(messages))
            return SimpleNamespace(content="plain-output")

    out = asyncio.run(
        structured_mod.invoke_structured_or_text(
            structured_llm=FailingStructured(),
            plain_llm=CapturingPlain(),
            messages=[SimpleNamespace(role="user", content="x")],
            agent_name="unit-test",
        )
    )
    assert out.content == "plain-output"
    assert captured["len"] == "1"


def test_agent_base_class_builds_envelope_without_llm_call() -> None:
    from agents.base import StructuredResearchAgent
    from schemas.agent_io import MarketRegimeOutput

    class FakeAnalyst(StructuredResearchAgent):
        agent_name = "FakeAnalyst"

        def build_prompts(self, **kwargs):  # type: ignore[override]
            return "system", "user"

        def build_narrative_summary(self, structured_data, **kwargs):  # type: ignore[override]
            return f"summary for {structured_data.market_regime}"

        def build_fallback_structured(self, *, output_schema, **_):  # type: ignore[override]
            return output_schema(
                market_regime="neutral_mixed",
                risk_mode="balanced",
                style_rotation_hint="no_bias",
                state_support_mode="none",
            )

    class StubLLM:
        async def ainvoke_structured(self, *, system_prompt, user_prompt, output_schema):
            return MarketRegimeOutput(
                market_regime="neutral_mixed",
                risk_mode="balanced",
                style_rotation_hint="no_bias",
                state_support_mode="none",
            )

    analyst = FakeAnalyst(llm_client=StubLLM())  # type: ignore[arg-type]
    env = asyncio.run(
        analyst.run_structured(
            output_schema=MarketRegimeOutput,
            market_snapshot={"dummy": True},
            rule_hits=[{"rule_code": "MKT-001", "rule_level": "warning", "message": "warn-1"}],
        )
    )
    assert env.agent_name == "FakeAnalyst"
    assert env.structured_output["market_regime"] == "neutral_mixed"
    assert env.warnings == ["warn-1"]
    assert env.evidence_refs == ["MKT-001@warning"]
    assert "summary for neutral_mixed" in env.narrative_summary


def test_agent_base_class_falls_back_when_llm_unavailable() -> None:
    from agents.base import StructuredResearchAgent
    from core.llm.client import LLMUnavailable
    from schemas.agent_io import MarketRegimeOutput

    class FakeAnalyst(StructuredResearchAgent):
        agent_name = "FakeAnalyst"

        def build_prompts(self, **kwargs):  # type: ignore[override]
            return "system", "user"

        def build_narrative_summary(self, structured_data, **kwargs):  # type: ignore[override]
            return f"summary for {structured_data.market_regime}"

        def build_fallback_structured(self, *, output_schema, market_snapshot, rule_hits, **_):  # type: ignore[override]
            return MarketRegimeOutput(
                market_regime="neutral_mixed",
                risk_mode="balanced",
                style_rotation_hint="no_bias",
                state_support_mode="none",
            )

    class UnavailableLLM:
        async def ainvoke_structured(self, *, system_prompt, user_prompt, output_schema):
            raise LLMUnavailable("no api key")

    analyst = FakeAnalyst(llm_client=UnavailableLLM())  # type: ignore[arg-type]
    env = asyncio.run(
        analyst.run_structured(
            output_schema=MarketRegimeOutput,
            market_snapshot={"dummy": True},
            rule_hits=[],
        )
    )
    assert "llm_unavailable_fallback" in env.warnings
    assert env.structured_output["market_regime"] == "neutral_mixed"


def test_data_router_categories_and_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    from data_pipeline import data_router as router_mod
    from data_pipeline.akshare_client import AkShareClient

    captured: list[str] = []

    async def fake_get_stock_list() -> str:
        captured.append("stock_list")
        return "ok"

    fake_client = AkShareClient.__new__(AkShareClient)
    fake_client.get_stock_list = fake_get_stock_list  # type: ignore[attr-defined]

    router = router_mod.AShareDataRouter(akshare_client=fake_client)

    assert "get_stock_list" in router.available_methods()
    assert router.get_category_for_method("get_stock_list") == "security_master"
    assert router.get_provider_chain("get_stock_list") == ["akshare"]

    out = asyncio.run(router.route("get_stock_list"))
    assert out == "ok"
    assert captured == ["stock_list"]


def test_factory_rejects_unknown_provider() -> None:
    from core.llm.factory import create_llm_client

    with pytest.raises(ValueError):
        create_llm_client(provider="not-a-real-provider", model_name="x")


def test_api_health_endpoint_runs_in_process() -> None:
    from fastapi.testclient import TestClient

    from apps.api.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert "A-Share" in body["service"]
    # 当 db 不可达时必须返回 degraded 而不是 5xx，体现 graceful degradation。
    if not body["db_ok"]:
        assert body["status"] == "degraded"
        assert body["db_detail"]
