"""Tests for GitHub URL parsing and rate-limited client behavior."""

from __future__ import annotations

import httpx
import pytest

from repo_analyzer.errors import GitHubError
from repo_analyzer.github.client import GitHubClient
from repo_analyzer.github.urls import file_url, parse_target


def test_parse_full_url() -> None:
    slug = parse_target("https://github.com/owner/repo")
    assert slug is not None
    assert (slug.owner, slug.repo) == ("owner", "repo")


def test_parse_tree_url_with_ref_and_subpath() -> None:
    slug = parse_target("https://github.com/owner/repo/tree/v2/src/app")
    assert slug is not None
    assert slug.ref == "v2"
    assert slug.subpath == "src/app"


def test_parse_blob_url() -> None:
    slug = parse_target("https://github.com/owner/repo/blob/main/README.md")
    assert slug is not None
    assert slug.ref == "main"
    assert slug.subpath == "README.md"


def test_parse_shorthand() -> None:
    slug = parse_target("owner/repo")
    assert slug is not None
    assert (slug.owner, slug.repo) == ("owner", "repo")


def test_parse_git_suffix() -> None:
    slug = parse_target("https://github.com/owner/repo.git")
    assert slug is not None
    assert slug.repo == "repo"


def test_reject_non_github_hosts() -> None:
    assert parse_target("https://gitlab.com/owner/repo") is None
    assert parse_target("https://example.com/foo/bar") is None


def test_reject_non_repo_pages() -> None:
    assert parse_target("https://github.com/owner/repo/issues") is None


@pytest.fixture()
def client() -> GitHubClient:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"full_name": "o/r", "default_branch": "main", "stargazers_count": 5},
        )
    )
    return GitHubClient(client=httpx.Client(transport=transport))


def test_metadata_parses_payload(client: GitHubClient) -> None:
    slug = parse_target("o/r")
    assert slug is not None
    meta = client.metadata(slug)
    assert meta.full_name == "o/r"
    assert meta.default_branch == "main"
    assert meta.stars == 5


def test_rate_limit_retry_sleeps_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("repo_analyzer.github.client.time.sleep", sleeps.append)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"},
                json={},
            )
        return httpx.Response(200, json={"full_name": "o/r", "default_branch": "dev"})

    client = GitHubClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    slug = parse_target("o/r")
    assert slug is not None
    meta = client.metadata(slug)
    assert meta.default_branch == "dev"
    assert sleeps, "expected a rate-limit backoff sleep"


def test_403_not_rate_limit_raises(client: None = None) -> None:
    handler = lambda request: httpx.Response(403, json={"message": "forbidden resource"})  # noqa: E731
    bad = GitHubClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    slug = parse_target("o/r")
    assert slug is not None
    with pytest.raises(GitHubError):
        bad.metadata(slug)


def test_404_raises_github_error(client: None = None) -> None:
    handler = lambda request: httpx.Response(404, json={"message": "Not Found"})  # noqa: E731
    missing = GitHubClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    slug = parse_target("o/r")
    assert slug is not None
    with pytest.raises(GitHubError):
        missing.metadata(slug)


def test_file_url_line_anchor() -> None:
    slug = parse_target("o/r")
    assert slug is not None
    url = file_url(slug, "src/app.py", "main", 10, 12)
    assert url.endswith("blob/main/src/app.py#L10-L12")
