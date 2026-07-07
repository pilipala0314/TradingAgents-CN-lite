"""真实 LLM 调用 smoke：调一次 DeepSeek，验 API key / base_url / model 真可调。

跑前：.env 中 LLM_API_KEY / LLM_API_BASE / LLM_MODEL_NAME 已配好。
跑后：写 results_llm_smoke.json 备查。
"""
import asyncio
import json
import time
from pathlib import Path

from core.llm.client import LLMClient, LLMUnavailable


async def main() -> None:
    print("=" * 60)
    print("真实 LLM smoke：DeepSeek (or provider per LLM_PROVIDER)")
    print("=" * 60)

    t0 = time.time()
    try:
        client = LLMClient()
    except Exception as e:
        print(f"  LLMClient init: ERROR {type(e).__name__}: {e}")
        Path("results_llm_smoke.json").write_text(
            json.dumps({"status": "init_error", "error": f"{type(e).__name__}: {e}"},
                       ensure_ascii=False, indent=2)
        )
        return

    print(f"  provider={client.provider} model={client.model_name}")
    if client.chat_model is None:
        print("  ⚠ LLMClient 未配置有效 api_key，调用会抛 LLMUnavailable")
    else:
        print(f"  base_url={client._client.base_url} key_tail=...{client._client.api_key[-4:]}")

    prompt = "用一句话介绍你自己。限制在 20 字以内。"
    try:
        content = await client.ainvoke_text(
            system_prompt="你是一位简练的助手。",
            user_prompt=prompt,
        )
        elapsed = time.time() - t0
        print(f"  ainvoke_text: ok in {elapsed:.2f}s")
        print(f"  response: {content!r}")
        result = {
            "status": "ok",
            "provider": client.provider,
            "model": client.model_name,
            "base_url": client._client.base_url,
            "elapsed_sec": round(elapsed, 3),
            "prompt": prompt,
            "response": content,
        }
    except LLMUnavailable as e:
        elapsed = time.time() - t0
        print(f"  LLMUnavailable: {e}")
        result = {"status": "llm_unavailable", "elapsed_sec": round(elapsed, 3), "error": str(e)}
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ERROR {type(e).__name__}: {e}")
        result = {
            "status": "error",
            "elapsed_sec": round(elapsed, 3),
            "error": f"{type(e).__name__}: {e}",
        }

    out_path = Path("results_llm_smoke.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("=" * 60)
    print(f"结果写入: {out_path} (status={result['status']})")


if __name__ == "__main__":
    asyncio.run(main())
