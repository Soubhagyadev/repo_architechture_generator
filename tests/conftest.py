"""Shared pytest fixtures: the sample repository and config factory."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_REPO = REPO_ROOT / "tests" / "fixtures" / "sample_repo"

sys.path.insert(0, str(REPO_ROOT))
collect_ignore = ["fixtures"]


@pytest.fixture()
def sample_repo() -> Path:
    """Path to the checked-in sample repository fixture."""
    return SAMPLE_REPO


@pytest.fixture()
def temp_config():
    """AppConfig pointed at a fresh temporary cache directory."""
    from repo_analyzer.config import AppConfig

    with tempfile.TemporaryDirectory() as cache_dir:
        yield AppConfig(cache={"root": cache_dir})


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove API keys so tests exercise the deterministic path."""
    for name in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
