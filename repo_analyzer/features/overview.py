"""Repository overview: inventory, tech stack, entry points, and flows."""

from __future__ import annotations

import json
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository, diagrams_for
from repo_analyzer.llm.schemas import OverviewOut
from repo_analyzer.llm.service import LLMService

_OVERVIEW_SYSTEM = """You are a senior software architect summarizing a repository for a new engineer.
You describe only what the provided evidence supports. Requirements:
- Ground every tech_stack entry and key file in the supplied files, with `file` and `line` citations.
- `headline` is <= 90 chars; `summary` is 2-4 sentences in plain, concrete English.
- Do not invent technologies that are absent from the evidence.
- Output only JSON matching: {"headline": str, "summary": str, "tech_stack": [{"name": str, "category": str, "evidence": [{"file": str, "line": int}]}], "key_files": [{"file": str, "line": int, "note": str}], "gotchas": [str]}
"""


def deterministic_overview(result: AnalyzedRepository) -> dict[str, Any]:
    """Build the grounded, no-LLM overview payload."""
    languages = [
        {"language": language, "files": files, "loc": loc}
        for language, files, loc in result.languages
        if language not in {"docs-markdown", "docs-text"}
    ]
    ecosystems = result.dependencies.ecosystems
    dep_names = {name.split("==")[0].split(">")[0].split("<")[0] for names in ecosystems.values() for name in names}
    top_files = sorted(
        result.graph.top_central(limit=8),
        key=lambda item: -item[1],
    )
    components = [
        {"name": comp.name, "files": len(comp.files)}
        for comp in result.components
    ]
    entry_files = [
        {"path": entry.path, "kind": entry.kind, "confidence": round(entry.confidence, 2)}
        for entry in result.entry_points
    ]
    manifests = result.dependencies.manifests
    config_files = result.config_paths[:10]
    ci_files = result.dependencies.ci_files[:6]
    frameworks = result.frameworks
    diagrams = [diagram.to_json() for diagram in diagrams_for(result)]
    flows = [flow.to_json() for flow in result.flows]
    stats = {
        "files": result.file_count,
        "total_loc": result.total_loc,
        "parse_errors": result.parse_errors,
        "languages": languages,
        "manifests": manifests,
        "config_files": config_files,
        "ci_files": ci_files,
        "dependency_count": len(dep_names),
        "dependency_sample": sorted(dep_names)[:20],
        "frameworks": frameworks,
        "top_files": [{"path": path, "degree": degree} for path, degree in top_files],
        "components": components,
        "entry_points": entry_files,
        "flows": flows,
        "cycles": [c for c in result.graph.cycles(limit=3)],
        "diagrams": diagrams,
    }
    summary_bits = [
        f"{result.file_count} files, {result.total_loc} LOC",
    ]
    if languages:
        primary = languages[0]
        summary_bits.append(f"primarily {primary['language']} ({primary['loc']} LOc)" if primary["loc"] else f"primarily {primary['language']}")
    if frameworks:
        summary_bits.append("frameworks: " + ", ".join(frameworks[:5]))
    return {
        "headline": f"{result.full_name}",
        "stats": stats,
        "summary_text": " | ".join(summary_bits),
        "cycles": stats["cycles"],
        "entry_points": entry_files,
    }


def llm_overview(
    result: AnalyzedRepository,
    llm: LLMService | None,
    overview_payload: dict[str, Any] | None = None,
) -> OverviewOut | None:
    """Enrich the deterministic overview with an LLM narrative (optional)."""
    from repo_analyzer.features.context import build_pack, ranked_paths, sanitize_for_prompt

    if llm is None:
        return None
    payload = overview_payload or deterministic_overview(result)
    stats = payload["stats"]
    entry_paths = [entry["path"] for entry in stats["entry_points"]]
    top_paths = [item["path"] for item in stats["top_files"]]
    ranked = ranked_paths(result, " ".join(result.frameworks + [result.primary_language()]), limit=18, extra_paths=entry_paths + top_paths)
    pack = build_pack(result, ranked[:12], budget=13_000, whole_files=entry_paths[:2])
    file_catalog = "\n".join(f"- {path}" for path in ranked)
    evidence = (
        f"Repository: {result.full_name}\n"
        f"Description: {result.description or '(none)'}\n"
        f"Languages (files, LOC): {json.dumps(stats['languages'][:8])}\n"
        f"Dependencies: {json.dumps(stats['dependency_sample'])}\n"
        f"Entry points: {json.dumps(entry_paths)}\n"
        f"Component folders: {json.dumps([comp.name for comp in result.components])}\n\n"
        "File catalog:\n" + file_catalog + "\n\n"
        "Key source excerpts (line numbers are exact):\n" + sanitize_for_prompt(pack.render())
    )
    if llm is None:
        return None
    try:
        out, _provider = llm.generate(
            OverviewOut,
            _OVERVIEW_SYSTEM,
            evidence,
            max_output_tokens=1800,
        )
    except Exception:
        return None
    # Validate citations to files we actually analyzed.
    known = set(result.parsed) | set(result.all_paths)
    out.tech_stack = [
        item for item in out.tech_stack
        if all(citation.file in known for citation in item.evidence)
    ]
    out.key_files = [citation for citation in out.key_files if citation.file in known]
    return out


__all__ = ["deterministic_overview", "llm_overview"]
