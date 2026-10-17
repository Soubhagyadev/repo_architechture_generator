"""Config loading and small utility tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from repo_analyzer.config import (
    AppConfig,
    LLMProviderConfig,
    load_config,
)
from repo_analyzer.utils import (
    first_text,
    flatten_tree,
    read_json,
    rel_path,
    sha256_hex,
    unique_extend,
    write_json,
)


class TestConfig:
    def test_default_gemini_provider_injected(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("REPO_ANALYZER_CONFIG", raising=False)
        monkeypatch.delenv("REPO_ANALYZER_CACHE", raising=False)
        config = load_config()
        assert config.default_provider == "gemini"
        provider = config.provider("gemini")
        assert provider is not None
        assert provider.env_key == "GEMINI_API_KEY"

    def test_fallback_chain_keeps_selected_first(self, tmp_path: Path) -> None:
        config = AppConfig(
            providers=[
                LLMProviderConfig(name="gemini", kind="gemini", model="m", env_key="A"),
                LLMProviderConfig(name="openrouter", kind="openrouter", model="m", env_key="B"),
            ]
        )
        chain = config.fallback_chain("openrouter")
        assert chain[0].name == "openrouter"

    def test_env_cache_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("REPO_ANALYZER_CONFIG", raising=False)
        monkeypatch.setenv("REPO_ANALYZER_CACHE", str(tmp_path / "custom"))
        config = load_config()
        assert config.cache.root.endswith("custom")

    def test_toml_file_is_read(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        toml_file = tmp_path / "cfg.toml"
        toml_file.write_text('default_provider = "openrouter"\n[[providers]]\nname = "openrouter"\nkind = "openrouter"\nmodel = "test-model"\nenv_key = "OPENROUTER_API_KEY"\n')
        monkeypatch.setenv("REPO_ANALYZER_CONFIG", str(toml_file))
        config = load_config()
        assert config.default_provider == "openrouter"
        assert config.provider("openrouter").model == "test-model"

    def test_invalid_provider_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LLMProviderConfig(name="x", kind="chatgpt", model="m", env_key="K")

    def test_groq_provider_autoconfigured(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("REPO_ANALYZER_CONFIG", raising=False)
        monkeypatch.delenv("REPO_ANALYZER_CACHE", raising=False)
        monkeypatch.delenv("GROQ_MODEL", raising=False)
        config = load_config()
        groq = config.provider("groq")
        assert groq is not None
        assert groq.kind == "openrouter"  # OpenAI-compatible protocol
        assert groq.base_url == "https://api.groq.com/openai/v1"
        assert groq.model == "qwen/qwen3.8-27b"
        assert groq.env_key == "GROQ_API_KEY"
        # all built-in providers join the fallback chain
        chain_names = [entry.name for entry in config.fallback_chain("groq")]
        assert chain_names[0] == "groq"
        assert {"gemini", "openrouter"} <= set(chain_names)

    def test_groq_model_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("REPO_ANALYZER_CONFIG", raising=False)
        monkeypatch.delenv("REPO_ANALYZER_CACHE", raising=False)
        monkeypatch.setenv("GROQ_MODEL", "llama-3.1-8b-instant")
        config = load_config()
        assert config.provider("groq").model == "llama-3.1-8b-instant"

    def test_groq_default_provider_first_in_chain(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("REPO_ANALYZER_CONFIG", raising=False)
        monkeypatch.setenv("REPO_ANALYZER_PROVIDER", "groq")
        monkeypatch.delenv("REPO_ANALYZER_CACHE", raising=False)
        config = load_config()
        assert config.default_provider == "groq"
        assert config.fallback_chain("groq")[0].name == "groq"


class TestUtils:
    def test_sha256_hex(self) -> None:
        assert sha256_hex(b"abc").startswith("ba7816bf")

    def test_flatten_tree(self) -> None:
        tree = {"src": {"app": {"main.py": 1}}, "README.md": None}
        paths = list(flatten_tree(tree))
        assert "src/app/main.py" in paths
        assert "README.md" in paths

    def test_first_text(self) -> None:
        assert first_text(None) == ""
        assert first_text("  spaced\tout  ") == "spaced out"
        assert "a" in first_text(["a", "b"])

    def test_unique_extend(self) -> None:
        target = ["a"]
        unique_extend(target, ["b", "a", "c", ""])
        assert target == ["a", "b", "c"]

    def test_json_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "data.json"
        write_json(path, {"k": [1, 2]})
        assert read_json(path) == {"k": [1, 2]}
        assert read_json(path.parent / "missing.json") is None

    def test_rel_path(self, tmp_path: Path) -> None:
        base = tmp_path
        target = tmp_path / "sub" / "file.txt"
        assert rel_path(base, target) == "sub/file.txt"

    def test_first_text_json_passthrough(self) -> None:
        assert isinstance(first_text({"a": 1}), str)
        json.loads(first_text({"a": 1}))
