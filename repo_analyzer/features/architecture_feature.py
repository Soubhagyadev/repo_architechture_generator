"""Architecture feature: deterministic diagrams plus optional LLM narrative."""

from __future__ import annotations

import json
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository, diagrams_for
from repo_analyzer.llm.schemas import ArchitectureOut
from repo_analyzer.llm.service import LLMService

_ARCH_SYSTEM = """You are a software architect producing an architecture write-up for an unfamiliar engineer.
Only describe components and data flows that the provided evidence supports.
Requirements:
- Every file citation must use the exact `path:line` numbers shown in the evidence.
- `data_flows` should describe realistic end-to-end paths (request -> handler -> service -> storage), naming files and line numbers from the evidence.
- If the evidence is thin, say so and keep the answer short.
- Output only JSON matching: {"summary": str, "components": [{"name": str, "description": str, "files": [{"file": str, "line": int}]}], "data_flows": [{"name": str, "trigger": str, "steps": [{"description": str, "file": str, "line": int | null}]}], "design_notes": [str]}
"""


def architecture_payload(result: AnalyzedRepository) -> dict[str, Any]:
    """Deterministic architecture data for reports and caching."""
    diagrams = [diagram.to_json() for diagram in diagrams_for(result)]
    flows = []
    for entry in result.entry_points[:3]:
        flow = next((f for f in result.flows if f.entry == entry.path), None)
        if flow is None:
            flow = _fresh_flow(result, entry.path)
        flows.append(
            {
                "name": f"execution flow: {entry.path}",
                "entry": entry.path,
                "steps": flow.steps,
            }
        )
    layers = _layer_table(result)
    return {
        "components": [component.to_json() for component in result.components],
        "diagrams": diagrams,
        "flows": flows,
        "fan_in_out": _fan_table(result),
        "layers": layers,
        "cycles": result.graph.cycles(limit=5),
    }


def _fresh_flow(result: AnalyzedRepository, entry: str) -> Any:
    """Trace an execution flow on demand."""
    from repo_analyzer.analysis.entry_points import trace_flow

    return trace_flow(result.graph, entry)


def _fan_table(result: AnalyzedRepository) -> list[dict[str, Any]]:
    """Top files by fan-in (imported a lot) as architecture hotspots."""
    fan_in = result.graph.fan_in()
    ranked = sorted(fan_in.items(), key=lambda item: (-item[1], item[0]))[:8]
    return [
        {
            "path": path,
            "imported_by": degree,
            "imports": len(result.graph.edges.get(path, ())),
        }
        for path, degree in ranked
        if degree > 0
    ]


def _layer_table(result: AnalyzedRepository) -> list[dict[str, Any]]:
    """Guess architecture layers via directory conventions."""
    markers = {
        "api": "api/transport layer",
        "routes": "api/transport layer",
        "views": "api/transport layer",
        "controllers": "api/transport layer",
        "handlers": "api/transport layer",
        "services": "service/business layer",
        "domain": "service/business layer",
        "usecases": "service/business layer",
        "core": "core domain",
        "db": "data access layer",
        "models": "data access layer",
        "repositories": "data access layer",
        "dao": "data access layer",
        "utils": "shared utilities",
        "helpers": "shared utilities",
        "common": "shared utilities",
        "config": "configuration",
        "settings": "configuration",
        "tests": "tests",
    }
    rows: list[dict[str, Any]] = []
    for component in result.components:
        lowered = component.name.lower()
        for token, layer in markers.items():
            if token in lowered.replace("::", "/").split("/"):
                rows.append({"component": component.name, "layer": layer, "files": len(component.files)})
                break
    return rows


def llm_architecture(result: AnalyzedRepository, llm: LLMService | None) -> ArchitectureOut | None:
    """Optional LLM-curated architecture narrative with citations."""
    from repo_analyzer.features.context import build_pack, ranked_paths, sanitize_for_prompt

    if llm is None:
        return None
    entry_paths = [entry.path for entry in result.entry_points[:3]]
    top_paths = [path for path, _score in result.graph.top_central(limit=10)]
    ranked = ranked_paths(
        result,
        " ".join(result.frameworks + ["architecture", "flow"] + entry_paths + top_paths),
        limit=16,
        extra_paths=top_paths,
    )
    pack = build_pack(result, ranked[:10], budget=15_000, whole_files=entry_paths[:1])
    evidence = (
        f"Repository: {result.full_name}\n"
        f"Component folders: {json.dumps([component.to_json() for component in result.components])[:1500]}\n"
        f"Import edges: {json.dumps({src: sorted(dsts) for src, dsts in result.graph.edges.items() if dsts})[:2600]}\n"
        f"Entry points: {json.dumps(entry_paths)}\n\n"
        "Key source excerpts (numbers are exact line numbers):\n"
        + sanitize_for_prompt(pack.render())
    )
    try:
        out, _provider = llm.generate(
            ArchitectureOut,
            _ARCH_SYSTEM,
            evidence,
            max_output_tokens=2600,
        )
    except Exception:
        return None
    known = set(result.all_paths)
    for component in out.components:
        component.files = [cit for cit in component.files if cit.file in known]
    out.components = [comp for comp in out.components if comp.files]
    out.data_flows = [
        flow for flow in out.data_flows
        if all(step.file in known for step in flow.steps)
    ]
    return out


__all__ = ["architecture_payload", "llm_architecture"]
