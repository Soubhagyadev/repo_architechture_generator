"""Repository Q&A: retrieval-grounded answers with file:line citations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.config import AppConfig
from repo_analyzer.llm.schemas import Citation, QAOut
from repo_analyzer.llm.service import LLMService
from repo_analyzer.features.context import build_pack, ranked_paths, sanitize_for_prompt, tokenize

_QA_SYSTEM = """You answer questions about a codebase using ONLY the supplied source excerpts.
Rules:
- Every claim about code must cite the exact source with `file` and `line` matching the numbered excerpts provided (format "12| code").
- If the excerpts do not contain the answer, say what is missing instead of guessing.
- Prefer 2-6 citations that pinpoint the relevant lines.
- Output only JSON matching: {"answer": str, "citations": [{"file": str, "line": int, "end_line": int | null, "note": str}], "confidence": "low"|"medium"|"high"}
"""


@dataclass
class Chunk:
    """A retrieval unit: one file section against the query."""

    path: str
    start_line: int
    text: str
    score: float = 0.0


def _bm25lite(query_tokens: set[str], doc_tokens: list[str], k1: float = 1.5, b: float = 0.75, avg_len: float = 120.0) -> float:
    """Compact BM25 approximation over token lists."""
    if not doc_tokens:
        return 0.0
    doc_len = len(doc_tokens)
    length_norm = 1 - b + b * (doc_len / (avg_len or doc_len))
    counts: dict[str, int] = {}
    for token in doc_tokens:
        counts[token] = counts.get(token, 0) + 1
    score = 0.0
    for token in query_tokens:
        count = counts.get(token, 0)
        if count:
            tf = count / doc_len
            score += tf * (k1 + 1) / (tf + k1 * length_norm)
    return score


def retrieve_chunks(
    result: AnalyzedRepository,
    question: str,
    *,
    max_files: int = 12,
    max_chunks: int = 24,
) -> list[Chunk]:
    """Rank file sections and symbol docstrings against the question."""
    query_tokens = tokenize(question)
    explicit_paths = {
        match.group(0)
        for match in re.finditer(r"[\w\-./]+\.[A-Za-z0-9]{1,5}", question)
    }
    chunks: list[Chunk] = []
    candidate_paths = ranked_paths(
        result,
        question,
        limit=max_files,
        extra_paths=[path for path in explicit_paths if path in result.parsed],
    )
    for path in candidate_paths:
        abs_path = result.workspace / path
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        parsed = result.parsed.get(path)
        boundaries = sorted({symbol.start_line - 2 for symbol in parsed.symbols} | {0}) if parsed else [0]
        boundaries = [line for line in boundaries if 0 <= line < len(lines)]
        for offset, start0 in enumerate(boundaries):
            end0 = boundaries[offset + 1] if offset + 1 < len(boundaries) else len(lines)
            section = "\n".join(lines[start0:end0])
            if not section.strip():
                continue
            section_tokens = tokenize(section) | tokenize(path.lower())
            score = _bm25lite(query_tokens, list(section_tokens))
            if any(token in question.lower() for token in (path.lower().split("/")[-1],)):
                score += 0.4
            if score > 0:
                chunks.append(
                    Chunk(path=path, start_line=start0 + 1, text=section, score=score)
                )
        if not boundaries and lines:
            section_tokens = tokenize(text) | tokenize(path.lower())
            score = _bm25lite(query_tokens, list(section_tokens))
            if score > 0:
                chunks.append(Chunk(path=path, start_line=1, text=text, score=score))
    chunks.sort(key=lambda chunk: -chunk.score)
    return chunks[:max_chunks]


@dataclass
class Answer:
    """Final Q&A output (LLM-grounded or deterministic fallback)."""

    question: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    confidence: str = "medium"
    used_llm: bool = False
    sources_consulted: list[str] = field(default_factory=list)


def ask(
    result: AnalyzedRepository,
    question: str,
    config: AppConfig,
    llm: LLMService | None = None,
) -> Answer:
    """Answer a question about the repository, grounded in source."""
    chunks = retrieve_chunks(result, question, max_files=config.analysis.max_qa_files)
    sources = sorted({chunk.path for chunk in chunks})
    if not chunks:
        return Answer(
            question=question,
            answer=(
                "I could not locate anything in the analyzed files matching that question. "
                "No source excerpt scored above zero for the query terms."
            ),
            confidence="low",
            used_llm=False,
            sources_consulted=[],
        )
    context = _render_chunks(result, chunks, budget=9_000)
    if llm is not None:
        try:
            out, _provider = llm.generate(QAOut, _QA_SYSTEM, context + f"\n\nQuestion: {question}", max_output_tokens=1200)
        except Exception:
            out = None
        if out is not None:
            locs = result.loc_for
            known = set(result.parsed) | set(result.all_paths)
            valid: list[Citation] = []
            for citation in out.citations:
                if citation.file not in known:
                    continue
                if citation.line > locs(citation.file):
                    continue
                if citation.end_line and citation.end_line > locs(citation.file):
                    citation.end_line = locs(citation.file)
                valid.append(citation)
            out.citations = valid
            return Answer(
                question=question,
                answer=out.answer,
                citations=out.citations,
                confidence=out.confidence,
                used_llm=True,
                sources_consulted=sources,
            )
    # Deterministic fallback: quote the best chunk verbatim.
    best = chunks[0]
    snippet_lines = best.text.splitlines()[:12]
    numbered = "\n".join(f"{best.start_line + offset:4d}| {line}" for offset, line in enumerate(snippet_lines))
    answer_text = (
        "No LLM provider is configured, so here are the closest code locations:\n"
        f"- {best.path}:{best.start_line}+ (relevance {best.score:.2f})\n\n```\n{numbered}\n```"
    )
    citation = Citation(
        file=best.path,
        line=best.start_line,
        end_line=best.start_line + len(snippet_lines) - 1,
        note="top retrieval hit",
    )
    return Answer(
        question=question,
        answer=answer_text,
        citations=[citation],
        confidence="low",
        used_llm=False,
        sources_consulted=sources,
    )


def _render_chunks(result: AnalyzedRepository, chunks: list[Chunk], budget: int) -> str:
    """Join chunk excerpts with exact line numbers within a char budget."""
    parts: list[str] = []
    used = 0
    for chunk in chunks:
        limit = min(40, max(8, len(chunk.text.splitlines())))
        lines = chunk.text.splitlines()[:limit]
        rendered_lines = [
            f"{chunk.start_line + offset:4d}| {line}" for offset, line in enumerate(lines)
        ]
        rendered = "## " + chunk.path + "\n" + "\n".join(rendered_lines)
        if used + len(rendered) > budget:
            remaining = budget - used
            if remaining < 400:
                break
            rendered = rendered[:remaining]
        parts.append(rendered)
        used += len(rendered)
    return sanitize_for_prompt("\n\n".join(parts))


__all__ = ["Answer", "Chunk", "ask", "retrieve_chunks"]
