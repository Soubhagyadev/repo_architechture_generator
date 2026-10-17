"""Schema for configuring AI providers, operating limits, and analysis behavior."""

from __future__ import annotations

import os
import tomllib
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator

PROJECT_CACHE_DIR = "repo_analyzer_cache"


class LLMProviderConfig(BaseModel):
    """A single configured LLM endpoint.

    Attributes:
        name: Stable identifier used by ``--provider`` selectors.
        kind: Backend protocol family; ``gemini`` uses Google's REST shape and
            ``openrouter`` (and any other OpenAI-compatible service) uses the
            chat-completions shape.
        model: Default model identifier sent with each request.
        env_key: Environment variable holding the API credential.
        base_url: Optional override for self-hosted or alternate endpoints.
    """

    name: str
    kind: str = "openrouter"
    model: str
    env_key: str
    base_url: str | None = None

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        if value not in {"gemini", "openrouter"}:
            raise ValueError('kind must be "gemini" or "openrouter" (openai-compatible)')
        return value


class LimitsConfig(BaseModel):
    """Safety and budget limits applied during repository analysis."""

    max_repo_files: int = 5000
    max_repo_bytes: int = 768 * 1024 * 1024
    max_file_bytes: int = 1_000_000
    max_batch_files: int = 200
    max_batch_chars: int = 900_000
    pack_char_budget: int = 26_000
    pack_min_score: float = 0.0
    max_files_per_pack: int = 120
    gathered_bytes_limit: int = 12_000_000
    history_limit: int = 10_000
    max_depth: int = 40
    min_lines_for_split: int = 260
    max_functions_per_summary_batch: int = 12


class ProviderDefaults(BaseModel):
    """Default provider/model selections plus generation parameters."""

    provider: str = "gemini"
    temperature: float = 0.2
    max_output_tokens: int = 4096
    max_retries: int = 5
    timeout_seconds: float = 90.0


class AnalysisConfig(BaseModel):
    """Toggleable analysis features and their budgets."""

    summarize_symbols: bool = True
    summarize_files: bool = True
    summarize_modules: bool = True
    max_summary_tokens: int = 512
    max_files_per_graph_batch: int = 40
    max_graph_chars: int = 120_000
    max_semantic_chunks: int = 160
    max_chunk_chars: int = 1200
    max_qa_files: int = 120
    chunk_parallel: bool = True
    max_chunk_workers: int = 4


class CompileConfig(BaseModel):
    """Budgets for the final architecture-documentation pass."""

    max_input_chars: int = 120_000
    circa_max_sections: int = 12


class CacheConfig(BaseModel):
    """Where intermediate and cumulative analysis state is persisted."""

    root: str = PROJECT_CACHE_DIR


class GitHubConfig(BaseModel):
    """Optional GitHub access and request pacing knobs."""

    api_base: str = "https://api.github.com"
    token_env: str = "GITHUB_TOKEN"
    per_page: int = 100


class AppConfig(BaseModel):
    """Fully materialized application configuration."""

    default_provider: str = "gemini"
    providers: list[LLMProviderConfig] = Field(default_factory=list)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    provider_defaults: ProviderDefaults = Field(default_factory=ProviderDefaults)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)

    def provider(self, name: str) -> LLMProviderConfig | None:
        """Return the provider entry with the given name, if configured."""
        for provider in self.providers:
            if provider.name.lower() == name.lower():
                return provider
        return None

    def fallback_chain(self, start: str) -> list[LLMProviderConfig]:
        """List providers starting at ``start`` and followed by all others."""
        ordered: list[LLMProviderConfig] = []
        for provider in self.providers:
            if provider.name.lower() == start.lower():
                ordered.append(provider)
        for provider in self.providers:
            if provider not in ordered:
                ordered.append(provider)
        return ordered

    @classmethod
    def read(cls, path: str) -> "AppConfig":
        """Load configuration from a TOML file."""
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        return cls.model_validate(data)


