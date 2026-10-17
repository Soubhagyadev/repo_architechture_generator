"""End-to-end analysis of a GitHub repo through the full pipeline (mocked HTTP)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import httpx
import pytest

from repo_analyzer.analyzer import RepoAnalyzer
from repo_analyzer.config import AppConfig
from repo_analyzer.github.client import GitHubClient

REPO_TAR_MEMBERS = {
    "demo-main/config.py": "DB_URL = 'sqlite://'\n",
    "demo-main/app.py": (
        "from config import DB_URL\n\n\n"
        "def run() -> str:\n"
        '    """Run the app."""\n'
        "    return DB_URL\n\n\n"
        "if __name__ == '__main__':\n"
        "    print(run())\n"
    ),
    "demo-main/README.md": "# demo\n",
    "demo-main/requirements.txt": "fastapi>=0.110\n",
}


def _tarball_bytes() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, text in REPO_TAR_MEMBERS.items():
            data = text.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _github_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == "https://api.github.com/repos/demo/demo":
        return httpx.Response(
            200,
            json={"full_name": "demo/demo", "default_branch": "main", "description": "demo repo"},
        )
    if "/commits/main" in url:
        return httpx.Response(200, json={"sha": "fedcba9876543210fedcba9876543210fedcba98"})
    if url.endswith("/languages"):
        return httpx.Response(200, json={"Python": 300})
    if url.startswith("https://codeload.github.com/"):
        return httpx.Response(200, content=_tarball_bytes())
    return httpx.Response(404, json={"message": "unexpected url " + url})


@pytest.fixture()
def analyzer(tmp_path: Path) -> RepoAnalyzer:
    config = AppConfig(cache={"root": str(tmp_path / "cache")})
    return RepoAnalyzer(
        config,
        client_factory=lambda: GitHubClient(
            config.github,
            client=httpx.Client(transport=httpx.MockTransport(_github_handler)),
        ),
    )


def test_github_analysis_end_to_end(analyzer: RepoAnalyzer, tmp_path: Path) -> None:
    result = analyzer.analyze("https://github.com/demo/demo")
    assert result.full_name == "demo/demo"
    assert result.commit_sha.startswith("fedcba987")
    paths = set(result.all_paths)
    assert {"config.py", "app.py", "README.md", "requirements.txt"} <= paths
    assert "app.py" in result.parsed
    symbols = {symbol.name for symbol in result.parsed["app.py"].symbols}
    assert "run" in symbols
    assert any(entry.path == "app.py" for entry in result.entry_points)
    assert "FastAPI" in result.frameworks
    assert result.languages[0][0] == "python"


def test_results_cached_across_runs(tmp_path: Path) -> None:
    config = AppConfig(cache={"root": str(tmp_path / "cache")})
    factory_count = {"n": 0}

    def factory():
        factory_count["n"] += 1
        return GitHubClient(
            config.github,
            client=httpx.Client(transport=httpx.MockTransport(_github_handler)),
        )

    with RepoAnalyzer(config, client_factory=factory) as analyzer:
        first = analyzer.analyze("https://github.com/demo/demo")
        second = analyzer.analyze("https://github.com/demo/demo")
        assert first.total_loc == second.total_loc
        assert factory_count["n"] == 1  # second run served fully from cache
