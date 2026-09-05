"""Thin wrapper over the Anthropic-compatible endpoint (Zhipu GLM)."""
from __future__ import annotations

from anthropic import Anthropic


class LLMNotConfigured(Exception):
    pass


class LLM:
    def __init__(self, settings):
        self.model_main = settings.llm_model_main
        self.model_light = settings.llm_model_light
        self._client = Anthropic(
            api_key=settings.llm_api_key or "missing",
            base_url=settings.llm_base_url,
            max_retries=1,
            timeout=600,
        ) if settings.llm_api_key else None

    @property
    def configured(self) -> bool:
        return self._client is not None

    def complete(self, system: str, user: str, model: str | None = None, max_tokens: int = 8192) -> tuple[str, dict]:
        if not self.configured:
            raise LLMNotConfigured("LLM_API_KEY / ANTHROPIC_AUTH_TOKEN not set")
        resp = self._client.messages.create(
            model=model or self.model_main,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return text, usage
