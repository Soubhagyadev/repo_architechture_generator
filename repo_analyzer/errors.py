"""Typed exception hierarchy for repository ingestion and analysis."""

from __future__ import annotations

__all__ = [
    "AnalyzerError",
    "ContributionError",
    "GitHubError",
    "IngestError",
    "LLMError",
    "ParseError",
    "QueriesError",
]


class AnalyzerError(Exception):
    """Base class for all analyzer errors."""


class GitHubError(AnalyzerError):
    """ Raised when the GitHub API request fails."""


class ArchiveError(AnalyzerError):
    """Raised when an archive cannot be safely be extracted."""

    def __init__(self, message: str):
        super().__init__(f"Archive error: {message}")


class IngestError(AnalyzerError):
    """Raised when repository files cannot be read or parsed."""


class ParseError(AnalyzerError):
    """Raised when source parsing fails."""


class ModuleFilterError(AnalyzerError):
    """Raised when no module can be selected for analysis."""


class QueriesError(AnalyzerError):
    """Raised when Q&A retrieval cannot produce an answer context."""


class LLMError(AnalyzerError):
    """Raised when LLM requests fail."""


class ContributionError(AnalyzerError):
    """Raised when contribution analysis cannot be performed."""


class ModuleSelectionError(AnalyzerError):
    """Raised when no module could be selected for analysis."""
