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


# ---------------------------------------------------------------- debt / deps

class DebtItem(BaseModel):
    """One tracked TODO/FIXME/HACK comment pinned to source."""

    model_config = ConfigDict(extra="forbid")

    file: str
    line: int = Field(ge=1)
    note: str = ""


class DebtTheme(BaseModel):
    """A thematic cluster of debt markers."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    items: list[DebtItem] = Field(default_factory=list)


class DebtOut(BaseModel):
    """Tech-debt narrative over found markers."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    themes: list[DebtTheme] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class DepUnused(BaseModel):
    """A dependency that appears declared but not imported."""

    model_config = ConfigDict(extra="forbid")

    name: str
    note: str = ""


class DepRisk(BaseModel):
    """A risk or inconsistency observed in dependency usage evidence."""

    model_config = ConfigDict(extra="forbid")

    name: str
    why: str


class DepReviewOut(BaseModel):
    """Dependency review narrative (usage-evidence grounded)."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    unused: list[DepUnused] = Field(default_factory=list)
    risky: list[DepRisk] = Field(default_factory=list)
    advice: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------- refactor

class RefactorTarget(BaseModel):
    """One proposed refactor target with a concrete plan."""

    model_config = ConfigDict(extra="forbid")

    name: str
    problem: str
    plan: list[str] = Field(default_factory=list)
    impact: str = Field(default="medium", pattern="^(low|medium|high)$")


class RefactorPlanOut(BaseModel):
    """Prioritized refactor plan."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    targets: list[RefactorTarget] = Field(default_factory=list)
    priorities: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------- security

class SecurityFinding(BaseModel):
    """One security-relevant spot found by deterministic scanning."""

    model_config = ConfigDict(extra="forbid")

    title: str
    severity: str = Field(default="low", pattern="^(low|medium|high|critical)$")
    file: str
    line: int = Field(ge=1)
    why: str = ""
    fix: str = ""


class SecurityOut(BaseModel):
    """Narrative wrap over the deterministic security scan."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    findings: list[SecurityFinding] = Field(default_factory=list)
    general_notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- api / review

class ApiSymbolDoc(BaseModel):
    """Description of one public symbol."""

    model_config = ConfigDict(extra="forbid")

    name: str
    signature: str = ""
    description: str


class ApiModuleDoc(BaseModel):
    """Role of one module plus descriptions of its public symbols."""

    model_config = ConfigDict(extra="forbid")

    module: str
    role: str
    symbols: list[ApiSymbolDoc] = Field(default_factory=list)


class ApiDocsOut(BaseModel):
    """Public-API reference narrative."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    modules: list[ApiModuleDoc] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    """A review comment pinned to a changed file."""

    model_config = ConfigDict(extra="forbid")

    file: str
    line: int | None = Field(default=None, ge=1)
    severity: str = Field(default="low", pattern="^(low|medium|high|critical)$")
    comment: str


class ReviewOut(BaseModel):
    """Narrative review of a commit range diff."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    commits_summary: str = ""
    findings: list[ReviewFinding] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)


# ------------------------------------------------------------- diagram notes

class DiagramNote(BaseModel):
    """Narrative explanation pinned to one deterministic mermaid diagram."""

    model_config = ConfigDict(extra="forbid")

    title: str
    explanation: str
    citations: list[Citation] = Field(default_factory=list)


class DiagramNotesOut(BaseModel):
    """Explanations for each generated diagram."""

    model_config = ConfigDict(extra="forbid")

    notes: list[DiagramNote] = Field(default_factory=list)


def citation_lines(citation: Citation) -> str:
    """Render a citation as ``path:L-L`` text."""
    if citation.end_line and citation.end_line > citation.line:
        return f"{citation.file}:{citation.line}-{citation.end_line}"
    return f"{citation.file}:{citation.line}"


__all__ = [
    "ApiDocsOut",
    "ApiModuleDoc",
    "ApiSymbolDoc",
    "ArchitectureOut",
    "BugInvestigationOut",
    "Citation",
    "CodeSummaryOut",
    "ComponentInfo",
    "DataFlow",
    "DataFlowStep",
    "DebtItem",
    "DebtOut",
    "DebtTheme",
    "DepReviewOut",
    "DepRisk",
    "DepUnused",
    "DiagramNote",
    "DiagramNotesOut",
    "DirectoryPurpose",
    "DocsOut",
    "Hypothesis",
    "OverviewOut",
    "QAOut",
    "RefactorPlanOut",
    "RefactorTarget",
    "ReviewFinding",
    "ReviewOut",
    "SecurityFinding",
    "SecurityOut",
    "TechChoice",
    "TechItem",
    "citation_lines",
]
