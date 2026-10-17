"""High-level LLM service: provider fallback, JSON schema validation, retries.

Any feature can call ``Service.generate(schema, system, user)`` and receive a
pydantic-validated model or an explicit ``LLMFailure`` it can degrade from.
That keeps model flakiness out of feature code.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from repo_analyzer.config import AppConfig, LLMProviderConfig
from repo_analyzer.errors import LLMError
from repo_analyzer.llm.base import BaseProvider
from repo_analyzer.llm.gemini import GeminiProvider
from repo_analyzer.llm.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", contravariant=False)

_RETRYABLE_MARKERS = (
    "rate limit",
    "timeout",
    "temporarily unavailable",
    "try again",
    "server error",
    "overloaded",
    "capacity",
)


@dataclass
class LLMFailure:
    """Structured failure with an optional auto-resume hint."""

    message: str
    provider_name: str
    retryable: bool = False
    resume_hint: str | None = None

    @classmethod
    def from_error(cls, err: BaseException, provider_name: str = "") -> "LLMFailure":
        """Classify an exception into a failure record."""
        text = str(err)
        retryable = any(marker in text.lower() for marker in _RETRYABLE_MARKERS)
        return cls(message=text, provider_name=provider_name, retryable=retryable)

    def to_json(self) -> dict[str, Any]:
        """Serialize for caching and UI surfacing."""
        return {
            "message": self.message,
            "provider": self.provider_name,
            "retryable": self.retryable,
            "resume_hint": self.resume_hint,
        }


@dataclass
class LLMResponse(Generic[T]):
    """A validated model output along with provenance."""

    data: T
    provider_name: str
    model: str

    def to_json(self) -> dict[str, Any]:
        """Serialize provenance (payload serializes itself)."""
        return {
            "provider": self.provider_name,
            "model": self.model,
            "data": self.data.model_dump() if isinstance(self.data, BaseModel) else self.data,
        }


class LLMService:
    """Facade over provider implementations with fallback + validation."""

    def __init__(self, config: AppConfig, http_client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = http_client
        self._active_provider: BaseProvider | None = None
        self._active_name = ""

    def _providers(self, start: str | None = None) -> list[LLMProviderConfig]:
        """Ordered provider chain for fallback behavior."""
        chain = self.config.fallback_chain(start or self.config.default_provider)
        return chain or []

    def _instantiate(self, provider_config: LLMProviderConfig) -> BaseProvider:
        """Create a provider client for ``provider_config``."""
        if provider_config.kind == "gemini":
            provider: BaseProvider = GeminiProvider(
                provider_config,
                client=self._client,
                timeout=self.config.provider_defaults.timeout_seconds,
            )
        elif provider_config.kind == "openrouter":
            provider = OpenRouterProvider(
                provider_config,
                client=self._client,
                timeout=self.config.provider_defaults.timeout_seconds,
            )
        else:
            raise LLMError(f"unknown provider kind {provider_config.kind!r}")
        logger.debug("instantiated %s provider %s", provider_config.kind, provider_config.name)
        return provider

    def generate(
        self,
        schema: type[T],
        system_prompt: str,
        user_prompt: str,
        *,
        provider: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        max_validation_attempts: int = 3,
        validation_feedback: str | None = None,
        history: list[BaseException] | None = None,
    ) -> tuple[T, LLMProviderConfig]:
        """Generate a validated instance of ``schema`` from any fallback provider.

        The prompts are provider-neutral. On JSON/schema failures the service
        retries the same provider with a corrective prefix, then moves on to
        the next provider in the chain. Raises ``LLMError`` only when every
        provider attempt is exhausted.
        """
        defaults = self.config.provider_defaults
        tokens = max_output_tokens or defaults.max_output_tokens
        temp = defaults.temperature if temperature is None else temperature
        attempted_errors: list[str] = []
        history = history or []

        label = system_prompt.splitlines()[0][:80] if system_prompt else "generation"
        chain = self._providers(provider)
        if provider and not any(entry.name.lower() == provider.lower() for entry in chain):
            raise LLMError(f"provider '{provider}' is not configured")
        for provider_config in chain:
            if provider and provider_config.name.lower() != provider.lower():
                continue
            try:
                provider_client = self._instantiate(provider_config)
            except LLMError as exc:
                logger.warning("provider %s unavailable: %s", provider_config.name, exc)
                attempted_errors.append(str(exc))
                continue
            feedback_text = validation_feedback or ""
            for attempt in range(1, max_validation_attempts + 2):
                try:
                    raw = provider_client.complete_json(
                        system_prompt, user_prompt + feedback_text, tokens, temp
                    )
                except ValueError as exc:
                    # Payload-level failure: retry this provider, then fall through.
                    history.append(exc)
                    attempted_errors.append(f"{provider_config.name}: {exc}")
                    logger.info(
                        "payload failure on %s for %s try=%s: %s",
                        provider_config.name,
                        label,
                        attempt,
                        exc,
                    )
                    if attempt > max_validation_attempts:
                        break
                    feedback_text = (
                        "\n\nYour previous reply was not a valid JSON object. "
                        "Respond with a single valid JSON object only - no prose, no markdown fences."
                    )
                    continue
                except LLMError as exc:
                    # Transport/auth-level failure: continue to the next provider.
                    history.append(exc)
                    attempted_errors.append(f"{provider_config.name}: {exc}")
                    logger.info(
                        "provider %s transport failure for %s try=%s: %s",
                        provider_config.name,
                        label,
                        attempt,
                        exc,
                    )
                    break
                try:
                    validated = schema.model_validate(raw)
                    return validated, provider_config
                except ValidationError as exc:
                    feedback_text = (
                        "\n\nIMPORTANT: A previous response was rejected by validation with these errors:\n"
                        + _format_validation_errors(exc)
                        + "\nMake sure the response is valid JSON matching the schema exactly. Return the corrected object only."
                    )
                    logger.info(
                        "validation retry %s for %s via %s: %s",
                        attempt,
                        label,
                        provider_config.name,
                        exc.error_count(),
                    )
        raise LLMError("; ".join(attempted_errors) or "no LLM providers configured")

    async def agenerate(
        self,
        schema: type[T],
        system_prompt: str,
        user_prompt: str,
        **kwargs: Any,
    ) -> tuple[T, LLMProviderConfig]:
        """Async wrapper for :meth:`generate` (runs in a worker thread)."""
        return await asyncio.to_thread(self.generate, schema, system_prompt, user_prompt, **kwargs)

    def close(self) -> None:
        """Close any lazily created HTTP client."""
        self._active_provider = None


def _format_validation_errors(exc: ValidationError, limit: int = 5) -> str:
    """Compact bullet list of validation failures for the retry prompt."""
    lines: list[str] = []
    for error in exc.errors()[:limit]:
        loc = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        lines.append(f"- field '{loc}': {error.get('msg')}")
    return "\n".join(lines)


__all__ = ["LLMFailure", "LLMResponse", "LLMService"]
