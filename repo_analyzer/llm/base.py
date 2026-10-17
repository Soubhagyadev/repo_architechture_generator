"""Provider abstractions for Gemini and OpenRouter-style chat APIs."""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from repo_analyzer.config import LLMProviderConfig
from repo_analyzer.errors import LLMError


class BaseProvider(ABC):
    """Common retry/transport plumbing for LLM REST providers."""

    def __init__(
        self,
        provider_config: LLMProviderConfig,
        client: httpx.Client | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.config = provider_config
        self._client = client or httpx.Client(timeout=timeout)
        self.timeout = timeout

    def _api_key(self) -> str:
        """Resolve the API key from the environment or raise a clear error."""
        key = os.environ.get(self.config.env_key, "")
        if not key:
            raise LLMError(
                f"missing API key: set {self.config.env_key} for provider '{self.config.name}'"
            )
        return key

    @abstractmethod
    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Request structured JSON output and return the parsed object."""

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """POST with retry/backoff for 429/5xx; returns parsed JSON body."""
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if attempt >= 4:
                    raise LLMError(f"network error talking to {self.config.name}: {exc}") from exc
                time.sleep(min(2**attempt, 8))
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt >= 5:
                    raise LLMError(
                        f"{self.config.name} returned {response.status_code} after retries"
                    )
                retry_after = response.headers.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after else min(2**attempt, 8)
                except ValueError:
                    delay = min(2**attempt, 8)
                time.sleep(delay)
                continue
            if response.status_code != 200:
                snippet = response.text[:300]
                raise LLMError(
                    f"{self.config.name} returned {response.status_code}: {snippet}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise LLMError(f"{self.config.name} sent invalid JSON envelope") from exc


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from model text that may wrap it in fences/prose."""
    cleaned = text.strip()
    if not cleaned:
        raise LLMError("model returned empty response")
    if cleaned.startswith("{"):
        candidates = [cleaned]
    else:
        candidates = []
        fence = None
        if "```json" in cleaned:
            fence = "```json"
        elif "```" in cleaned:
            fence = "```"
        if fence:
            chunk = cleaned.split(fence, 1)[1]
            chunk = chunk.split("```", 1)[0]
            candidates.append(chunk.strip())
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last > first:
            candidates.append(cleaned[first : last + 1])
    errors: list[str] = []
    for candidate in candidates[:2]:
        try:
            parsed = json.loads(candidate)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if isinstance(parsed, dict):
            return parsed
        errors.append("payload is not a JSON object")
    # Payload-level failure: retry on the same provider, fall through after.
    raise ValueError("could not parse JSON from model response: " + "; ".join(errors[:2]))


def merge_settings(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge provider call settings."""
    merged = dict(base)
    if override:
        merged.update(override)
    return merged


__all__ = ["BaseProvider", "extract_json_object", "merge_settings"]
