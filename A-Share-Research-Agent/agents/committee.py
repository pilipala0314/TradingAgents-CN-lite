"""CommitteeSynthesizer — 最终准入/排序者。

按 memory 中"同层级只能有一个最终命名者和一个最终准入/排序者"原则：
- Naming       在 SectorRankingSynthesizer 完成（板块主线的最终命名）
- 准入/排序    在本类完成（market/sector/stock 三层的最终拍板）

输入: market_outlook + sector_outlooks + stock_outlooks
输出: AgentOutputEnvelope{
    structured_output: {
        "verdict":         "long" | "neutral" | "short" | "avoid",
        "approved_targets": [symbol, ...],
        "blocked_targets":  [symbol, ...],
        "risk_budget":     "low" | "normal" | "high",
    }
}
"""
from __future__ import annotations

import logging
from typing import Any

from schemas.agent_io import AgentOutputEnvelope

logger = logging.getLogger(__name__)


class CommitteeSynthesizer:
    """投研委员会 — market + sector + stock 三层投票 + 风控硬约束。"""

    def __init__(self) -> None:
        self.system_prompt = (
            "你是 A 股投研委员会主席。你的职责是综合市场、板块、个股三层判断，"
            "给出最终准入/排序决策。你**不能**改写上游已发生的事实（如板块命名、个股战术标签），"
            "只负责投票、排序、风控。下游报告会按你的 verdict 渲染。\n\n"
            "决策规则：\n"
            "1) market_regime=risk_off_panic  → 全市场 verdict=avoid, confidence>0.8\n"
            "2) market_regime=risk_off_stabilized → verdict=neutral, 限制 confidence<=0.5\n"
            "3) 个股 stock_outlook.trend=bullish AND market=risk_on_* "
            "AND sector_stage∈{confirmed,strengthening} → 准入\n"
            "4) 个股 stock_outlook.tactical_tags 含 avoid/hard_block → 一律 blocked_targets\n"
            "5) approved_targets 最多 5 只，按 conviction（confidence×个股权重）排序\n\n"
            "严格以 JSON 输出 {verdict, confidence, approved_targets, blocked_targets, risk_budget, rationale}。"
        )

    async def run(
        self,
        *,
        market_outlook: AgentOutputEnvelope,
        sector_outlooks: list[AgentOutputEnvelope],
        stock_outlooks: list[AgentOutputEnvelope],
    ) -> AgentOutputEnvelope:
        # 1) 规则引擎先出 baseline（不靠 LLM）
        baseline = self._rule_based_decision(market_outlook, sector_outlooks, stock_outlooks)

        # 2) LLM 在 baseline 上做综合判断
        from core.config import settings
        from core.llm import create_llm_client  # 延迟导入

        fallback_used = False
        llm_error: str | None = None
        parsed: dict[str, Any] = {}
        llm_response_raw: str = ""
        user_payload: str = ""
        model_name = getattr(settings, "LLM_MODEL_NAME", "deepseek-chat")
        try:
            client = create_llm_client(
                provider=getattr(settings, "LLM_PROVIDER", "deepseek"),
                model_name=model_name,
                base_url=getattr(settings, "LLM_API_BASE", None),
                api_key=getattr(settings, "LLM_API_KEY", None),
                temperature=0.2,
            )
            user_payload = self._build_llm_payload(
                market_outlook, sector_outlooks, stock_outlooks, baseline
            )
            # OpenAICompatibleLLMClient 暴露 get_chat_model() -> LangChain BaseChatModel
            chat_model = client.get_chat_model()
            response = await chat_model.ainvoke(
                [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_payload},
                ]
            )
            llm_response_raw = getattr(response, "content", "") or str(response)
            parsed = self._parse_response(llm_response_raw)
        except Exception as exc:
            logger.warning("Committee LLM 不可用/失败，使用规则 baseline: %s", exc)
            fallback_used = True
            llm_error = f"{type(exc).__name__}: {exc}"

        structured: dict[str, Any] = {
            "verdict": parsed.get("verdict") or baseline["verdict"],
            "approved_targets": parsed.get("approved_targets") or baseline["approved_targets"],
            "blocked_targets": parsed.get("blocked_targets") or baseline["blocked_targets"],
            "risk_budget": parsed.get("risk_budget") or baseline["risk_budget"],
            "rationale": parsed.get("rationale") or baseline["rationale"],
        }
        confidence = float(parsed.get("confidence") or baseline["confidence"])
        narrative = baseline["rationale"]
        if fallback_used:
            narrative = f"⚠️ 本次决策为规则 baseline（LLM 不可用或推理失败）。\n{narrative}"

        # P4.4 — 决策 trace：把规则 baseline / LLM 原始响应 / 解析结果 / 异常 / 最终决策打包到
        # extra_metadata["decision_trace"]，由 signal_persister.persist_decision_trace() 落库。
        decision_trace: dict[str, Any] = {
            "rule_baseline": baseline,
            "llm_parsed": parsed,
            "llm_response_raw": llm_response_raw,
            "llm_user_payload_preview": user_payload[:2000] if user_payload else "",
            "llm_error": llm_error,
            "fallback_used": fallback_used,
            "model_name": model_name,
            "inputs_snapshot": {
                "market_outlook": market_outlook.structured_output,
                "sector_outlooks": [s.structured_output for s in sector_outlooks],
                "stock_outlooks": [s.structured_output for s in stock_outlooks],
            },
            "final_decision": {
                "verdict": structured["verdict"],
                "approved_targets": structured["approved_targets"],
                "blocked_targets": structured["blocked_targets"],
                "risk_budget": structured["risk_budget"],
                "confidence": confidence,
                "rationale": structured["rationale"],
            },
        }

        return AgentOutputEnvelope(
            agent_name="CommitteeSynthesizer",
            structured_output=structured,
            narrative_summary=narrative,
            evidence_refs=[],
            confidence=max(0.0, min(1.0, confidence)),
            warnings=["LLM fallback used"] if fallback_used else [],
            extra_metadata={"decision_trace": decision_trace},
        )

    # ── 规则 baseline ──────────────────────────────────────────────
    def _rule_based_decision(
        self,
        market_outlook: AgentOutputEnvelope,
        sector_outlooks: list[AgentOutputEnvelope],
        stock_outlooks: list[AgentOutputEnvelope],
    ) -> dict[str, Any]:
        market_regime = (
            market_outlook.structured_output or {}
        ).get("market_regime", "neutral_mixed")
        if market_regime == "risk_off_panic":
            return {
                "verdict": "avoid",
                "confidence": 0.85,
                "rationale": "市场极端恐慌，禁止任何新增敞口。",
                "approved_targets": [],
                "blocked_targets": [
                    s.structured_output.get("symbol", "?") for s in stock_outlooks
                ],
                "risk_budget": "low",
            }
        approved: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for env in stock_outlooks:
            so = env.structured_output or {}
            symbol = so.get("symbol") or "?"
            tags = so.get("tactical_flags") or []
            if any(t in tags for t in ("avoid", "hard_block")):
                blocked.append({"symbol": symbol, "reason": "tactical=avoid"})
                continue
            trend = so.get("trend_signal") or so.get("trend") or "neutral"
            if trend in ("bullish",) and market_regime in (
                "risk_on_expansion",
                "risk_on_rotation",
            ):
                approved.append(
                    {
                        "symbol": symbol,
                        "trend": trend,
                        "confidence": float(so.get("confidence", so.get("conviction_score", 0.5))),
                    }
                )
        approved_sorted = sorted(approved, key=lambda x: -x["confidence"])[:5]
        return {
            "verdict": "long" if approved_sorted else "neutral",
            "confidence": 0.6 if approved_sorted else 0.4,
            "rationale": f"市场={market_regime}；准入 {len(approved_sorted)} 只，拦截 {len(blocked)} 只。",
            "approved_targets": approved_sorted,
            "blocked_targets": blocked,
            "risk_budget": "normal",
        }

    def _build_llm_payload(
        self,
        market_outlook: AgentOutputEnvelope,
        sector_outlooks: list[AgentOutputEnvelope],
        stock_outlooks: list[AgentOutputEnvelope],
        baseline: dict[str, Any],
    ) -> str:
        import json

        return json.dumps(
            {
                "market_outlook": market_outlook.structured_output,
                "sector_outlooks": [s.structured_output for s in sector_outlooks],
                "stock_outlooks": [s.structured_output for s in stock_outlooks],
                "rule_baseline": baseline,
                "instruction": (
                    "请综合以上三层判断，在 rule_baseline 之上做最终决策。"
                    "verdict ∈ long|neutral|short|avoid；approved_targets 限 ≤5 只，按 conviction 排序；"
                    "blocked_targets 给拦截原因。严格以 JSON 输出。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

    def _parse_response(self, content: str) -> dict[str, Any]:
        import json
        import re

        if not content:
            logger.warning("committee _parse_response: LLM 返回空内容")
            return {}
        # 尝试直接 parse
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.debug("committee _parse_response: 直接 JSON parse 失败: %s", exc)
        # 尝试抽 ```json ... ``` 块
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "committee _parse_response: 抽取 ```json``` 块后 parse 失败: %s; content_head=%r",
                    exc, content[:200],
                )
        logger.error(
            "committee _parse_response: LLM 输出无法解析为 JSON, content_head=%r",
            content[:200],
        )
        return {}
