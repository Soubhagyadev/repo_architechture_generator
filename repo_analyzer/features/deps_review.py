"""Dependency review: declared vs actually-imported packages, evidence-only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.llm.schemas import DepReviewOut, DepRisk
from repo_analyzer.llm.service import LLMService

_NORM_RE = re.compile(r"[-_.]+")

_PY_STDLIB = {
    "abc", "argparse", "ast", "asyncio", "base64", "binascii", "bisect", "builtins",
    "calendar", "collections", "contextlib", "copy", "csv", "ctypes", "dataclasses",
    "datetime", "decimal", "difflib", "enum", "functools", "gc", "glob", "gzip",
    "hashlib", "heapq", "hmac", "html", "http", "importlib", "inspect", "io", "ipaddress",
    "itertools", "json", "logging", "math", "mimetypes", "multiprocessing", "os", "pathlib",
    "pickle", "platform", "pprint", "queue", "random", "re", "secrets", "select", "shutil",
    "signal", "site", "socket", "sqlite3", "ssl", "stat", "string", "struct", "subprocess",
    "sys", "sysconfig", "tarfile", "tempfile", "textwrap", "threading", "time", "tomllib",
    "traceback", "types", "typing", "unittest", "urllib", "uuid", "warnings", "weakref",
    "zipfile", "zlib", "xml", "venv", "wave", "wsgiref", "zoneinfo",
}
# Common distribution-name -> import-name aliases (prevents false "unused").
_PY_ALIASES = {
    "pillow": "pil", "beautifulsoup4": "bs4", "python-dateutil": "dateutil",
    "pyyaml": "yaml", "scikit-learn": "sklearn", "opencv-python": "cv2",
    "pycryptodome": "crypto", "python-dotenv": "dotenv", "pydantic": "pydantic",
    "python-jose": "jose", "python-multipart": "multipart", "msgpack-python": "msgpack",
    "ruamel-yaml": "ruamel", "google-auth": "google", "python-telegram-bot": "telegram",
    "beautifulsoup": "bs4", "python-magic": "magic", "pywin32": "win32",
}

_DEPS_SYSTEM = """You review a repository's declared dependencies against ACTUAL import evidence supplied below.
Rules:
- Do not invent CVEs, advisories, or version facts. Comment only on what the evidence shows (naming, aliasing, pinned ranges, duplicate ecosystems).
- `unused` must be a subset of the declared-but-unmatched list provided.
- `risky` entries should quote concrete evidence (files or manifest lines) in `why`.
- Output only JSON matching: {"summary": str, "unused": [{"name": str, "note": str}], "risky": [{"name": str, "why": str}], "advice": [str]}
"""


@dataclass
class DepUsage:
    """Import-name roots seen in source per language."""

    python: set[str] = field(default_factory=set)
    javascript: set[str] = field(default_factory=set)
    other: set[str] = field(default_factory=set)


def collect_usage(result: AnalyzedRepository) -> DepUsage:
    """Collect import-name roots from every parsed file."""
    usage = DepUsage()
    for path, parsed in result.parsed.items():
        for ref in parsed.imports:
            if ref.module.startswith((".", "/", "~")):
                continue
            module = ref.module.strip()
            if not module:
                continue
            if parsed.language == "python":
                usage.python.add(module.split(".")[0].lower())
            elif parsed.language in {"javascript", "typescript"}:
                if module.startswith("@"):
                    usage.javascript.add(module.split("/", 1)[0])
                else:
                    usage.javascript.add(module.split("/")[0].split(".")[0].lower())
            else:
                usage.other.add(module.lower())
    return usage


def _normalized(name: str) -> str:
    """PEP503-ish plus node-ish normalization for comparison."""
    return _NORM_RE.sub("-", name.lower().strip())


def _import_roots_for(name: str) -> set[str]:
    """Candidate import roots for a declared package name."""
    lower = _normalized(name)
    roots = {lower.replace("-", "")}
    if lower in _PY_ALIASES:
        roots.add(_PY_ALIASES[lower])
    roots.add(lower.split("-")[0])
    return roots


def deps_payload(result: AnalyzedRepository, llm: LLMService | None = None) -> dict[str, Any]:
    """Compare declared names against collected import roots; optionally narrate."""
    usage = collect_usage(result)
    py_usage = {root for root in usage.python if root not in _PY_STDLIB}
    js_usage = usage.javascript

    unused: list[dict[str, str]] = []
    matched: list[str] = []
    for ecosystem, names in sorted(result.dependencies.ecosystems.items()):
        for name in names:
            if ecosystem in {"python"}:
                roots = _import_roots_for(name)
                if py_usage & roots:
                    matched.append(name)
                    continue
                unused.append({"name": name, "note": "python manifest lists it; no matching import found"})
            elif ecosystem in {"npm"}:
                base = name.split("/")[0].lower()
                if any(base in root or root in base for root in js_usage) or base in js_usage:
                    matched.append(name)
                    continue
                unused.append({"name": name, "note": "npm manifest lists it; no matching require/import found"})
            else:
                matched.append(name)

    # Imports never declared anywhere (rough: local packages and typing tricks excluded)
    declared_roots: set[str] = set()
    for names in result.dependencies.ecosystems.values():
        for name in names:
            declared_roots |= {r.lower() for r in _import_roots_for(name)}
    underdeclared = sorted(
        root for root in py_usage if root not in declared_roots and root not in {r.replace("-", "") for r in declared_roots}
    )[:12]

    payload: dict[str, Any] = {
        "declared_count": sum(len(names) for names in result.dependencies.ecosystems.values()),
        "ecosystems": {eco: list(names) for eco, names in sorted(result.dependencies.ecosystems.items())},
        "unused_candidates": unused,
        "underdeclared_candidates": underdeclared,
        "risky": [],
        "advice": [],
        "summary": "",
        "llm_enriched": False,
    }

    if llm is not None and (unused or underdeclared):
        evidence = (
            f"Repository: {result.full_name}\n"
            f"Declared dependencies: {json.dumps(payload['ecosystems'])}\n"
            f"Import roots found in python source: {sorted(py_usage)[:60]}\n"
            f"Import roots found in js/ts source: {sorted(js_usage)[:60]}\n"
            f"Declared-but-unmatched: {json.dumps([item['name'] for item in unused])[:1200]}\n"
            f"Imports-not-declared: {json.dumps(underdeclared)}\n"
        )
        try:
            out, _provider = llm.generate(DepReviewOut, _DEPS_SYSTEM, evidence, max_output_tokens=1800)
        except Exception:
            out = None
        if out is not None:
            unused_names = {item["name"] for item in unused}
            payload["unused_candidates"] = [
                {"name": item.name, "note": item.note}
                for item in out.unused if item.name in unused_names
            ] or unused
            payload["risky"] = [risk.model_dump() for risk in out.risky][:10]
            payload["advice"] = list(out.advice)[:6]
            payload["summary"] = out.summary
            payload["llm_enriched"] = True
            return payload

    payload["summary"] = (
        f"{len(result.dependencies.ecosystems)} ecosystem(s); "
        f"{len(unused)} unused candidate(s), {len(underdeclared)} under-declared candidate(s) (heuristic)."
    )
    payload["risky"] = [
        DepRisk(name="evaluation needed", why="heuristic comparison only; verify manually").model_dump()
    ] if underdeclared else []
    payload["advice"] = [
        "Heuristic view: manifest names are matched case-insensitively against import roots; "
        "manually confirm before removing anything."
    ]
    return payload


__all__ = ["DepUsage", "collect_usage", "deps_payload"]
