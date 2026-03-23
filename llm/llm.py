from functools import lru_cache

import config

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - runtime fallback when optional dep missing
    ChatOpenAI = None


class _FallbackLLM:
    """Fallback LLM to keep service available when langchain_openai is absent."""

    def __init__(self, reason: str = "llm_dependency_missing"):
        self.reason = reason

    def invoke(self, prompt):
        _ = prompt
        return type("LLMResult", (), {"content": f"服务暂时不可用: {self.reason}"})()


@lru_cache(maxsize=32)
def _build_llm(model: str, temperature: float, timeout: int, max_retries: int):
    if ChatOpenAI is None:
        return _FallbackLLM("langchain_openai_not_installed")

    try:
        return ChatOpenAI(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=model,
            api_key=config.OPENAI_API_KEY,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )
    except Exception as e:  # pragma: no cover - runtime/network/config failures
        return _FallbackLLM(e.__class__.__name__)


def get_llm(model=None, temperature=0, timeout=20, max_retries=1):
    """Centralized LLM factory with cache to reuse clients."""
    model_name = model or config.MODEL
    return _build_llm(str(model_name), float(temperature), int(timeout), int(max_retries))
