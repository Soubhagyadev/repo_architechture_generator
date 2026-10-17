"""Contribution assistant: investigates how a change or bug ripples the code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.config import AppConfig
from repo_analyzer.features.context import tokenize
from repo_analyzer.llm.schemas import BugInvestigationOut, Citation
from repo_analyzer.llm.service import LLMService

_BUG_SYSTEM = """You are helping a developer investigate a bug or change in a repository using the supplied evidence.
Rules:
- Cite exact files and line numbers from the evidence; never invent files.
- `affected_paths` must trace reachable code paths (entry -> ... -> suspect) using files that appear in the evidence.
- Only list test files that the file catalog actually shows.
- Output only JSON matching: {"hypotheses": [{"title": str, "description": str, "files": [{"file": str, "line": int}]}], "affected_paths": [{"description": str, "file": str, "line": int | null}], "suggested_tests": [{"file": str, "line": int | null}], "next_steps": [str]}
"""


@dataclass
class TraceNode:
    """A file reached by tracing the dependency graph from suspects."""

    path: str
    distance: int
    direction: str  # upstream | downstream | direct


@dataclass
class Investigation:
    """Deterministic bug-investigation payload."""

    report: str
    suspects: list[str] = field(default_factory=list)
    trace: list[TraceNode] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    test_frameworks: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """Serialize for caching."""
        return {
            "report": self.report,
            "suspects": self.suspects,
            "trace": [
                {"path": node.path, "distance": node.distance, "direction": node.direction}
                for node in self.trace
            ],
            "test_files": self.test_files,
            "test_frameworks": self.test_frameworks,
            "references": self.references,
        }


def _score_file(
    result: AnalyzedRepository,
    query_tokens: set[str],
    path: str,
    call_index: dict[str, dict[str, int]],
) -> float:
    """Score one file's fit to the report text."""
    parsed = result.parsed.get(path)
    tokens = tokenize(path)
    signature_hits = 0.0
    if parsed:
        for symbol in parsed.symbols:
            if symbol.name in query_tokens or symbol.name.lower() in {token.lower() for token in query_tokens}:
                signature_hits += 1.0
            tokens.update(tokenize(symbol.signature))
            tokens.update(tokenize(symbol.doc))
            for callee in symbol.calls:
                tokens.update(tokenize(callee))
    direct_hit = len(query_tokens & tokens)
    reverse_call = 0.0
    for token in query_tokens:
        if token in call_index and path in call_index[token]:
            reverse_call += 1.0
    return direct_hit * 0.2 + signature_hits + reverse_call * 1.5


def trace_impact(
    result: AnalyzedRepository,
    suspects: list[str],
    *,
    depth: int = 2,
    limit: int = 12,
) -> list[TraceNode]:
    """BFS the import graph in both directions from the suspect files."""
    nodes: list[TraceNode] = []
    seen: set[str] = set()
    for path in suspects:
        if path not in seen:
            nodes.append(TraceNode(path=path, distance=0, direction="direct"))
            seen.add(path)
    for path in suspects[:4]:
        for reached, distance in sorted(
            result.graph.upstream(path, depth).items(), key=lambda item: item[1]
        ):
            if reached not in seen:
                nodes.append(TraceNode(path=reached, distance=distance, direction="upstream"))
                seen.add(reached)
        for reached, distance in sorted(
            result.graph.downstream(path, depth).items(), key=lambda item: item[1]
        ):
            if reached not in seen:
                nodes.append(TraceNode(path=reached, distance=distance, direction="downstream"))
                seen.add(reached)
    return nodes[:limit]


