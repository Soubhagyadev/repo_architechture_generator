"""Pydantic schemas validated against LLM responses for each feature."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Citation(BaseModel):
    """A source reference that must exist in the analyzed repository."""

    model_config = ConfigDict(extra="forbid")

    file: str
    line: int = Field(ge=1)
    end_line: int | None = Field(default=None, ge=1)
    note: str = ""

    @field_validator("file")
    @classmethod
    def _clean_path(cls, value: str) -> str:
        cleaned = value.strip().lstrip("./")
        if not cleaned or "\\" in cleaned or cleaned.endswith("/"):
            raise ValueError("file must be a POSIX repository path")
        return cleaned


class TechItem(BaseModel):
    """A named technology with evidence inside the repository."""

    model_config = ConfigDict(extra="forbid")

    name: str
    category: str
    evidence: list[Citation] = Field(default_factory=list)


class OverviewOut(BaseModel):
    """Overview summary of what the repository is and how it is built."""

    model_config = ConfigDict(extra="forbid")

    headline: str
    summary: str
    tech_stack: list[TechItem] = Field(default_factory=list)
    key_files: list[Citation] = Field(default_factory=list)
    gotchas: list[str] = Field(default_factory=list)


class ComponentInfo(BaseModel):
    """An architectural component with the files implementing it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    files: list[Citation] = Field(default_factory=list)


class DataFlowStep(BaseModel):
    """One hop in a data flow, pinned to source when possible."""

    model_config = ConfigDict(extra="forbid")

    description: str
    file: str
    line: int | None = None


class DataFlow(BaseModel):
    """An end-to-end path data takes through the system."""

    model_config = ConfigDict(extra="forbid")

    name: str
    trigger: str
    steps: list[DataFlowStep] = Field(default_factory=list)


class ArchitectureOut(BaseModel):
    """LLM-curated architecture narrative layered over deterministic diagrams."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    components: list[ComponentInfo] = Field(default_factory=list)
    data_flows: list[DataFlow] = Field(default_factory=list)
    design_notes: list[str] = Field(default_factory=list)


class DirectoryPurpose(BaseModel):
    """Explains one directory's responsibility."""

    model_config = ConfigDict(extra="forbid")

    path: str
    purpose: str
    important_files: list[Citation] = Field(default_factory=list)


class TechChoice(BaseModel):
    """A technology decision with rationale and trade-offs."""

    model_config = ConfigDict(extra="forbid")

    technology: str
    rationale: str
    trade_offs: str = ""


class DocsOut(BaseModel):
    """Generated codebase documentation."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    directories: list[DirectoryPurpose] = Field(default_factory=list)
    tech_choices: list[TechChoice] = Field(default_factory=list)
    execution_flow: list[DataFlowStep] = Field(default_factory=list)


class QAOut(BaseModel):
    """Grounded answer to a repository question."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: str = Field(default="medium", pattern="^(low|medium|high)$")


class Hypothesis(BaseModel):
    """A candidate root cause for a reported bug."""

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    files: list[Citation] = Field(default_factory=list)


class BugInvestigationOut(BaseModel):
    """Structured bug-investigation plan."""

    model_config = ConfigDict(extra="forbid")

    hypotheses: list[Hypothesis] = Field(default_factory=list)
    affected_paths: list[DataFlowStep] = Field(default_factory=list)
    suggested_tests: list[Citation] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class CodeSummaryOut(BaseModel):
    """Compact description of a function or file behavior."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    behaves_like: str = ""


def citation_lines(citation: Citation) -> str:
    """Render a citation as ``path:L-L`` text."""
    if citation.end_line and citation.end_line > citation.line:
        return f"{citation.file}:{citation.line}-{citation.end_line}"
    return f"{citation.file}:{citation.line}"


__all__ = [
    "ArchitectureOut",
    "BugInvestigationOut",
    "Citation",
    "CodeSummaryOut",
    "ComponentInfo",
    "DataFlow",
    "DataFlowStep",
    "DirectoryPurpose",
    "DocsOut",
    "Hypothesis",
    "OverviewOut",
    "QAOut",
    "TechChoice",
    "TechItem",
    "citation_lines",
]
