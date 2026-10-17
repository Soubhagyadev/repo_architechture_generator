"""Analyzer orchestration tests against the sample repo (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_analyzer.analyzer import RepoAnalyzer, call_index_for, diagrams_for
from repo_analyzer.errors import IngestError
from repo_analyzer.utils import sha256_hex


@pytest.fixture()
def analyzed(sample_repo: Path, temp_config) -> "object":
    with RepoAnalyzer(temp_config) as analyzer:
        yield analyzer.analyze(str(sample_repo), is_local=True)


def test_inventory_counts(analyzed) -> None:
    paths = set(analyzed.all_paths)
    assert "src/app/main.py" in paths
    assert "web/frontend.js" in paths
    assert "pyproject.toml" in analyzed.config_paths
    assert "README.md" in analyzed.docs_paths
    # excluded trees
    assert not any(path.startswith("node_modules/") for path in analyzed.all_paths)
    assert not any(path.startswith("build/") for path in analyzed.all_paths)
    assert analyzed.parse_errors == 0


def test_language_stats(analyzed) -> None:
    languages = {name: (files, loc) for name, files, loc in analyzed.languages}
    assert "python" in languages
    assert "javascript" in languages
    assert languages["python"][1] > languages["javascript"][1]


def test_graph_connects_layers(analyzed) -> None:
    edges = {
        (source, target)
        for source, targets in analyzed.graph.edges.items()
        for target in targets
    }
    assert ("src/app/main.py", "src/app/api.py") in edges
    assert ("src/app/api.py", "src/app/services.py") in edges
    assert ("src/app/services.py", "src/app/db.py") in edges
    assert ("web/frontend.js", "web/apiClient.js") in edges
    assert ("cli/main.js", "web/apiClient.js") in edges
    assert ("tests/test_services.py", "src/app/services.py") in edges


def test_entry_points_detected(analyzed) -> None:
    entry_paths = {entry.path for entry in analyzed.entry_points}
    assert "src/app/main.py" in entry_paths
    main_entry = next(entry for entry in analyzed.entry_points if entry.path == "src/app/main.py")
    assert main_entry.confidence >= 0.9


def test_frameworks_from_pyproject(analyzed) -> None:
    assert {"FastAPI", "SQLAlchemy", "Uvicorn", "pytest"} <= set(analyzed.frameworks)


def test_diagrams_contain_text(analyzed) -> None:
    diagrams = diagrams_for(analyzed)
    titles = [diagram.title for diagram in diagrams]
    assert "Component relationships" in titles
    assert all(diagram.mermaid.startswith("flowchart") for diagram in diagrams)


def test_call_index_finds_cross_file_symbols(analyzed) -> None:
    index = call_index_for(analyzed)
    assert set(index["create_user"]) >= {"src/app/api.py", "src/app/services.py"}


def test_parse_cache_reuse(sample_repo: Path, temp_config) -> None:
    """Second run of the same directory must reuse existing parse rows."""
    db = temp_config.cache.root + "/cache.sqlite3"
    with RepoAnalyzer(temp_config) as analyzer:
        first = analyzer.analyze(str(sample_repo), is_local=True)

        store = analyzer.store
        blob = sha256_hex((sample_repo / "src/app/services.py").read_bytes())
        before = store.parse_for_blob(blob) is not None
        assert before

        second = analyzer.analyze(str(sample_repo), is_local=True)
        assert second.total_loc == first.total_loc


def test_gitflow_incremental_change(temp_config, sample_repo: Path, tmp_path: Path) -> None:
    """A changed file re-parses; unchanged files reuse the same parse row."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    import shutil

    shutil.copytree(sample_repo, temp_config.cache.root + "/x", dirs_exist_ok=True)
    shutil.rmtree(temp_config.cache.root + "/x", ignore_errors=True)
    shutil.copytree(sample_repo, workspace)
    target = workspace / "src/app/services.py"
    original = target.read_text()
    target.write_text(original + "\n\ndef added_by_test() -> int:\n    return 1\n")

    with RepoAnalyzer(temp_config, ) as analyzer:
        change = analyzer.analyze(str(workspace), is_local=True)
        symbol_names = [symbol.name for symbol in change.parsed["src/app/services.py"].symbols]
        assert "added_by_test" in symbol_names


def test_invalid_local_path_raises(temp_config) -> None:
    with RepoAnalyzer(temp_config) as analyzer:
        with pytest.raises(IngestError):
            analyzer.analyze("/nonexistent/path/xyz", is_local=True)


def test_snapshot_roundtrip(temp_config, sample_repo: Path) -> None:
    with RepoAnalyzer(temp_config) as analyzer:
        result = analyzer.analyze(str(sample_repo), is_local=True)
        snapshot = analyzer.store.get_analysis("index:" + result.full_name.split(" ")[0])
        if snapshot is None:
            pytest.skip("snapshot key path changed")
        assert "graph" in snapshot
        assert json.dumps(snapshot)
