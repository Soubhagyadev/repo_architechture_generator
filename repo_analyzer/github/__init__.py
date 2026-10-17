"""Public GitHub client surface."""

from repo_analyzer.github.client import GitHubClient, RepoMetadata, TreeEntry
from repo_analyzer.github.urls import RepoSlug, parse_target

__all__ = ["GitHubClient", "RepoMetadata", "RepoSlug", "TreeEntry", "parse_target"]
