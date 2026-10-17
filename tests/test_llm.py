"""LLM provider + orchestration tests over mocked HTTP transports."""

from __future__ import annotations

import json
import os

import httpx
import pytest
from pydantic import BaseModel

from repo_analyzer.config import AppConfig, LLMProviderConfig
from repo_analyzer.errors import LLMError
from repo_analyzer.llm.base import extract_json_object
from repo_analyzer.llm.gemini import GeminiProvider
from repo_analyzer.llm.openrouter import OpenRouterProvider
from repo_analyzer.llm.service import LLMService


class Answer(BaseModel):
    answer: str
    confidence: float


def _gemini_envelope(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": text}]}}]},
    )


def _openrouter_envelope(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@pytest.fixture()
def api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or")


def _service(handler, providers: list[LLMProviderConfig]) -> LLMService:
    config = AppConfig(default_provider=providers[0].name, providers=providers)
    return LLMService(config, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


class TestExtractJson:
    def test_plain_json(self) -> None:
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_json(self) -> None:
        assert extract_json_object("```json\n{\"a\": 2}\n```") == {"a": 2}

    def test_json_in_prose(self) -> None:
        assert extract_json_object('Sure! here: {"a": 3} hope that helps') == {"a": 3}

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            extract_json_object("not json")

    def test_non_object_rejected(self) -> None:
        with pytest.raises(ValueError):
            extract_json_object("[1, 2, 3]")


class TestGeminiProvider:
    def test_valid_response(self, api_keys: None) -> None:
        handler = lambda req: _gemini_envelope('{"answer": "hi", "confidence": 0.9}')  # noqa: E731
        provider = GeminiProvider(
            LLMProviderConfig(name="g", kind="gemini", model="m", env_key="GEMINI_API_KEY"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        out = provider.complete_json("sys", "user", 100, 0.2)
        assert out["answer"] == "hi"

    def test_empty_candidates_raise_value_error(self, api_keys: None) -> None:
        handler = lambda req: httpx.Response(200, json={"candidates": []})  # noqa: E731
        provider = GeminiProvider(
            LLMProviderConfig(name="g", kind="gemini", model="m", env_key="GEMINI_API_KEY"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(ValueError):
            provider.complete_json("sys", "user", 100, 0.2)

    def test_missing_key_raises_llm_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        provider = GeminiProvider(
            LLMProviderConfig(name="g", kind="gemini", model="m", env_key="GEMINI_API_KEY"),
        )
        with pytest.raises(LLMError):
            provider.complete_json("sys", "user", 100, 0.2)


class TestOpenRouterProvider:
    def test_valid_response(self, api_keys: None) -> None:
        handler = lambda req: _openrouter_envelope('{"answer": "yo", "confidence": 0.5}')  # noqa: E731
        provider = OpenRouterProvider(
            LLMProviderConfig(name="o", kind="openrouter", model="m", env_key="OPENROUTER_API_KEY"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        assert provider.complete_json("s", "u", 10, 0.1)["answer"] == "yo"


class TestLLMService:
    def test_schema_validation_and_provenance(self, api_keys: None) -> None:
        handler = lambda req: _gemini_envelope('{"answer": "it is FastAPI", "confidence": 0.8}')  # noqa: E731
        service = _service(
            handler,
            [LLMProviderConfig(name="gemini", kind="gemini", model="m", env_key="GEMINI_API_KEY")],
        )
        model, provider_config = service.generate(Answer, "sys", "user")
        assert model.answer == "it is FastAPI"
        assert model.confidence == 0.8
        assert provider_config.name == "gemini"

    def test_payload_failure_retries_same_provider(self, api_keys: None) -> None:
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return _gemini_envelope("garbage" if calls["n"] == 1 else json.dumps({"answer": "ok", "confidence": 1.0}))

        service = _service(
            handler,
            [LLMProviderConfig(name="gemini", kind="gemini", model="m", env_key="GEMINI_API_KEY")],
        )
        model, _ = service.generate(Answer, "", "")
        assert model.answer == "ok"
        assert calls["n"] == 2

    def test_validation_feedback_retry(self, api_keys: None) -> None:
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return _gemini_envelope(json.dumps({"answer": "partial"}))  # missing confidence
            return _gemini_envelope(json.dumps({"answer": "complete", "confidence": 0.7}))

        service = _service(
            handler,
            [LLMProviderConfig(name="gemini", kind="gemini", model="m", env_key="GEMINI_API_KEY")],
        )
        model, _ = service.generate(Answer, "", "")
        assert model.confidence == 0.7
        assert calls["n"] == 2

    def test_transport_failure_falls_back_to_next_provider(self, api_keys: None) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            if "gemini" in str(req.url):
                return httpx.Response(401, text="unauthorized")
            return _openrouter_envelope(json.dumps({"answer": "via openrouter", "confidence": 0.4}))

        service = _service(
            handler,
            [
                LLMProviderConfig(name="gemini", kind="gemini", model="m", env_key="GEMINI_API_KEY"),
                LLMProviderConfig(name="openrouter", kind="openrouter", model="m", env_key="OPENROUTER_API_KEY"),
            ],
        )
        model, provider_config = service.generate(Answer, "", "")
        assert model.answer == "via openrouter"
        assert provider_config.name == "openrouter"

    def test_unknown_provider_name_rejected(self, api_keys: None) -> None:
        handler = lambda req: _gemini_envelope('{"answer": "x", "confidence": 1}')  # noqa: E731
        service = _service(
            handler,
            [LLMProviderConfig(name="gemini", kind="gemini", model="m", env_key="GEMINI_API_KEY")],
        )
        with pytest.raises(LLMError):
            service.generate(Answer, "", "", provider="does-not-exist")

    def test_all_providers_exhausted_raises(self, api_keys: None) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return _gemini_envelope("still garbage")

        service = _service(
            handler,
            [LLMProviderConfig(name="gemini", kind="gemini", model="m", env_key="GEMINI_API_KEY")],
        )
        with pytest.raises(LLMError):
            service.generate(Answer, "", "", max_validation_attempts=1)
