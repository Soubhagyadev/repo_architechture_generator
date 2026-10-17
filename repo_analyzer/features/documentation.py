"""Codebase documentation feature: directories, tech choices, execution flow."""

from __future__ import annotations

import json
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.llm.schemas import DocsOut
from repo_analyzer.llm.service import LLMService

_DOCS_SYSTEM = """You are writing onboarding documentation for a repository.
Ground every statement in the provided evidence; cite exact files with `file` + `line` where a claim is about specific code.
Requirements:
- `directories`: explain the responsibility of each supplied directory. Cite 1-3 important files inside it.
- `tech_choices`: technology decisions visible in dependencies/config with short rationale and trade-offs; do not invent tools.
- `execution_flow`: the main run path, step by step, citing entry point code first.
- Output only JSON matching: {"summary": str, "directories": [{"path": str, "purpose": str, "important_files": [{"file": str, "line": int}]}], "tech_choices": [{"technology": str, "rationale": str, "trade_offs": str}], "execution_flow": [{"description": str, "file": str, "line": int | null}]}
"""


def documentation_payload(result: AnalyzedRepository) -> dict[str, Any]:
    """Deterministic documentation data."""
    dir_map: dict[str, list[str]] = {}
    for path in result.all_paths:
        parts = path.split("/")
        key = parts[0] if len(parts) > 1 else "(root)"
        dir_map.setdefault(key, []).append(path)
    directories = []
    for name in sorted(dir_map, key=lambda dir_name: (-len(dir_map[dir_name]), dir_name)):
        files = dir_map[name]
        parseable = [path for path in files if path in result.parsed]
        symbol_count = sum(len(result.parsed[path].symbols) for path in parseable)
        purpose = _guess_purpose(name, files, symbol_count)
        important = sorted(parseable, key=lambda path: (-len(result.parsed[path].symbols), path))[:3]
        directories.append(
            {
                "path": name,
                "files": len(files),
                "symbols": symbol_count,
                "purpose": purpose,
                "important_files": important,
            }
        )
    tech_rows: list[dict[str, Any]] = []
    entry_flow = result.flows[0].steps if result.flows else []
    for ecosystem, names in sorted(result.dependencies.ecosystems.items()):
        tech_rows.append(
            {
                "ecosystem": ecosystem,
                "count": len(names),
                "sample": names[:14],
            }
        )
    return {
        "summary": f"{result.full_name}: {len(result.all_paths)} files, {result.total_loc} LOC",
        "directories": directories[:14],
        "tech": tech_rows,
        "config_files": result.config_paths[:14],
        "docs_files": result.docs_paths[:10],
        "entry_flow": entry_flow,
        "frameworks": result.frameworks,
    }


def _guess_purpose(name: str, files: list[str], symbol_count: int) -> str:
    """Directory purpose from naming conventions (no LLM needed)."""
    lowered = name.lower()
    conventions = {
        "src": "primary application/source code",
        "app": "primary application source",
        "lib": "shared library code",
        "tests": "automated tests",
        "test": "automated tests",
        "docs": "documentation",
        "web": "web client",
        "frontend": "web client",
        "ui": "web client",
        "static": "static assets",
        "assets": "static assets",
        "scripts": "task and maintenance scripts",
        "tools": "task and maintenance scripts",
        "bin": "executable entry scripts",
        "cli": "command-line interface",
        "migrations": "schema migrations",
        "deploy": "deployment configuration",
        "infrastructure": "infrastructure configuration",
        "k8s": "infrastructure configuration",
        "docker": "container configuration",
        "(root)": "configuration and documentation files",
    }
    for token, purpose in conventions.items():
        if token in lowered.split("/")[-1] or token == lowered.rstrip("s"):
            return purpose
    if symbol_count > 20:
        return f"code module ({symbol_count} symbols)"
    return f"contains {len(files)} files"


def llm_documentation(result: AnalyzedRepository, llm: LLMService | None) -> DocsOut | None:
    """Optional LLM documentation grounded in real files."""
    from repo_analyzer.features.context import build_pack, ranked_paths, sanitize_for_prompt

    if llm is None:
        return None
    payload = documentation_payload(result)
    include_paths: list[str] = []
    for directory in payload["directories"]:
        include_paths.extend(directory["important_files"][:2])
    include_paths.extend(result.config_paths[:6])
    include_paths.extend([entry.path for entry in result.entry_points[:2]])
    ranked = ranked_paths(result, " ".join(result.frameworks + ["entry", "config"]), limit=14, extra_paths=include_paths)
    pack = build_pack(result, ranked[:10], budget=14_000, whole_files=result.config_paths[:2])
    tree_lines: list[str] = []
    for directory in payload["directories"][:12]:
        marker = " * " if any(entry.path.startswith(directory["path"] + "/") for entry in result.entry_points) else "   "
        tree_lines.append(f"{marker}{directory['path']}/ ({directory['files']} files)")
    evidence = (
        f"Repository: {result.full_name}\n"
        f"Directory tree ( * marks an entry-point tree):\n" + "\n".join(tree_lines) + "\n\n"
        f"Dependencies: {json.dumps(payload['tech'])[:1600]}\n"
        f"Config files: {json.dumps(payload['config_files'])}\n\n"
        "Key source excerpts (line numbers exact):\n" + sanitize_for_prompt(pack.render())
    )
    try:
        out, _provider = llm.generate(DocsOut, _DOCS_SYSTEM, evidence, max_output_tokens=2400)
    except Exception:
        return None
    known = set(result.all_paths)
    for directory in out.directories:
        directory.important_files = [cit for cit in directory.important_files if cit.file in known]
    out.execution_flow = [step for step in out.execution_flow if not step.file or step.file in known]
    out.directories = [
        directory for directory in out.directories
        if directory.path == "(root)" or directory.path.split("/")[0] in {path.split("/")[0] for path in result.all_paths}
    ]
    return out


__all__ = ["documentation_payload", "llm_documentation"]
