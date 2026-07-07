import json
import logging
import os
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from core.config import settings

from .api_key_env import get_api_key_env
from .base_client import BaseLLMClient, normalize_message_content
from .capabilities import get_capabilities
from .structured import bind_structured_output, invoke_structured_or_text

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """LLM 不可用时显式抛出，方便上层 Agent 切换 deterministic 兜底。"""


_PROVIDER_BASE_URL = {
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "qwen-cn": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "glm": "https://api.z.ai/api/paas/v4/",
    "glm-cn": "https://open.bigmodel.cn/api/paas/v4/",
    "minimax": "https://api.minimax.io/v1",
    "minimax-cn": "https://api.minimaxi.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}


def _input_to_messages(input_: Any) -> list[Any]:
    if isinstance(input_, list):
        return input_
    if hasattr(input_, "to_messages"):
        return input_.to_messages()
    return []


class NormalizedChatOpenAI(ChatOpenAI):
    """Capability-aware OpenAI-compatible chat model wrapper."""

    def invoke(self, input, config=None, **kwargs):
        response = super().invoke(input, config=config, **kwargs)
        response.content = normalize_message_content(response.content)
        return response

    def with_structured_output(self, schema, *, method=None, **kwargs):
        caps = get_capabilities(self.model_name)
        structured_method = method or caps.preferred_structured_method
        if structured_method == "none":
            raise NotImplementedError(f"{self.model_name} 不支持结构化输出")
        if structured_method == "function_calling" and not caps.supports_tool_choice:
            kwargs.setdefault("tool_choice", None)
        return super().with_structured_output(schema, method=structured_method, **kwargs)


class DeepSeekChatOpenAI(NormalizedChatOpenAI):
    """DeepSeek reasoning models require reasoning_content round-trip."""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        outgoing = payload.get("messages", [])
        message_list = _input_to_messages(input_)
        for message_dict, message in zip(outgoing, message_list, strict=False):
            if not isinstance(message, AIMessage):
                continue
            reasoning = message.additional_kwargs.get("reasoning_content")
            if reasoning is not None:
                message_dict["reasoning_content"] = reasoning
        return payload

    def _create_chat_result(self, response, generation_info=None):
        chat_result = super()._create_chat_result(response, generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(chat_result.generations, response_dict.get("choices", []), strict=False):
            reasoning = choice.get("message", {}).get("reasoning_content")
            if reasoning is not None:
                generation.message.additional_kwargs["reasoning_content"] = reasoning
        return chat_result


class MinimaxChatOpenAI(NormalizedChatOpenAI):
    """MiniMax reasoning models need reasoning_split to keep content clean."""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if get_capabilities(self.model_name).requires_reasoning_split:
            payload.setdefault("reasoning_split", True)
        return payload


class OpenAICompatibleLLMClient(BaseLLMClient):
    """Provider-aware chat model builder copied into the isolated subproject."""

    def get_chat_model(self) -> BaseChatModel:
        llm_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.base_url:
            llm_kwargs["base_url"] = self.base_url
        if self.api_key:
            llm_kwargs["api_key"] = self.api_key

        # 仅当 provider == 'openai' 且没有自定义 base_url 时，才走 OpenAI Responses API。
        # 任何挂 OpenAI 兼容 base_url（DeepSeek / Qwen / MiniMax 等）都用 Chat Completions 接口。
        if self.provider == "openai" and not self.base_url:
            llm_kwargs["use_responses_api"] = True

        if self.provider == "deepseek":
            chat_cls: type[BaseChatModel] = DeepSeekChatOpenAI
        elif self.provider in {"minimax", "minimax-cn"}:
            chat_cls = MinimaxChatOpenAI
        else:
            chat_cls = NormalizedChatOpenAI

        return chat_cls(**llm_kwargs)


class LLMClient:
    """统一 LLM 客户端，内嵌了 CN-lite 的 provider/capability 设计。"""

    def __init__(
        self,
        model_name: str | None = None,
        temperature: float = 0.1,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.provider = (provider or settings.LLM_PROVIDER or "deepseek").lower()
        self.model_name = (
            model_name
            or settings.LLM_MODEL_NAME
            or settings.DEEPSEEK_MODEL_NAME
            or "deepseek-chat"
        )
        resolved_api_key = self._resolve_api_key(api_key)
        resolved_base_url = self._resolve_base_url(base_url)

        self._client = OpenAICompatibleLLMClient(
            provider=self.provider,
            model_name=self.model_name,
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            temperature=temperature,
            max_tokens=4096,
        )
        if self._client.api_key:
            # 只有真 key 时才构造 SDK 客户端；否则只保留元数据，调用即抛 LLMUnavailable。
            self.chat_model: BaseChatModel | None = self._client.get_chat_model()
            logger.info(
                "LLMClient ready: provider=%s model=%s base=%s key_tail=%s",
                self.provider,
                self.model_name,
                resolved_base_url,
                self._client.api_key[-4:] if self._client.api_key else "",
            )
        else:
            self.chat_model = None
            logger.info(
                "LLMClient 未配置有效 api_key（provider=%s model=%s），"
                "调用将直接抛 LLMUnavailable 由 Agent 走 deterministic 兜底。",
                self.provider,
                self.model_name,
            )

    def _resolve_api_key(self, api_key: str | None) -> str | None:
        # 优先级：显式传参 > LLM_API_KEY > {PROVIDER}_API_KEY > 环境变量
        # 把空字符串、纯占位符都视为未配置。
        candidates: list[str | None] = [
            api_key,
            settings.LLM_API_KEY if settings.LLM_API_KEY else None,
            settings.DEEPSEEK_API_KEY
            if self.provider == "deepseek" and settings.DEEPSEEK_API_KEY
            else None,
        ]
        for candidate in candidates:
            if candidate and self._is_real_api_key(candidate):
                return candidate
        env_var = get_api_key_env(self.provider)
        if env_var:
            env_value = os.environ.get(env_var)
            if env_value and self._is_real_api_key(env_value):
                return env_value
        return None

    @staticmethod
    def _is_real_api_key(value: str) -> bool:
        if not value:
            return False
        normalized = value.strip().lower()
        if normalized in {"sk-placeholder", "placeholder", "your-api-key", "changeme"}:
            return False
        # 真 key 至少 16 位
        return len(value.strip()) >= 16

    def _resolve_base_url(self, base_url: str | None) -> str | None:
        if base_url:
            return base_url
        if settings.LLM_API_BASE:
            return settings.LLM_API_BASE
        if self.provider == "deepseek" and settings.DEEPSEEK_API_BASE:
            return settings.DEEPSEEK_API_BASE
        if self.provider == "ollama":
            return os.environ.get("OLLAMA_BASE_URL", _PROVIDER_BASE_URL["ollama"])
        return _PROVIDER_BASE_URL.get(self.provider)

    async def ainvoke_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[T],
    ) -> T:
        if self.chat_model is None:
            raise LLMUnavailable(
                f"LLM 未配置 api_key，provider={self.provider} model={self.model_name}"
            )
        logger.debug(
            "LLM 请求: provider=%s model=%s schema=%s",
            self.provider,
            self.model_name,
            output_schema.__name__,
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        structured_llm = bind_structured_output(
            self.chat_model,
            output_schema,
            agent_name=output_schema.__name__,
        )
        response = await invoke_structured_or_text(
            structured_llm=structured_llm,
            plain_llm=self.chat_model,
            messages=messages,
            agent_name=output_schema.__name__,
        )
        if isinstance(response, output_schema):
            logger.debug("LLM 结构化输出成功解析为 %s", output_schema.__name__)
            return response

        return await self._fallback_json_parsing(messages, output_schema, fallback_response=response)

    async def ainvoke_text(self, system_prompt: str, user_prompt: str) -> str:
        if self.chat_model is None:
            raise LLMUnavailable(
                f"LLM 未配置 api_key，provider={self.provider} model={self.model_name}"
            )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        response: AIMessage = await self.chat_model.ainvoke(messages)
        return self._normalize_content(response.content)

    async def _fallback_json_parsing(
        self,
        messages: list[Any],
        output_schema: type[T],
        fallback_response: Any | None = None,
    ) -> T:
        logger.warning("尝试退级 JSON 解析...")
        if fallback_response is not None:
            content = self._normalize_response_content(fallback_response)
            parsed = self._try_validate_json_text(content, output_schema)
            if parsed is not None:
                return parsed

        fallback_messages = [
            *messages,
            HumanMessage(
                content=(
                    "请严格输出符合以下 JSON Schema 的 JSON，不要包含 Markdown 代码块，也不要补充解释：\n"
                    f"{json.dumps(output_schema.model_json_schema(), ensure_ascii=False)}"
                )
            )
        ]

        response: AIMessage = await self.chat_model.ainvoke(fallback_messages)
        content = self._normalize_content(response.content)
        parsed = self._try_validate_json_text(content, output_schema)
        if parsed is not None:
            return parsed
        logger.error("退级 JSON 解析失败，原始输出: %s", content)
        raise ValueError("LLM 输出不是有效的 JSON，且无法按目标 Schema 校验。")

    @staticmethod
    def _try_validate_json_text(content: str, output_schema: type[T]) -> T | None:
        try:
            parsed_dict = json.loads(content)
            return output_schema.model_validate(parsed_dict)
        except json.JSONDecodeError:
            return None
        except Exception as exc:
            logger.error("退级 Pydantic 验证失败: %s", exc)
            return None

    def _normalize_response_content(self, response: Any) -> str:
        content = response.content if isinstance(response, AIMessage) else response
        return self._normalize_content(content)

    @staticmethod
    def _normalize_content(content: Any) -> str:
        content = normalize_message_content(content)
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        return content.strip()