def _augment_with_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Fill in provider entries needed by the selected default provider.

    All supported providers (gemini, openrouter, groq) are ensured so that the
    runtime fallback chain reaches whichever API key the user actually set.
    The selected default provider is ordered first in the chain.
    """
    configured_default = str(data.get("default_provider", "gemini")).lower()
    providers = data.setdefault("providers", [])
    names = {str(entry.get("name", "")).lower() for entry in providers}

    defaults = data.get("provider_defaults")
    default_model = None
    if isinstance(defaults, dict):
        default_model = defaults.get("model")

    groq_model = os.environ.get("GROQ_MODEL") or "qwen/qwen3.8-27b"
    openrouter_model = os.environ.get("OPENROUTER_MODEL") or "openai/gpt-4o-mini"

    def _ensure(
        name: str,
        kind: str,
        model: str,
        env_key: str,
        base_url: str | None = None,
    ) -> None:
        if name in names:
            return
        entry: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "env_key": env_key,
        }
        if model:
            entry["model"] = model
        if base_url:
            entry["base_url"] = base_url
        providers.append(entry)
        names.add(name)

    groq = lambda: _ensure(  # noqa: E731
        "groq",
        "openrouter",
        groq_model,
        "GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
    )

    if configured_default == "gemini":
        _ensure("gemini", "gemini", str(default_model or "gemini-2.5-flash"), "GEMINI_API_KEY")
        _ensure("openrouter", "openrouter", openrouter_model, "OPENROUTER_API_KEY")
        groq()
    elif configured_default in {"openrouter", "openai"}:
        _ensure("openrouter", "openrouter", str(default_model or openrouter_model), "OPENROUTER_API_KEY")
        _ensure("gemini", "gemini", "gemini-2.5-flash", "GEMINI_API_KEY")
        groq()
    elif configured_default == "groq":
        _ensure("groq", "openrouter", str(default_model or groq_model), "GROQ_API_KEY", base_url="https://api.groq.com/openai/v1")
        _ensure("gemini", "gemini", "gemini-2.5-flash", "GEMINI_API_KEY")
        _ensure("openrouter", "openrouter", openrouter_model, "OPENROUTER_API_KEY")
    else:
        valid = ", ".join(["gemini", "openrouter", "groq"])
        raise ValueError(f"Unknown default provider '{configured_default}'. Valid: {valid}")
    return data


def load_config(path: str | None = None) -> AppConfig:
    """Load config from ``path`` or discover it from environment/cwd.

    Resolution order:
      1. explicit ``path``
      2. ``REPO_ANALYZER_CONFIG`` environment variable
      3. ``repo_analyzer.toml`` in the current working directory
      4. defaults with environment-driven provider entries
    Unknown top-level keys are tolerated so partial config files keep working.
    """
    candidate_paths: list[str] = []
    if path:
        candidate_paths.append(path)
    env_path = os.environ.get("REPO_ANALYZER_CONFIG")
    if env_path:
        candidate_paths.append(env_path)
    candidate_paths.append("repo_analyzer.toml")

    data: dict[str, Any] = {}
    used_file = False
    for candidate in candidate_paths:
        if candidate and os.path.exists(candidate):
            with open(candidate, "rb") as handle:
                data = tomllib.load(handle)
            used_file = True
            break

    if not used_file:
        default_provider = os.environ.get("REPO_ANALYZER_PROVIDER", "gemini").lower()
        data = {"default_provider": default_provider}
        if default_provider == "openrouter":
            data["provider_defaults"] = {}
            if os.environ.get("OPENROUTER_MODEL"):
                data["provider_defaults"]["model"] = os.environ["OPENROUTER_MODEL"]

    _augment_with_defaults(data)
    cache_dir = os.environ.get("REPO_ANALYZER_CACHE")
    if cache_dir:
        cache_override = data.setdefault("cache", {})
        if isinstance(cache_override, dict):
            cache_override["root"] = cache_dir
    # Tolerate unknown keys and missing optional sections.
    return AppConfig.model_validate(data)


class ModuleSelectionResult(BaseModel):
    """Outcome of deciding which modules to analyze for a request."""

    selected_files: list[str] = Field(default_factory=list)
    rationale: str = ""
    used_fallback: bool = False

    @property
    def is_ok(self) -> bool:
        """Whether at least one file was selected."""
        return bool(self.selected_files) and not self.used_fallback


__all__ = [
    "AnalysisConfig",
    "AppConfig",
    "CacheConfig",
    "CompileConfig",
    "GitHubConfig",
    "LimitsConfig",
    "LLMProviderConfig",
    "ModuleSelectionResult",
    "PROJECT_CACHE_DIR",
    "ProviderDefaults",
    "load_config",
]
