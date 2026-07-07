from __future__ import annotations

from .client import OpenAICompatibleLLMClient

_OPENAI_COMPATIBLE_PROVIDERS = {
    "openai",
    "xai",
    "deepseek",
    "qwen",
    "qwen-cn",
    "glm",
    "glm-cn",
    "minimax",
    "minimax-cn",
    "ollama",
    "openrouter",
}


def create_llm_client(
    *,
    provider: str,
    model_name: str,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> OpenAICompatibleLLMClient:
    """Create an internal LLM client without importing outer-repo modules."""
    provider_lower = provider.lower()
    if provider_lower not in _OPENAI_COMPATIBLE_PROVIDERS:
        raise ValueError(f"当前项目暂不支持该 LLM provider: {provider}")

    return OpenAICompatibleLLMClient(
        provider=provider_lower,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )
