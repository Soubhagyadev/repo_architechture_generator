"""Google Gemini provider using the generateContent REST API."""

from __future__ import annotations

from typing import Any

from repo_analyzer.config import LLMProviderConfig
from repo_analyzer.llm.base import BaseProvider

_DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(BaseProvider):
    """Calls Gemini with JSON response mode enabled."""

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Generate a JSON object response."""
        base = self.config.base_url or _DEFAULT_BASE
        url = f"{base}/models/{self.config.model}:generateContent"
        key = self._api_key()
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        header = {"x-goog-api-key": key}
        envelope = self._post_json(url, payload, header)
        candidates = envelope.get("candidates") or []
        if not candidates:
            finish_reason = str(envelope.get("promptFeedback", {}).get("blockReason", ""))
            blocked = f" (blocked: {finish_reason})" if finish_reason else ""
            raise ValueError(
                f"gemini returned no candidates{blocked}: usage={envelope.get('usageMetadata', '')}"
            )
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(str(part.get("text") or "") for part in parts)
        if not text.strip():
            raise ValueError("gemini response contained no text parts")
        from repo_analyzer.llm.base import extract_json_object

        return extract_json_object(text)


__all__ = ["GeminiProvider"]
