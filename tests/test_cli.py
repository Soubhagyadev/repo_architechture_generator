"""CLI end-to-end tests via typer's CliRunner (all offline)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repo_analyzer.cli import app

runner = CliRunner()


@pytest.fixture()
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI cache somewhere temporary and clear API keys."""
    cache = tmp_path / "cache"
    monkeypatch.setenv("REPO_ANALYZER_CACHE", str(cache))
    for key in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    return cache


def test_analyze_writes_reports(sample_repo: Path, cli_env: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "reports"
    result = runner.invoke(
        app,
        ["analyze", str(sample_repo), "--local", "--no-llm", "--out", str(out_dir)],
    )
    assert result.exit_code == 0
    files = {path.name for path in out_dir.iterdir()}
    assert any(name.endswith(".md") for name in files)
    assert any(name.endswith(".html") for name in files)
    md_text = next((out_dir / name).read_text() for name in files if name.endswith(".md"))
    assert "```mermaid" in md_text
    assert "Overview" in md_text or "Summary" in md_text


def test_ask_grounds_answer(sample_repo: Path, cli_env: Path) -> None:
    result = runner.invoke(
        app,
        ["ask", str(sample_repo), "--local", "--no-llm", "How does create_user work?"],
    )
    assert result.exit_code == 0
    assert "Citations" in result.output
    assert "src/app" in result.output


def test_bug_runs_investigation(sample_repo: Path, cli_env: Path) -> None:
    result = runner.invoke(
        app,
        [
            "bug",
            str(sample_repo),
            "--local",
            "--no-llm",
            "create_user crashes when the name is blank",
        ],
    )
    assert result.exit_code == 0
    assert "tests to examine" in result.output
    assert "test_services.py" in result.output


def test_bug_json_output(sample_repo: Path, cli_env: Path) -> None:
    result = runner.invoke(
        app,
        ["bug", str(sample_repo), "--local", "--no-llm", "--json", "create_user blank name bug"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["suspects"]


def test_architecture_prints_mermaid(sample_repo: Path, cli_env: Path) -> None:
    result = runner.invoke(app, ["architecture", str(sample_repo), "--local"])
    assert result.exit_code == 0
    assert "flowchart" in result.output


def test_docs_lists_directories(sample_repo: Path, cli_env: Path) -> None:
    result = runner.invoke(app, ["docs", str(sample_repo), "--local"])
    assert result.exit_code == 0
    assert "src" in result.output
    assert "tests" in result.output


def test_invalid_github_target_fails(sample_repo: Path, cli_env: Path) -> None:
    result = runner.invoke(app, ["analyze", "not-a-github-thing-%%"])
    assert result.exit_code != 0
    assert "error:" in result.output


def test_html_report_embeds_sections(sample_repo: Path, cli_env: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "html_reports"
    result = runner.invoke(
        app,
        ["analyze", str(sample_repo), "--local", "--no-llm", "--out", str(out_dir)],
    )
    assert result.exit_code == 0
    html_path = next(path for path in out_dir.iterdir() if path.suffix == ".html")
    text = html_path.read_text()
    assert "<!DOCTYPE html>" in text
    assert "mermaid" in text
    assert "src/app" in text
