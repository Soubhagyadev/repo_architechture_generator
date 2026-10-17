"""Detect repository entry points and derive execution flows from the graph."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from repo_analyzer.analysis.graph import ModuleGraph


@dataclass
class EntryPoint:
    """A file suspected of being a program entry point."""

    path: str
    kind: str
    confidence: float = 0.5
    reason: str = ""


@dataclass
class ExecutionFlow:
    """A traced call chain from an entry point into the codebase."""

    name: str
    steps: list[str] = field(default_factory=list)
    entry: str = ""

    def to_json(self) -> dict[str, object]:
        """Serialize for caching."""
        return {"name": self.name, "entry": self.entry, "steps": self.steps}


def detect_entry_points(
    parsed_symbols: dict[str, list[str]],
    entry_flags: dict[str, list[str]],
    manifests: dict[str, dict[str, object]],
    graph: ModuleGraph,
) -> list[EntryPoint]:
    """Combine heuristics into a ranked entry-point list.

    ``parsed_symbols`` maps path -> top-level symbol names; ``entry_flags``
    maps path -> parser flags; ``manifests`` maps package.json/pyproject style
    manifests (parsed) to files; ``graph`` supplies import fan-in/out.
    """
    scored: dict[str, list[tuple[float, str, str]]] = {}

    def add(path: str, confidence: float, kind: str, reason: str) -> None:
        scored.setdefault(path, []).append((confidence, kind, reason))

    for path, flags in entry_flags.items():
        for flag in flags:
            if flag == "dunder-main":
                add(path, 0.9, "script", "__main__ guard")
            elif flag == "server-bootstrap":
                add(path, 0.9, "server", "starts a server (uvicorn/flask run)")
            elif flag == "main-function":
                add(path, 0.6, "main", "defines main()")
            elif flag == "go-main":
                add(path, 0.95, "main", "package main with func main()")
            elif flag == "java-main":
                add(path, 0.6, "main", "declares main method")
            elif flag == "module-convention":
                add(path, 0.4, "module", "conventional main/index module")

    for manifest_path, manifest in manifests.items():
        if not isinstance(manifest, dict):
            continue
        base = manifest_path.rsplit("/", 1)[0]
        for key in ("main", "bin", "module"):
            value = manifest.get(key)
            if isinstance(value, str) and value:
                candidate = posixpath.join(base, value) if base else value
                add(candidate, 0.8, "manifest", f"{manifest_path} declares it as entry")
        scripts = manifest.get("scripts")
        if isinstance(scripts, dict):
            start = scripts.get("start") or scripts.get("serve")
            if isinstance(start, str):
                for token in start.split():
                    if token.endswith(".js") or token.endswith(".ts"):
                        candidate = posixpath.join(base, token) if base else token
                        add(candidate, 0.5, "manifest", f"npm start runs {token}")

    for path, symbols in parsed_symbols.items():
        if "main" in symbols:
            add(path, 0.55, "main", "exports a main() function")

    # Files many others import but that import nothing are likely libs, not entries.
    results: list[EntryPoint] = []
    for path, votes in scored.items():
        best = max(votes, key=lambda vote: vote[0])
        confidence = min(0.98, sum(vote[0] for vote in votes) / (1 + len(votes) * 0.1))
        results.append(EntryPoint(path=path, kind=best[1], confidence=confidence, reason=best[2]))
    results.sort(key=lambda item: (-item.confidence, item.path))
    return results[:12]


def trace_flow(graph: ModuleGraph, entry: str, max_depth: int = 4, max_steps: int = 12) -> ExecutionFlow:
    """BFS the import graph from ``entry`` to describe an execution flow."""
    order: list[str] = [entry]
    visited = {entry}
    frontier: list[str] = [entry]
    depth = 0
    while frontier and depth < max_depth and len(order) < max_steps:
        nxt: list[str] = []
        for node in sorted(frontier):
            for target in sorted(graph.edges.get(node, ())):
                if target not in visited:
                    visited.add(target)
                    order.append(target)
                    if len(order) >= max_steps:
                        break
                    nxt.append(target)
        frontier = nxt
        depth += 1
    flow = ExecutionFlow(name=f"flow from {entry}", entry=entry)
    for hop, path in enumerate(order):
        arrow = "-> " if hop else "starts at "
        flow.steps.append(f"{arrow}{path}")
    return flow


__all__ = ["EntryPoint", "ExecutionFlow", "detect_entry_points", "trace_flow"]