def find_test_files(result: AnalyzedRepository) -> tuple[list[str], list[str]]:
    """Locate test files and guess the test framework from markers."""
    test_files: list[str] = []
    for path in result.all_paths:
        name = path.rsplit("/", 1)[-1].lower()
        segments = {segment.lower() for segment in path.split("/")}
        if name.startswith("test_") or name.endswith("_test.py") or name.endswith("_test.go") or ".test." in name:
            test_files.append(path)
        elif "tests" in segments or "test" in segments:
            if any(path.endswith(ext) for ext in (".py", ".js", ".ts", ".go", ".java")):
                test_files.append(path)
    frameworks: list[str] = []
    all_tokens = set()
    for path in test_files[:20]:
        parsed = result.parsed.get(path)
        if parsed:
            for symbol in parsed.symbols:
                all_tokens.update(tokenize(symbol.signature))
            try:
                text = (result.workspace / path).read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                text = ""
        else:
            text = ""
        tokens = tokenize(text)
        all_tokens.update(tokens)
        if "pytest" in tokens or "fixture" in tokens:
            frameworks.append("pytest")
        if "unittest" in tokens or "TestCase" in text:
            frameworks.append("unittest")
        if "jest" in tokens or "describe(" in text:
            frameworks.append("jest")
        if "mocha" in tokens:
            frameworks.append("mocha")
        if "testing-library" in tokens:
            frameworks.append("testing-library")
    return sorted(set(test_files)), sorted(set(frameworks))


def investigate(
    result: AnalyzedRepository,
    report: str,
    config: AppConfig,
    llm: LLMService | None = None,
) -> dict[str, Any]:
    """Determine suspect files, affected paths, and tests for a bug report."""
    query_tokens = tokenize(report)
    call_index: dict[str, dict[str, int]] = {}
    for path, parsed in result.parsed.items():
        for symbol in parsed.symbols:
            for callee in symbol.calls:
                bucket = call_index.setdefault(callee.split(".")[-1], {})
                bucket[path] = bucket.get(path, 0) + 1

    scored = [
        (_score_file(result, query_tokens, path, call_index), path)
        for path in result.parsed
    ]
    scored.sort(key=lambda item: (-item[0], item[0]))
    suspects = [path for score, path in scored if score > 0][:8]
    if not suspects:
        # Fall back to centrality so there is always something concrete.
        suspects = [path for path, _score in result.graph.top_central(limit=5)]

    trace = trace_impact(result, suspects[:3])
    test_files, test_frameworks = find_test_files(result)

    symbol_hits = []
    for path in suspects[:4]:
        parsed = result.parsed.get(path)
        if not parsed:
            continue
        for symbol in parsed.symbols:
            if symbol.name.lower() in {token.lower() for token in query_tokens} or any(
                token in symbol.name.lower() for token in query_tokens
            ):
                symbol_hits.append(
                    {
                        "file": path,
                        "line": symbol.start_line,
                        "end_line": symbol.end_line,
                        "symbol": symbol.name,
                        "signature": symbol.signature,
                    }
                )

    # Related tests: file-name overlap plus coverage of imports from suspects.
    matching_tests: list[dict[str, Any]] = []
    suspect_stems = {
        re.sub(r"[^a-z0-9]", "", path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower())
        for path in suspects[:6]
    }
    for test_file in test_files[:40]:
        stem = re.sub(r"^test_", "", test_file.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower())
        stem = re.sub(r"[^a-z0-9]", "", stem)
        reason = ""
        if stem and stem in suspect_stems:
            reason = f"name matches suspect {stem}"
        else:
            parsed_test = result.parsed.get(test_file)
            if parsed_test:
                for imp in parsed_test.imports:
                    for path in suspects:
                        if path.rsplit("/", 1)[-1].rsplit(".", 1)[0] in imp.module:
                            reason = f"imports {imp.module}"
                            break
                if not reason:
                    for symbol in parsed_test.symbols:
                        if symbol.name.lower() in {token.lower() for token in query_tokens}:
                            reason = f"has test {symbol.name} that matches report"
                            break
        if reason:
            matching_tests.append({"file": test_file, "reason": reason})
        if len(matching_tests) >= 6:
            break

    payload: dict[str, Any] = {
        "report": report[:600],
        "suspects": suspects,
        "symbol_hits": symbol_hits[:12],
        "trace": [node.__dict__ for node in trace],
        "test_frameworks": test_frameworks,
        "related_tests": matching_tests,
        "test_catalog": test_files[:16],
    }

    if llm is None:
        payload["llm_enriched"] = False
        payload["plan"] = _deterministic_plan(payload, result)
        return payload

    from repo_analyzer.features.context import build_pack, sanitize_for_prompt

    pack_paths = suspects[:6] + [test["file"] for test in matching_tests[:3]]
    pack_paths += [node.path for node in trace[:4] if node.path not in pack_paths]
    pack = build_pack(
        result,
        pack_paths[:12],
        budget=14_000,
        focus_symbols={
            path: [hit["symbol"] for hit in symbol_hits if hit["file"] == path]
            for path in pack_paths
        },
    )
    evidence = (
        f"Repository: {result.full_name}\n"
        f"Bug report / change description: {report[:600]}\n\n"
        f"Ranked suspect files: {json.dumps(suspects)}\n"
        f"Matching symbols: {json.dumps(symbol_hits[:10])}\n"
        f"Graph reachability: {json.dumps([{'path': n.path, 'distance': n.distance, 'direction': n.direction} for n in trace])[:1800]}\n"
        f"Test files available: {json.dumps(test_files[:20])}\n"
        f"Matching tests: {json.dumps(matching_tests)}\n\n"
        "Source excerpts (line numbers exact):\n"
        + sanitize_for_prompt(pack.render())
    )
    try:
        out, _provider = llm.generate(
            BugInvestigationOut, _BUG_SYSTEM, evidence, max_output_tokens=2200
        )
    except Exception:
        payload["llm_enriched"] = False
        payload["plan"] = _deterministic_plan(payload, result)
        return payload

    locs = {path: result.loc_for(path) for path in result.all_paths}
    known = set(result.all_paths)

    def _clean_citations(citations: list[Citation]) -> list[Citation]:
        cleaned: list[Citation] = []
        for citation in citations:
            if citation.file not in known or citation.line > locs[citation.file]:
                continue
            if citation.end_line and citation.end_line > locs[citation.file]:
                citation.end_line = locs[citation.file]
            cleaned.append(citation)
        return cleaned

    for hypothesis in out.hypotheses:
        hypothesis.files = _clean_citations(hypothesis.files)
    out.hypotheses = [hyp for hyp in out.hypotheses if hyp.files]
    out.affected_paths = [step for step in out.affected_paths if not step.file or step.file in known]
    out.suggested_tests = [
        Citation(file=test.file, line=test.line or 1) for test in out.suggested_tests if test.file in known
    ]
    payload["llm_enriched"] = True
    payload["plan"] = out.model_dump()
    return payload


