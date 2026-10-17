"""A minimal, rate-limit aware GitHub REST + codeload client."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from repo_analyzer.config import GitHubConfig
from repo_analyzer.errors import GitHubError
from repo_analyzer.github import urls
from repo_analyzer.github.urls import RepoSlug

_USER_AGENT = "repo-analyzer/0.1 (repository analysis tool)"
_MAX_RETRIES = 3


@dataclass(frozen=True)
class RepoMetadata:
    """Essential fields from ``GET /repos/{owner}/{repo}``."""

    full_name: str
    default_branch: str
    description: str = ""
    stars: int = 0
    language: str = ""
    size_kb: int = 0
    pushed_at: str = ""
    archived: bool = False
    forks: int = 0
    open_issues: int = 0

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "RepoMetadata":
        """Build metadata from a raw REST payload, tolerating missing fields."""
        return cls(
            full_name=str(payload.get("full_name") or ""),
            default_branch=str(payload.get("default_branch") or "HEAD"),
            description=str(payload.get("description") or ""),
            stars=int(payload.get("stargazers_count") or 0),
            language=str(payload.get("language") or ""),
            size_kb=int(payload.get("size") or 0),
            pushed_at=str(payload.get("pushed_at") or ""),
            archived=bool(payload.get("archived") or False),
            forks=int(payload.get("forks_count") or 0),
            open_issues=int(payload.get("open_issues_count") or 0),
        )


@dataclass(frozen=True)
class TreeEntry:
    """One entry from the recursive git tree."""

    path: str
    kind: str  # "blob" or "tree"
    size: int
    sha: str


class GitHubClient:
    """Synchronous GitHub client with token auth, backoff, and rate-limit waits."""

    def __init__(
        self,
        config: GitHubConfig | None = None,
        client: httpx.Client | None = None,
        token: str | None = None,
    ) -> None:
        self.config = config or GitHubConfig()
        self._token = token or os.environ.get(self.config.token_env, "")
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- core request plumbing -------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Issue a request with retry/backoff and rate-limit handling."""
        last_error: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(min(2**attempt, 8))
                continue
            if response.status_code in (403, 429):
                self._handle_rate_limit(response)
                continue
            if response.status_code >= 500:
                last_error = GitHubError(f"github 5xx ({response.status_code}) for {url}")
                time.sleep(min(2**attempt, 8))
                continue
            return response
        raise GitHubError(f"request failed after retries: {url} ({last_error})")

    def _handle_rate_limit(self, response: httpx.Response) -> None:
        """Sleep until the rate limit resets (bounded) or raise."""
        remaining = response.headers.get("x-ratelimit-remaining")
        if response.status_code == 403 and remaining not in (None, "0"):
            # Not a rate limit problem (e.g. forbidden resource); treat as fatal.
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                payload = {}
            message = str(payload.get("message") or response.text[:200])
            raise GitHubError(f"github rejected request: {message}")
        reset = response.headers.get("x-ratelimit-reset")
        retry_after = response.headers.get("retry-after")
        if retry_after:
            delay = min(float(retry_after), 60.0)
        elif reset:
            delay = min(max(float(reset) - time.time(), 1.0), 60.0)
        else:
            delay = 5.0
        time.sleep(delay)

    def _get_json(self, url: str) -> Any:
        response = self._request("GET", url)
        if response.status_code == 404:
            raise GitHubError(f"not found: {url}")
        if response.status_code != 200:
            raise GitHubError(f"github returned {response.status_code} for {url}")
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubError(f"invalid JSON from {url}") from exc

    # -- public API ------------------------------------------------------------

    def metadata(self, slug: RepoSlug) -> RepoMetadata:
        """Fetch repository metadata."""
        payload = self._get_json(urls.api_repo_url(slug, self.config.api_base))
        if not isinstance(payload, dict):
            raise GitHubError("unexpected metadata payload")
        return RepoMetadata.from_api(payload)

    def tree(self, slug: RepoSlug, sha: str) -> list[TreeEntry]:
        """Fetch the recursive tree for ``sha`` (handles truncation gracefully)."""
        payload = self._get_json(urls.api_tree_url(slug, sha, self.config.api_base))
        if not isinstance(payload, dict) or payload.get("truncated") is True:
            # Truncated trees still return partial data; we proceed with it.
            pass
        entries: list[TreeEntry] = []
        for item in payload.get("tree", []) if isinstance(payload, dict) else []:
            entries.append(
                TreeEntry(
                    path=str(item.get("path") or ""),
                    kind=str(item.get("type") or "blob"),
                    size=int(item.get("size") or 0),
                    sha=str(item.get("sha") or ""),
                )
            )
        return entries

    def languages(self, slug: RepoSlug) -> dict[str, int]:
        """Fetch byte counts per language."""
        payload = self._get_json(f"{urls.api_repo_url(slug, self.config.api_base)}/languages")
        if not isinstance(payload, dict):
            return {}
        return {str(key): int(value) for key, value in payload.items()}

    def tarball(self, slug: RepoSlug, ref: str) -> bytes:
        """Download the source tarball for ``ref`` (follows codeload redirect)."""
        response = self._request("GET", urls.tarball_url(slug, ref))
        if response.status_code not in (200, 302):
            raise GitHubError(f"tarball download failed with {response.status_code}")
        return response.content

    def branch_head(self, slug: RepoSlug, branch: str) -> str:
        """Resolve a branch name to its head commit SHA."""
        payload = self._get_json(
            f"{urls.api_repo_url(slug, self.config.api_base)}/commits/{branch}"
        )
        if isinstance(payload, dict) and isinstance(payload.get("sha"), str):
            return payload["sha"]
        raise GitHubError(f"could not resolve head for branch {branch!r}")

    def iter_paginated(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 20,
    ) -> Iterator[dict[str, Any]]:
        """Yield items from a paginated list endpoint."""
        params = dict(params or {})
        params.setdefault("per_page", self.config.per_page)
        page = 1
        while page <= max_pages:
            params["page"] = page
            response = self._request("GET", url, params=params)
            if response.status_code == 404:
                return
            if response.status_code != 200:
                raise GitHubError(f"github returned {response.status_code} for {url}")
            batch = response.json()
            if not isinstance(batch, list) or not batch:
                return
            yield from (item for item in batch if isinstance(item, dict))
            if len(batch) < int(params.get("per_page", self.config.per_page)):
                return
            page += 1


__all__ = ["GitHubClient", "RepoMetadata", "TreeEntry"]
