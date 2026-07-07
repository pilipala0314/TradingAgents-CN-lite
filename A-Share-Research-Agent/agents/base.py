from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from core.config import settings
from core.llm.client import LLMClient, LLMUnavailable
from schemas.agent_io import AgentOutputEnvelope

logger = logging.getLogger(__name__)


class StructuredResearchAgent(ABC):
    """Shared agent runtime copied from external agent-framework patterns."""

    agent_name: str

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client or LLMClient()

    @abstractmethod
    def build_prompts(self, **kwargs: Any) -> tuple[str, str]:
        """Return the system/user prompt pair."""

    @abstractmethod
    def build_narrative_summary(self, structured_data: Any, **kwargs: Any) -> str:
        """Render a concise human-readable summary."""

    @abstractmethod
    def build_fallback_structured(self, **kwargs: Any) -> Any:
        """子类的 deterministic 兜底：返回与目标 schema 一致的最小对象。

        子类必须根据传入的 market_snapshot / sector_snapshot / rule_hits 等
        信号直接给出结构化结论，不能依赖 LLM；不要捏造字段。
        """

    async def run_structured(self, *, output_schema: type, **kwargs: Any) -> AgentOutputEnvelope:
        logger.info("%s 开始执行结构化分析。", self.agent_name)
        system_prompt, user_prompt = self.build_prompts(**kwargs)
        try:
            structured_data = await self.llm.ainvoke_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_schema=output_schema,
            )
            logger.info("%s LLM 真实返回结构化数据。", self.agent_name)
        except LLMUnavailable as exc:
            logger.warning(
                "%s LLM 不可用，切换 deterministic 兜底：%s", self.agent_name, exc
            )
            fallback = self.build_fallback_structured(output_schema=output_schema, **kwargs)
            if fallback is None:
                raise
            structured_data = fallback
            warnings = ["llm_unavailable_fallback", *self.extract_warnings(**kwargs)]
        except Exception as exc:
            logger.exception("%s LLM 调用失败（非 unavailable）：%s", self.agent_name, exc)
            raise
        else:
            warnings = self.extract_warnings(**kwargs)

        evidence_refs = self.extract_evidence_refs(**kwargs)
        confidence = self.estimate_confidence(structured_data=structured_data, **kwargs)
        return AgentOutputEnvelope(
            agent_name=self.agent_name,
            structured_output=structured_data.model_dump(),
            narrative_summary=self.build_narrative_summary(structured_data, **kwargs),
            evidence_refs=evidence_refs,
            confidence=confidence,
            warnings=warnings,
            output_version=settings.DEFAULT_OUTPUT_VERSION,
        )

    def estimate_confidence(self, *, structured_data: Any, **kwargs: Any) -> float:
        """Allow subclasses to override confidence heuristics."""
        return 0.8

    def extract_warnings(self, **kwargs: Any) -> list[str]:
        rule_hits = kwargs.get("rule_hits") or []
        return [str(hit.get("message", "")) for hit in rule_hits if hit.get("message")]

    def extract_evidence_refs(self, **kwargs: Any) -> list[str]:
        refs: list[str] = []
        rule_hits = kwargs.get("rule_hits") or []
        for hit in rule_hits:
            code = hit.get("rule_code")
            if not code:
                continue
            level = hit.get("rule_level")
            if level:
                refs.append(f"{code}@{level}")
            else:
                refs.append(str(code))
        return refs
