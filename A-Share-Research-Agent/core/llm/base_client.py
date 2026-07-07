from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


def normalize_message_content(content: Any) -> str:
    """Normalize provider-specific message content into plain text.

    Several OpenAI-compatible providers return a list of typed blocks
    instead of a single string. We only preserve human-readable text
    fragments so downstream agents and JSON fallback logic stay stable.
    """
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        text_blocks: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    text_blocks.append(item.strip())
                continue

            if not isinstance(item, dict):
                continue

            item_type = str(item.get("type", "")).lower()
            if item_type and item_type not in {"text", "output_text"}:
                continue

            text = item.get("text")
            if isinstance(text, str) and text.strip():
                text_blocks.append(text.strip())

        return "\n".join(text_blocks).strip()

    return str(content).strip()


class BaseLLMClient(ABC):
    """Minimal reusable contract inspired by TradingAgents-CN-lite."""

    def __init__(
        self,
        *,
        provider: str,
        model_name: str,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abstractmethod
    def get_chat_model(self) -> Any:
        """Return the provider-specific LangChain chat model."""
