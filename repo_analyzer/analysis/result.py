"""Serializable result of repository analysis shared by all features."""

from __future__ import annotations

from pydantic import BaseModel, Field

from repo_analyzer.analysis.architecture import Component, Diagram
from repo_analyzer.analysis.dependencies import DependencyInfo
from repo_analyzer.analysis.entry_points import ExecutionFlow

__all__ = ["AnalysisResult", "FileStat", "LanguageStat", "RepoIndex"]


class FileStat(BaseModel):
    """Per-file inventory entry."""

    path: str
    language: str
    loc: int = 0
    size: int = 0
    parse_error: str = ""
    entry_flags: list[str] = Field(default_factory=list)


class LanguageStat(BaseModel):
    """Aggregate per-language counters."""

    language: str
    files: int
    loc: int


class RepoIndex(BaseModel):
    """Everything deterministic analysis produces for one commit."""

    full_name: str
    ref: str
    commit_sha: str = ""
    description: str = ""
    source: str = "github"  # github | local
    files: list[FileStat] = Field(default_factory=list)
    languages: list[LanguageStat] = Field(default_factory=list)
    dependencies: DependencyInfo
    graph: dict[str, list[str]] = Field(default_factory=dict)
    components: list[Component] = Field(default_factory=list)
    entry_points: list[FileStat] = Field(default_factory=list)
    flows: list[ExecutionFlow] = Field(default_factory=list)
    diagrams: list[Diagram] = Field(default_factory=list)
    total_loc: int = 0
    file_count: int = 0
    parsed_count: int = 0
    parse_errors: int = 0

    def primary_language(self) -> str:
        """Name of the dominant language by LOC."""
        if not self.languages:
            return "unknown"
        return self.languages[0].language


class AnalysisResult(BaseModel):
    """Top-level payload cached per repository/commit."""

    index: RepoIndex
    provider_provenance: dict[str, str] = Field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict."""
        return self.model_dump()
