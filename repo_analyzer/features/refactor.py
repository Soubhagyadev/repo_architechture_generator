"""Refactor planning: deterministic hotspots (size/fan/cycles) plus LLM plan."""

from __future__ import annotations

import json
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.llm.schemas import RefactorPlanOut, RefactorTarget
from repo_analyzer.llm.service import LLMService

_GOD_FILE_LOC = 300
_GOD_FUNC_LINES = 80
_GOD_CLASS_METHODS = 12
_HIGH_FAN_OUT = 10

_REFACTOR_SYSTEM = """You propose a refactor plan from the supplied deterministic hotspots of a repository.
Rules:
- Targets must be real files/symbols from the evidence (use exact file paths; for symbols include file:line).
- `plan` entries are concrete, ordered steps (<= 6 per target).
- Never claim to have found new hotspots; only elaborate on the supplied ones.
- Output only JSON matching: {"summary": str, "targets": [{"name": str, "problem": str, "plan": [str], "impact": "low"|"medium"|"high"}], "priorities": [str]}
"""


def hotspots(result: AnalyzedRepository) -> dict[str, list[dict[str, Any]]]:
    """Deterministic refactor hotspot inventory (generated/lock files excluded)."""
    skip = {
        path
        for path in result.file_stats
        if path.rsplit("/", 1)[-1]
        in {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "Pipfile.lock", "composer.lock", "flake.lock"}
        or path.rsplit("/", 1)[-1].startswith((".min.", "__init__.py"))
        or any(part in {"vendor", "dist", "build", "__pycache__", "node_modules"} for part in path.split("/")[:-1])
    }
    god_files = [
        {
            "file": path,
            "loc": loc,
            "symbols": len(result.parsed[path].symbols) if path in result.parsed else 0,
        }
        for path, loc in sorted(result.file_stats.items(), key=lambda item: -item[1])
        if loc >= _GOD_FILE_LOC and path not in skip
    ][:8]

    god_symbols: list[dict[str, Any]] = []
    for path, parsed in result.parsed.items():
        if path in skip:
            continue
        for symbol in parsed.symbols:
            size = symbol.end_line - symbol.start_line + 1
            if symbol.kind in {"function", "method"} and size >= _GOD_FUNC_LINES:
                god_symbols.append(
                    {"ref": f"{path}:{symbol.start_line}", "name": symbol.name, "kind": symbol.kind, "lines": size}
                )
            elif symbol.kind == "class":
                methods = sum(1 for other in parsed.symbols if other.kind == "method" and other.path == path and other.start_line >= symbol.start_line and other.end_line <= symbol.end_line)
                if methods >= _GOD_CLASS_METHODS:
                    god_symbols.append(
                        {"ref": f"{path}:{symbol.start_line}", "name": symbol.name, "kind": "class", "lines": size}
                    )
    god_symbols = god_symbols[:10]

    fan_out = result.graph.fan_out()
    coupling = [
        {"file": path, "imports": count, "imported_by": result.graph.fan_in().get(path, 0)}
        for path, count in sorted(fan_out.items(), key=lambda item: -item[1])
        if count >= _HIGH_FAN_OUT
    ][:8]

    return {
        "god_files": god_files,
        "god_symbols": god_symbols,
        "coupling": coupling,
        "cycles": [cycle[:8] for cycle in result.graph.cycles(limit=5)],
    }


def refactor_payload(result: AnalyzedRepository, llm: LLMService | None = None) -> dict[str, Any]:
    """Build hotspot payload; LLM turns it into a plan when available."""
    spots = hotspots(result)
    payload: dict[str, Any] = {
        "hotspots": spots,
        "targets": [],
        "priorities": [],
        "summary": "",
        "llm_enriched": False,
    }
    total_signals = len(spots["god_files"]) + len(spots["god_symbols"]) + len(spots["coupling"]) + len(spots["cycles"])
    if not total_signals:
        payload["summary"] = "No refactor-worthy hotspots found (file sizes, symbol sizes, fan-out, and cycles are all modest)."
        return payload

    if llm is not None:
        evidence = (
            f"Repository: {result.full_name} ({result.file_count} files, {result.total_loc} LOC)\n"
            f"God files (LOC >= {_GOD_FILE_LOC}): {json.dumps(spots['god_files'])}\n"
            f"Large symbols (>= {_GOD_FUNC_LINES} lines / classes >= {_GOD_CLASS_METHODS} methods): {json.dumps(spots['god_symbols'])[:1800]}\n"
            f"High fan-out files (imports >= {_HIGH_FAN_OUT}): {json.dumps(spots['coupling'])}\n"
            f"Import cycles: {json.dumps(spots['cycles'])[:1200]}\n\n"
            "File names for anchoring:\n"
            + "\n".join(f"- {entry['file']}" for entry in spots["god_files"][:6])
        )
        try:
            out, _provider = llm.generate(RefactorPlanOut, _REFACTOR_SYSTEM, evidence, max_output_tokens=2400)
        except Exception:
            out = None
        if out is not None:
            known_files = set(result.all_paths)

            def valid_name(name: str) -> bool:
                base = name.split(":", 1)[0]
                return base in known_files

            targets = [target for target in out.targets if valid_name(target.name)]
            payload["targets"] = [target.model_dump() for target in targets][:8]
            payload["priorities"] = list(out.priorities)[:6]
            payload["summary"] = out.summary
            payload["llm_enriched"] = bool(targets)
            return payload

    # Deterministic fallback: one target per hotspot kind.
    targets: list[dict[str, Any]] = []
    for entry in spots["god_files"]:
        targets.append(
            RefactorTarget(
                name=entry["file"],
                problem=f"file is {entry['loc']} LOC with {entry['symbols']} symbols",
                plan=["split by responsibility into cohesive modules", "move private helpers into library modules"],
                impact="medium",
            ).model_dump()
        )
    for entry in spots["god_symbols"]:
        targets.append(
            RefactorTarget(
                name=entry["ref"],
                problem=f"{entry['kind']} '{entry['name']}' spans {entry['lines']} lines",
                plan=["extract cohesive sub-functions", "unit-test the extracted parts first"],
                impact="medium" if entry["kind"] == "function" else "high",
            ).model_dump()
        )
    for entry in spots["coupling"]:
        targets.append(
            RefactorTarget(
                name=entry["file"],
                problem=f"imports {entry['imports']} modules (fan-out hotspot)",
                plan=["introduce interfaces/ports for the most-used dependencies", "group imports by layer and invert where possible"],
                impact="medium",
            ).model_dump()
        )
    payload["targets"] = targets[:10]
    payload["priorities"] = [entry["name"] for entry in targets[:5]]
    payload["summary"] = f"{total_signals} hotspots; deterministic plan (no LLM)."
    return payload


__all__ = ["hotspots", "refactor_payload"]
