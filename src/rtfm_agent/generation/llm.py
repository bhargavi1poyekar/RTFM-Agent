# src/rtfm_agent/generation/llm.py
"""
One small interface over the three LLM providers in settings.LLM_PROVIDER.

Everything else in the app only calls `llm.complete(system, user)`, so
switching provider is a config change, not a code change. Client libraries
are imported lazily: you only need the one you actually use.
"""
from typing import Optional, Protocol

from config.settings import Settings, settings as default_settings


class LLMError(RuntimeError):
    """The LLM is misconfigured or could not be reached."""


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class OllamaLLM:
    def __init__(self, model: str, host: str, temperature: float, max_tokens: int,
                 num_ctx: int = 4096):
        from ollama import Client

        self.name = f"ollama/{model}"
        self._client = Client(host=host)
        self._model = model
        self._options = {"temperature": temperature, "num_predict": max_tokens,
                         "num_ctx": num_ctx}

    def complete(self, system: str, user: str) -> str:
        try:
            response = self._client.chat(
                model=self._model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                options=self._options,
            )
        except Exception as e:  # connection refused, model not pulled, ...
            raise LLMError(f"Ollama call failed ({self.name}): {e}") from e
        return response["message"]["content"]


class OpenAILLM:
    def __init__(self, model: str, api_key: str, temperature: float, max_tokens: int):
        from openai import OpenAI

        self.name = f"openai/{model}"
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception as e:
            raise LLMError(f"OpenAI call failed ({self.name}): {e}") from e
        return response.choices[0].message.content or ""


class AnthropicLLM:
    def __init__(self, model: str, api_key: str, temperature: float, max_tokens: int):
        from anthropic import Anthropic

        self.name = f"anthropic/{model}"
        self._client = Anthropic(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception as e:
            raise LLMError(f"Anthropic call failed ({self.name}): {e}") from e
        return "".join(b.text for b in response.content if b.type == "text")


def get_llm(cfg: Optional[Settings] = None) -> LLM:
    """Build the client for cfg.LLM_PROVIDER. Raises LLMError if a needed
    API key is missing, so a bad config fails at the first request with a
    clear message rather than deep inside a provider SDK."""
    cfg = cfg or default_settings
    common = dict(temperature=cfg.LLM_TEMPERATURE, max_tokens=cfg.LLM_MAX_TOKENS)

    if cfg.LLM_PROVIDER == "ollama":
        return OllamaLLM(cfg.LLM_MODEL, cfg.OLLAMA_HOST, num_ctx=cfg.OLLAMA_NUM_CTX, **common)
    if cfg.LLM_PROVIDER == "openai":
        if not cfg.OPENAI_API_KEY:
            raise LLMError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
        return OpenAILLM(cfg.OPENAI_MODEL, cfg.OPENAI_API_KEY, **common)
    if cfg.LLM_PROVIDER == "anthropic":
        if not cfg.ANTHROPIC_API_KEY:
            raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return AnthropicLLM(cfg.ANTHROPIC_MODEL, cfg.ANTHROPIC_API_KEY, **common)
    raise LLMError(f"Unknown LLM_PROVIDER: {cfg.LLM_PROVIDER}")
