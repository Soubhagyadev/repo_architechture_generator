"""Module dependency graph built from parsed files."""

from __future__ import annotations

import posixpath
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class ModuleGraph:
    """Directed file-level import graph with import and call edges."""

    nodes: list[str] = field(default_factory=list)
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    edge_reasons: dict[tuple[str, str], list[int]] = field(default_factory=lambda: defaultdict(list))

    def add_node(self, path: str) -> None:
        """Register a file node."""
        if path not in self.edges:
            self.nodes.append(path)
            self.edges.setdefault(path, set())

    def add_edge(self, src: str, dst: str, line: int) -> None:
        """Record that ``src`` imports (or calls into) ``dst`` at ``line``."""
        if src == dst or not dst:
            return
        self.add_node(src)
        self.add_node(dst)
        self.edges[src].add(dst)
        self.edge_reasons[(src, dst)].append(line)

    def dependents(self, path: str) -> set[str]:
        """Files that import ``path`` (reverse edges)."""
        return {src for src, targets in self.edges.items() if path in targets}

    def dependencies(self, path: str) -> set[str]:
        """Files that ``path`` imports."""
        return set(self.edges.get(path, ()))

    def orphans(self) -> set[str]:
        """Nodes with neither incoming nor outgoing edges."""
        return {
            node
            for node in self.nodes
            if not self.edges.get(node) and not self.dependents(node)
        }

    def cycles(self, limit: int = 8) -> list[list[str]]:
        """Strongly connected components with size > 1 (up to ``limit``)."""
        index_counter = [0]
        stack: list[str] = []
        on_stack: set[str] = set()
        lowlink: dict[str, int] = {}
        index: dict[str, int] = {}
        result: list[list[str]] = []

        def strongconnect(node: str) -> None:
            index[node] = index_counter[0]
            lowlink[node] = index_counter[0]
            index_counter[0] += 1
            stack.append(node)
            on_stack.add(node)
            for target in self.edges.get(node, ()):
                if target not in index:
                    strongconnect(target)
                    lowlink[node] = min(lowlink[node], lowlink[target])
                elif target in on_stack:
                    lowlink[node] = min(lowlink[node], index[target])
            if lowlink[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    result.append(sorted(component))

        for node in self.nodes:
            if node not in index:
                strongconnect(node)
        return result[:limit]

    def fan_out(self) -> dict[str, int]:
        """Out-degree per node."""
        return {node: len(self.edges.get(node, ())) for node in self.nodes}

    def fan_in(self) -> dict[str, int]:
        """In-degree per node."""
        counts: Counter[str] = Counter()
        for targets in self.edges.values():
            for target in targets:
                counts[target] += 1
        return {node: counts.get(node, 0) for node in self.nodes}

    def top_central(self, limit: int = 10) -> list[tuple[str, int]]:
        """Files ranked by combined in/out degree."""
        fan_out = self.fan_out()
        fan_in = self.fan_in()
        scored = [
            (node, fan_in[node] + fan_out[node])
            for node in self.nodes
            if fan_in[node] + fan_out[node] > 0
        ]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]

    def downstream(self, path: str, depth: int = 3) -> dict[str, int]:
        """Files reachable from ``path`` (what it depends on) mapped to BFS depth."""
        seen: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque([(path, 0)])
        while queue:
            node, level = queue.popleft()
            if level >= depth:
                continue
            for target in self.edges.get(node, ()):
                if target not in seen or seen[target] > level + 1:
                    seen[target] = level + 1
                    queue.append((target, level + 1))
        seen.pop(path, None)
        return seen

    def upstream(self, path: str, depth: int = 3) -> dict[str, int]:
        """Files that reach ``path`` (what depends on it) mapped to BFS depth."""
        reverse: dict[str, set[str]] = defaultdict(set)
        for src, targets in self.edges.items():
            for target in targets:
                reverse[target].add(src)
        seen: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque([(path, 0)])
        while queue:
            node, level = queue.popleft()
            if level >= depth:
                continue
            for target in reverse.get(node, ()):
                if target not in seen or seen[target] > level + 1:
                    seen[target] = level + 1
                    queue.append((target, level + 1))
        seen.pop(path, None)
        return seen

    def to_json(self) -> dict[str, object]:
        """Serialize for reports/caching."""
        return {
            "nodes": sorted(self.nodes),
            "edges": {src: sorted(targets) for src, targets in self.edges.items() if targets},
        }


def resolve_import(imported: str, importer: str, all_files: set[str], is_package: set[str]) -> str | None:
    """Resolve an imported module string to a repo file path, if possible.

    Handles relative (``./x``, ``../x``, ``..x``) and absolute repo imports by
    matching candidate files (``mod.py``, ``mod/index.py``, ``mod.js``) against
    ``all_files``. Returns a POSIX path or ``None``.
    """
    if not imported:
        return None
    importer_dir = posixpath.dirname(importer)

    if imported.startswith("."):
        level = len(imported) - len(imported.lstrip("."))
        base = imported[level:].lstrip("/")
        target_dir = importer_dir
        for _ in range(level - 1):
            target_dir = posixpath.dirname(target_dir)
        candidate = posixpath.normpath(posixpath.join(target_dir, base)) if base else target_dir
    else:
        candidate = imported.replace(".", "/")

    extension_candidates: list[str] = []
    if "/" in candidate or candidate.startswith("."):
        extension_candidates = [
            candidate,
            f"{candidate}.py",
            f"{candidate}/__init__.py",
            f"{candidate}.js",
            f"{candidate}.ts",
            f"{candidate}/index.js",
            f"{candidate}/index.ts",
        ]
    else:
        # Dotted python-style import: match each package prefix.
        parts = candidate.split("/")
        for i in range(len(parts), 0, -1):
            prefix = "/".join(parts[:i])
            extension_candidates.extend(
                [
                    f"{prefix}.py",
                    f"{prefix}/__init__.py",
                    f"{prefix}.js",
                    f"{prefix}/index.js",
                    f"{prefix}.ts",
                    f"{prefix}/index.ts",
                ]
            )
        extension_candidates.append(candidate)

    for option in extension_candidates:
        if option in all_files:
            return option
    for option in extension_candidates:
        if option in is_package:
            return option
    return None


__all__ = ["ModuleGraph", "resolve_import"]
