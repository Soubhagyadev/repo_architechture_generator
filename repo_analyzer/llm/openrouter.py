"""OpenRouter (OpenAI-compatible chat completions) provider."""

from __future__ import annotations

from typing import Any

from repo_analyzer.config import LLMProviderConfig
from repo_analyzer.llm.base import BaseProvider, extract_json_object

_DEFAULT_BASE = "https://openrouter.ai/api/v1"


class OpenRouterProvider(BaseProvider):
    """Calls OpenRouter's chat-completions endpoint asking for JSON mode."""

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Generate a JSON object response via OpenRouter."""
        base = self.config.base_url or _DEFAULT_BASE
        url = f"{base}/chat/completions"
        key = self._api_key()
        payload = {
            "model": self.config.model,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "X-Title": "repo-analyzer",
        }
        envelope = self._post_json(url, payload, headers)
        choices = envelope.get("choices") or []
        if not choices:
            error = envelope.get("error")
            raise ValueError(f"openrouter returned no choices: {error}")
        message = choices[0].get("message") or {}
        text = str(message.get("content") or "")
        if not text.strip():
            raise ValueError("openrouter response contained empty content")
        return extract_json_object(text)


__all__ = ["OpenRouterProvider"]
