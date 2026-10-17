"""Builds the module graph from parsed files, resolving import members."""

from __future__ import annotations

import posixpath

from repo_analyzer.analysis.graph import ModuleGraph, resolve_import
from repo_analyzer.parsing.symbols import ParsedFile


def build_graph(parsed: dict[str, ParsedFile]) -> ModuleGraph:
    """Create a file-level graph, resolving ``from X import y`` to real files.

    When ``y`` names a sibling file inside package ``X`` (common Python and
    JS barrel-file pattern), the edge points at that concrete file instead of
    the package ``__init__``, which keeps the graph useful for tracing.
    """
    graph = ModuleGraph()
    all_files = set(parsed)
    packages = {
        path for path in all_files
        if posixpath.basename(path) == "__init__.py" or posixpath.basename(path).lower() == "index.js"
    }
    for path in sorted(parsed):
        graph.add_node(path)
        source = parsed[path]
        for imp in source.imports:
            base_target = resolve_import(imp.module, path, all_files, packages)
            if base_target is None:
                continue
            resolved_members: list[str] = []
            if posixpath.basename(base_target) in {"__init__.py", "index.js", "index.ts"}:
                base_dir = posixpath.dirname(base_target)
                for name in imp.names:
                    member_candidates = [
                        posixpath.join(base_dir, f"{name}.py"),
                        posixpath.join(base_dir, f"{name}.js"),
                        posixpath.join(base_dir, f"{name}.ts"),
                        posixpath.join(base_dir, name, "__init__.py"),
                    ]
                    for candidate in member_candidates:
                        if candidate in all_files:
                            resolved_members.append(candidate)
                            break
            if resolved_members:
                for member in resolved_members:
                    graph.add_edge(path, member, imp.line)
            else:
                graph.add_edge(path, base_target, imp.line)
    return graph


def build_call_edges(parsed: dict[str, ParsedFile]) -> dict[str, dict[str, int]]:
    """Symbol-name -> {file: count} mapping for cross-file call tracing.

    Indexed from all parsed files; used by the contribution assistant to
    find where a function named in a stack trace or bug report is called.
    """
    reverse_index: dict[str, dict[str, int]] = {}
    for path, source in parsed.items():
        for symbol in source.symbols:
            bucket = reverse_index.setdefault(symbol.name, {})
            bucket[path] = bucket.get(path, 0) + 1
    return reverse_index


__all__ = ["build_call_edges", "build_graph"]
