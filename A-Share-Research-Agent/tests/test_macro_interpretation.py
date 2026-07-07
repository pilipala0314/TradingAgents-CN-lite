"""test_macro_interpretation — P0-T6 宏观 LLM 解读单测。

覆盖：
- LLM_API_KEY 未配置 → llm_status="unconfigured", interpretation=None
- LLM 不可用 → llm_status="unavailable", interpretation=None
- LLM 调用成功 → 返回结构化 interpretation
- 失败语义：不静默降级、占位提示由前端负责
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.macro_interpretation_service import (
    _build_macro_snapshot_payload,
    interpret_macro_snapshot,
)

logger = logging.getLogger(__name__)


class FakeMacroSnapshot:
    def __init__(self) -> None:
        self.pmi = SimpleSnap(available=True, value=50.2, as_of="2026-07-07", context="制造业景气")
        self.cpi = SimpleSnap(available=True, value=2.1, as_of="2026-07-07", context="温和通胀")
        self.m2 = SimpleSnap(available=True, value=9.5, as_of="2026-07-07", context="稳健")
        self.bond_yield = SimpleSnap(available=True, value=1.5, as_of="2026-07-07", context="利差舒适")
        self.regime_hint = "neutral_mixed"


class SimpleSnap:
    def __init__(self, *, available: bool, value: Any, as_of: str | None, context: str = "") -> None:
        self.available = available
        self.value = value
        self.as_of = as_of
        self.context = context
        self.raw = {}


class TestBuildMacroSnapshotPayload:
    def test_builds_payload(self) -> None:
        snap = FakeMacroSnapshot()
        payload = _build_macro_snapshot_payload(snap)
        assert "pmi" in payload
        assert payload["pmi"]["value"] == 50.2
        assert "cpi" in payload
        assert "m2" in payload
        assert "bond_yield" in payload
        assert "regime_hint" in payload


class TestInterpretMacroSnapshot:
    @pytest.mark.asyncio
    async def test_returns_unconfigured_when_no_api_key(self) -> None:
        snap = FakeMacroSnapshot()
        with patch("services.macro_interpretation_service.settings") as mock_settings:
            mock_settings.LLM_API_KEY = ""
            result = await interpret_macro_snapshot(snapshot=snap)
        assert result["llm_status"] == "unconfigured"
        assert result["interpretation"] is None
        assert "未配置" in result["llm_error"]

    @pytest.mark.asyncio
    async def test_returns_unavailable_when_llm_raises(self) -> None:
        snap = FakeMacroSnapshot()

        async def _raise_timeout(*_args, **_kwargs):
            raise RuntimeError("network timeout")

        fake_structured = MagicMock()
        fake_structured.ainvoke = _raise_timeout
        fake_plain = MagicMock()
        # 降级路径也必须 raise（模拟普通文本也失败）
        fake_plain.ainvoke = _raise_timeout

        with patch("services.macro_interpretation_service.settings") as mock_settings, \
             patch("services.macro_interpretation_service.create_llm_client") as mock_factory, \
             patch("services.macro_interpretation_service.bind_structured_output") as mock_bind:
            mock_settings.LLM_API_KEY = "test-key"
            mock_settings.LLM_PROVIDER = "deepseek"
            mock_settings.LLM_MODEL = "deepseek-chat"
            mock_factory.return_value.get_langchain_llm = MagicMock(return_value=fake_plain)
            mock_bind.return_value = fake_structured

            result = await interpret_macro_snapshot(snapshot=snap)

        assert result["llm_status"] == "error"
        assert result["interpretation"] is None
        assert "network timeout" in result["llm_error"]

    @pytest.mark.asyncio
    async def test_returns_unsupported_when_structured_bind_fails(self) -> None:
        snap = FakeMacroSnapshot()

        fake_plain = MagicMock()

        with patch("services.macro_interpretation_service.settings") as mock_settings, \
             patch("services.macro_interpretation_service.create_llm_client") as mock_factory, \
             patch("services.macro_interpretation_service.bind_structured_output", return_value=None) as mock_bind:
            mock_settings.LLM_API_KEY = "test-key"
            mock_settings.LLM_PROVIDER = "deepseek"
            mock_settings.LLM_MODEL = "deepseek-chat"
            mock_factory.return_value.get_langchain_llm = MagicMock(return_value=fake_plain)

            result = await interpret_macro_snapshot(snapshot=snap)

        assert result["llm_status"] == "unsupported"
        assert result["interpretation"] is None
        assert "不支持" in result["llm_error"]

    @pytest.mark.asyncio
    async def test_returns_interpretation_on_success(self) -> None:
        from schemas.agent_io import MacroInterpretationOutput

        snap = FakeMacroSnapshot()

        interpretation_obj = MacroInterpretationOutput(
            overall_stance="cautious",
            summary="当前宏观处于谨慎区间，PMI 扩张但 CPI 温和。",
            pmi_view="PMI 50.2，重回扩张。",
            inflation_view="CPI 同比 2.1%，温和。",
            liquidity_view="M2 同比 9.5%，稳健。",
            rate_spread_view="中美利差 1.5%，舒适。",
            confidence=0.82,
            caveats=["数据源单点"],
        )
        fake_structured = MagicMock()
        fake_structured.ainvoke = AsyncMock(return_value=interpretation_obj)
        fake_plain = MagicMock()

        with patch("services.macro_interpretation_service.settings") as mock_settings, \
             patch("services.macro_interpretation_service.create_llm_client") as mock_factory, \
             patch("services.macro_interpretation_service.bind_structured_output") as mock_bind:
            mock_settings.LLM_API_KEY = "test-key"
            mock_settings.LLM_PROVIDER = "deepseek"
            mock_settings.LLM_MODEL = "deepseek-chat"
            mock_factory.return_value.get_langchain_llm = MagicMock(return_value=fake_plain)
            mock_bind.return_value = fake_structured

            result = await interpret_macro_snapshot(snapshot=snap)

        assert result["llm_status"] == "ok"
        assert result["interpretation"] is not None
        assert result["interpretation"]["overall_stance"] == "cautious"
        assert result["interpretation"]["confidence"] == 0.82
