"""Deterministic architecture artifacts: components, Mermaid diagrams, flows."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from repo_analyzer.analysis.dependencies import DependencyInfo, frameworks_from_deps
from repo_analyzer.analysis.graph import ModuleGraph

_SAFE_ID = re.compile(r"[^A-Za-z0-9_]")


def _mermaid_id(text: str) -> str:
    """Stable ASCII identifier safe for Mermaid node ids."""
    return "n_" + _SAFE_ID.sub("_", text.lower())[:40]


@dataclass
class Component:
    """A cohesive group of files (directory-based module)."""

    name: str
    base_dir: str
    files: list[str] = field(default_factory=list)
    description: str = ""

    def to_json(self) -> dict[str, Any]:
        """Serialize for caching."""
        return {
            "name": self.name,
            "base_dir": self.base_dir,
            "files": self.files,
            "description": self.description,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "Component":
        """Deserialize from cached JSON."""
        return cls(
            name=str(payload.get("name") or ""),
            base_dir=str(payload.get("base_dir") or ""),
            files=list(payload.get("files") or []),
            description=str(payload.get("description") or ""),
        )


@dataclass
class Diagram:
    """A Mermaid diagram plus its caption."""

    title: str
    kind: str  # flowchart | sequence | graph
    mermaid: str

    def to_json(self) -> dict[str, Any]:
        """Serialize for caching."""
        return {"title": self.title, "kind": self.kind, "mermaid": self.mermaid}

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "Diagram":
        """Deserialize from cached JSON."""
        return cls(
            title=str(payload.get("title") or ""),
            kind=str(payload.get("kind") or "flowchart"),
            mermaid=str(payload.get("mermaid") or ""),
        )


def derive_components(parsed_paths: list[str], max_components: int = 12) -> list[Component]:
    """Group source files by their top one or two directory levels."""
    buckets: dict[str, list[str]] = {}
    for path in parsed_paths:
        parts = [part for part in path.split("/") if part]
        stem, _ext = posixpath.splitext(parts[-1])
        if len(parts) == 1:
            key = "<root>"
        elif stem == "__init__":
            # package folder itself
            key = posixpath.dirname(path) or "<root>"
        else:
            candidates = [parts[0]]
            if parts[0] in {"src", "lib", "app", "packages", "source", "server", "api", "backend", "frontend", "client"} and len(parts) > 2:
                candidates.append(posixpath.join(parts[0], parts[1]))
            key = candidates[-1]
        buckets.setdefault(key, []).append(path)

    components: list[Component] = []
    for key in sorted(buckets, key=lambda name: (-len(buckets[name]), name))[:max_components]:
        base_dir = "" if key == "<root>" else key
        files = sorted(buckets[key])
        pretty = key.replace("/", "::") if key != "<root>" else "root files"
        components.append(Component(name=pretty, base_dir=base_dir, files=files))
    return components


def component_graph_mermaid(graph: ModuleGraph, components: list[Component]) -> str:
    """Mermaid flowchart of cross-component dependency edges."""
    file_to_component: dict[str, str] = {}
    for component in components:
        cid = _mermaid_id(component.name)
        for path in component.files:
            file_to_component[path] = cid

    edge_set: dict[tuple[str, str], int] = {}
    for src, targets in graph.edges.items():
        src_comp = file_to_component.get(src)
        if src_comp is None:
            continue
        for target in targets:
            target_comp = file_to_component.get(target)
            if target_comp is None or target_comp == src_comp:
                continue
            key = (src_comp, target_comp)
            edge_set[key] = edge_set.get(key, 0) + 1

    lines = ["flowchart LR"]
    declared: set[str] = set()
    for component in components:
        cid = _mermaid_id(component.name)
        if cid in declared:
            continue
        declared.add(cid)
        label = component.name.replace('"', "'")
        lines.append(f'    {cid}["{label}"]')
    for (src, dst), weight in sorted(edge_set.items(), key=lambda item: -item[1]):
        lines.append(f"    {src} -->|{weight} files| {dst}")
    if len(lines) == 1:
        lines.append("    empty[no cross-component edges]")
    return "\n".join(lines)


def dependency_layers_mermaid(graph: ModuleGraph, limit_files: int = 40) -> str:
    """Mermaid flowchart of the most important file-level dependencies."""
    top = {node for node, _score in graph.top_central(limit=limit_files)}
    if not top:
        return "flowchart TD\n    empty[no module edges detected]"
    # Keep only edges between top nodes.
    lines: list[str] = ["flowchart TD"]
    included: set[str] = set()
    edges: list[tuple[str, str, list[int]]] = []
    for src in sorted(top):
        for target in sorted(graph.edges.get(src, ())):
            if target in top:
                edges.append((src, target, graph.edge_reasons.get((src, target), [])))
                included.add(src)
                included.add(target)
    for node in sorted(included):
        label = posixpath.basename(node)
        parent = posixpath.dirname(node)
        shown = f"{label}\\n{parent}" if parent else label
        lines.append(f'    {_mermaid_id(node)}["{shown}"]')
    for src, target, _reasons in edges:
        lines.append(f"    {_mermaid_id(src)} --> {_mermaid_id(target)}")
    return "\n".join(lines)


def cycle_mermaid(graph: ModuleGraph) -> str:
    """Mermaid flowchart highlighting circular import cycles, or empty text."""
    cycles = graph.cycles(limit=5)
    if not cycles:
        return "flowchart TD\n    ok[No circular imports detected]"
    lines: list[str] = ["flowchart LR"]
    cycle_id = 0
    for members in cycles:
        cycle_id += 1
        nodes_id = f"c{cycle_id}"
        lines.append(f"    subgraph {nodes_id}[circular import #{cycle_id}]")
        for member in members:
            lines.append(f"        {_mermaid_id(member)}[{posixpath.basename(member)}]")
        lines.append("    end")
        for index in range(len(members)):
            src = members[index]
            dst = members[(index + 1) % len(members)]
            lines.append(f"    {_mermaid_id(src)} --> {_mermaid_id(dst)}")
    return "\n".join(lines)


def dependency_section(dep_info: DependencyInfo) -> str:
    """Human-readable dependency summary (ecosystems + frameworks)."""
    frameworks = frameworks_from_deps(dep_info)
    sections: list[str] = []
    if frameworks:
        sections.append("Frameworks: " + ", ".join(sorted(frameworks)))
    for ecosystem in sorted(dep_info.ecosystems):
        names = dep_info.ecosystems[ecosystem]
        shown = ", ".join(names[:24])
        if len(names) > 24:
            shown += f", +{len(names) - 24} more"
        sections.append(f"{ecosystem}: {shown}")
    if dep_info.manifests:
        sections.append("Manifests: " + ", ".join(dep_info.manifests[:12]))
    if dep_info.ci_files:
        sections.append("CI: " + ", ".join(dep_info.ci_files[:6]))
    return "\n".join(f"- {item}" for item in sections) or "- no dependency manifests detected"


def execution_flow_mermaid(flow_steps: list[list[str]], max_rows: int = 8) -> str:
    """Mermaid sequence diagram approximating major execution flows."""
    lines: list[str] = ["sequenceDiagram", "    autonumber"]
    rows = 0
    for steps in flow_steps:
        if rows >= max_rows:
            break
        for step in steps:
            if rows >= max_rows:
                break
            safe = step.replace('"', "'")
            lines.append(f'    Note over entry: {safe}')
            rows += 1
    return "\n".join(lines)


__all__ = [
    "Component",
    "Diagram",
    "component_graph_mermaid",
    "cycle_mermaid",
    "dependency_layers_mermaid",
    "dependency_section",
    "derive_components",
    "execution_flow_mermaid",
]
