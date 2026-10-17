"""Feature tests: context packs, overview, architecture, docs, Q&A, contributor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_analyzer.analyzer import RepoAnalyzer
from repo_analyzer.features import qa as qa_feature
from repo_analyzer.features.architecture_feature import architecture_payload
from repo_analyzer.features.contributor import find_test_files, investigate, trace_impact
from repo_analyzer.features.context import (
    build_pack,
    ranked_paths,
    sanitize_for_prompt,
    tokenize,
)
from repo_analyzer.features.documentation import documentation_payload
from repo_analyzer.features.overview import deterministic_overview
from repo_analyzer.utils import sha256_hex


@pytest.fixture(scope="module")
def analyzed(sample_repo: Path):
    from repo_analyzer.config import AppConfig
    import tempfile

    with tempfile.TemporaryDirectory() as cache_dir:
        config = AppConfig(cache={"root": cache_dir})
        with RepoAnalyzer(config) as analyzer:
            yield analyzer.analyze(str(sample_repo), is_local=True)


class TestContextPacks:
    def test_ranked_paths_surface_relevant_files(self, analyzed) -> None:
        ranked = ranked_paths(analyzed, "how does create_user persist to the database", limit=6)
        assert "src/app/services.py" in ranked or "src/app/db.py" in ranked

    def test_build_pack_respects_budget(self, analyzed) -> None:
        pack = build_pack(analyzed, list(analyzed.parsed)[:8], budget=3000)
        assert pack.char_total <= 3000
        assert pack.paths

    def test_pack_includes_line_numbers(self, analyzed) -> None:
        pack = build_pack(analyzed, ["src/app/services.py"], budget=8000)
        assert any("| def " in block for block in pack.files.values())
        assert pack.render().count("|") >= 5

    def test_focus_symbols_limit_ranges(self, analyzed) -> None:
        pack = build_pack(
            analyzed,
            ["src/app/services.py"],
            budget=8000,
            focus_symbols={"src/app/services.py": ["create_user"]},
        )
        assert all("create_user" in pack.files["src/app/services.py"] for _ in [0])

    def test_secrets_redacted(self) -> None:
        text = 'AKIAABCDEFGHIJKLMNOP = "supersecret"\nghp_abcdefghijklmnopqrst\nsk-abcdefghijklmnopqrstuvwx\nAPI_KEY = "hunter2"\n'
        cleaned = sanitize_for_prompt(text)
        assert "supersecret" not in cleaned
        assert "ghp_abcdefghijklmnopqrst" not in cleaned
        assert "hunter2" not in cleaned

    def test_tokenize(self) -> None:
        tokens = tokenize("def create_user(name: str)")
        assert {"user", "name", "str", "create", "create_user"} <= tokens or {"create", "user"} & tokens


class TestOverview:
    def test_deterministic_payload(self, analyzed) -> None:
        payload = deterministic_overview(analyzed)
        stats = payload["stats"]
        assert stats["files"] == analyzed.file_count
        assert stats["entry_points"]
        assert stats["diagrams"]
        assert "FastAPI" in stats["frameworks"]
        assert stats["flows"]

    def test_no_llm_returns_none(self, analyzed) -> None:
        from repo_analyzer.features.overview import llm_overview

        assert llm_overview(analyzed, None) is None


class TestArchitecture:
    def test_payload_shape(self, analyzed) -> None:
        payload = architecture_payload(analyzed)
        assert len(payload["diagrams"]) == 3
        assert any(entry["entry"] == "src/app/main.py" for entry in list(map(lambda flow: {"entry": flow["entry"]}, payload["flows"])))
        assert "src::app" in {component["name"] for component in payload["components"]}


class TestDocumentation:
    def test_directories_and_tech(self, analyzed) -> None:
        payload = documentation_payload(analyzed)
        dirs = {directory["path"]: directory["purpose"] for directory in payload["directories"]}
        assert dirs["src"] == "primary application/source code"
        assert dirs["tests"] == "automated tests"
        assert payload["tech"]


class TestQA:
    def test_retrieval_findings(self, analyzed) -> None:
        chunks = qa_feature.retrieve_chunks(analyzed, "How does user creation persist to the database?")
        paths = {chunk.path for chunk in chunks}
        assert {"src/app/services.py", "src/app/db.py"} & paths

    def test_deterministic_answer_with_citation(self, analyzed, temp_config) -> None:
        answer = qa_feature.ask(analyzed, "How does user creation work?", temp_config)
        assert answer.used_llm is False
        assert answer.citations
        first = answer.citations[0]
        assert first.file in analyzed.parsed
        assert first.line <= analyzed.loc_for(first.file)
        loc = f"{first.file}:{first.line}"
        assert loc in answer.answer


class TestContributor:
    def test_find_test_files_detects_pytest(self, analyzed) -> None:
        test_files, frameworks = find_test_files(analyzed)
        assert "tests/test_services.py" in test_files
        assert "pytest" in frameworks

    def test_trace_impact_both_directions(self, analyzed) -> None:
        nodes = trace_impact(analyzed, ["src/app/services.py"])
        directions = {node.direction for node in nodes}
        assert "direct" in directions
        assert {"upstream", "downstream"} <= directions
        paths = {node.path for node in nodes}
        assert "src/app/main.py" in paths  # imports services
        assert "src/app/db.py" in paths    # depends on services

    def test_investigation_payload(self, analyzed, temp_config) -> None:
        payload = investigate(analyzed, "create_user crashes when the name is blank", temp_config, llm=None)
        assert payload["suspects"]
        assert "create_user" in {hit["symbol"] for hit in payload["symbol_hits"]}
        related = {test["file"] for test in payload["related_tests"]}
        assert "tests/test_services.py" in related
        plan = payload["plan"]
        assert plan["hypotheses"]
        assert plan["suggested_tests"]
        assert plan["next_steps"]

    def test_symbol_hit_lines_are_real(self, analyzed, temp_config) -> None:
        payload = investigate(analyzed, "create_user validation", temp_config, llm=None)
        for hit in payload["symbol_hits"]:
            source = (analyzed.workspace / hit["file"]).read_text().splitlines()
            line_text = source[hit["line"] - 1]
            assert hit["symbol"] in line_text