def _deterministic_plan(payload: dict[str, Any], result: AnalyzedRepository) -> dict[str, Any]:
    """No-LLM fallback plan built from the deterministic data."""
    steps = [
        f"Reproduce the report against {result.full_name} (ref {result.ref}).",
        "Inspect the suspect files listed below; start at the hit with the highest score.",
        "Run the matched test files to see which assertions fail.",
    ]
    if not payload["related_tests"]:
        steps.append("No test file matches by name; search the repository for callers of the suspect symbols.")
    hypotheses = [
        {
            "title": f"Behavior change likely in {path}",
            "files": [
                {
                    "file": hit["file"],
                    "line": hit["line"],
                    "end_line": hit["end_line"],
                    "note": hit["symbol"],
                }
                for hit in payload["symbol_hits"]
                if hit["file"] == path
            ]
            or [{"file": path, "line": 1, "end_line": None, "note": "entry candidate"}],
            "description": (
                "This file scored highest against the report text and is reachable in the module graph. "
                "Verify the cited symbols first."
            ),
        }
        for path in payload["suspects"][:3]
    ]
    return {
        "hypotheses": hypotheses,
        "affected_paths": [
            {
                "description": f"{node['direction']} of suspects (distance {node['distance']})",
                "file": node["path"],
                "line": 1,
            }
            for node in payload["trace"][:8]
        ],
        "suggested_tests": [
            {"file": test["file"], "line": None} for test in payload["related_tests"][:6]
        ]
        or [{"file": path, "line": None} for path in payload["test_catalog"][:4]],
        "next_steps": steps,
    }


__all__ = ["Investigation", "TraceNode", "find_test_files", "investigate", "trace_impact"]
