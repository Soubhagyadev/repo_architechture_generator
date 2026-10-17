"""Tech-debt feature: TODO/FIXME/HACK extraction plus LLM thematic clustering."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.config import AppConfig
from repo_analyzer.features.context import sanitize_for_prompt
from repo_analyzer.llm.schemas import DebtItem, DebtOut, DebtTheme
from repo_analyzer.llm.service import LLMService

_MARKERS = ("TODO", "FIXME", "HACK", "XXX")
_MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s]?(.{0,180})", re.IGNORECASE)
_NOISE_PREFIX_RE = re.compile(r"^[\s/*#<>|\"'~-]+(?:\b(TODO|FIXME|HACK|XXX)\b[:\s]?)?", re.IGNORECASE)

_DEBT_SYSTEM = """You cluster technical-debt markers for a codebase using ONLY the supplied markers.
Rules:
- `themes` group the provided markers (use their exact file names and line numbers; never invent new ones).
- Every marker must appear in at most one theme; do not drop markers unless they are duplicates.
- `recommendations` are short, concrete, evidence-anchored (<= 3 actions).
- Output only JSON matching: {"summary": str, "themes": [{"name": str, "description": str, "items": [{"file": str, "line": int, "note": str}]}], "recommendations": [str]}
"""


@dataclass
class DebtMarker:
    """One literal TODO/FIXME/HACK/XXX occurrence."""

    kind: str
    file: str
    line: int
    text: str

    def to_json(self) -> dict[str, Any]:
        """Serialize for caching."""
        return {"kind": self.kind, "file": self.file, "line": self.line, "text": self.text}


_SKIP_SUFFIXES = (
    ".sqlite3", ".sqlite", ".db", ".sqlite3-wal", ".sqlite3-shm", ".wal",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".wasm", ".so", ".exe",
)


def extract_markers(result: AnalyzedRepository, max_markers: int = 80) -> list[DebtMarker]:
    """Scan all text files for debt markers (cheap pass over raw lines)."""
    markers: list[DebtMarker] = []
    for path in result.all_paths:
        if path.rsplit("/", 1)[-1].lower().endswith(_SKIP_SUFFIXES):
            continue
        abs_path = result.workspace / path
        try:
            if abs_path.stat().st_size > 1_000_000:
                continue
            raw = abs_path.read_bytes()
            if raw[:4096].find(b"\0") != -1:
                continue
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            continue
        for offset, line in enumerate(text.splitlines(), start=1):
            match = _MARKER_RE.search(line)
            if not match:
                continue
            cleaned = _NOISE_PREFIX_RE.sub("", line.strip()).strip()
            cleaned = re.sub(r"^[\s*#/]+", "", cleaned).rstrip("*/ \t")
            if len(cleaned) > 200:
                cleaned = cleaned[:200] + "…"
            markers.append(
                DebtMarker(kind=match.group(1).upper(), file=path, line=offset, text=cleaned or match.group(0))
            )
            if len(markers) >= max_markers:
                return markers
    return markers


def debt_payload(
    result: AnalyzedRepository,
    llm: LLMService | None = None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """Deterministic marker inventory plus optional LLM clustering."""
    markers = extract_markers(result)
    by_kind: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for marker in markers:
        by_kind[marker.kind] = by_kind.get(marker.kind, 0) + 1
        by_file[marker.file] = by_file.get(marker.file, 0) + 1
    payload: dict[str, Any] = {
        "count": len(markers),
        "by_kind": by_kind,
        "top_files": sorted(by_file.items(), key=lambda item: (-item[1], item[0]))[:10],
        "markers": [marker.to_json() for marker in markers],
        "themes": [],
        "recommendations": [],
        "llm_enriched": False,
    }
    if not markers:
        payload["summary"] = "No TODO/FIXME/HACK/XXX markers found in the analyzed files."
        return payload

    if llm is not None:
        evidence = (
            f"Repository: {result.full_name}\n"
            f"Marker counts by kind: {json.dumps(by_kind)}\n"
            f"Markers ({len(markers)}):\n" + "\n".join(
                f"- {marker.kind} {marker.file}:{marker.line} {marker.text}" for marker in markers[:60]
            ) + "\n\nSource context is not required; cluster the markers above."
        )
        try:
            out, _provider = llm.generate(DebtOut, _DEBT_SYSTEM, evidence, max_output_tokens=2000)
        except Exception:
            out = None
        if out is not None:
            known = {marker.file for marker in markers}
            locs = {path: result.loc_for(path) for path in known}
            valid_items = 0
            for theme in out.themes:
                cleaned: list[DebtItem] = []
                for item in theme.items:
                    if item.file not in known or item.line > locs[item.file]:
                        continue
                    cleaned.append(item)
                    valid_items += 1
                theme.items = cleaned
            out.themes = [theme for theme in out.themes if theme.items]
            payload["themes"] = [theme.model_dump() for theme in out.themes]
            payload["recommendations"] = list(out.recommendations)[:5]
            payload["summary"] = out.summary
            payload["llm_enriched"] = valid_items > 0
            return payload

    # Deterministic fallback: one theme per marker kind.
    buckets: dict[str, list[DebtItem]] = {}
    for marker in markers:
        buckets.setdefault(marker.kind, []).append(
            DebtItem(file=marker.file, line=marker.line, note=marker.text)
        )
    payload["themes"] = [
        DebtTheme(
            name=f"{kind} markers ({len(items)})",
            description=f"{len(items)} {kind} marker(s) across {len({item.file for item in items})} file(s)",
            items=items[:12],
        ).model_dump()
        for kind, items in sorted(buckets.items())
    ]
    payload["recommendations"] = [
        f"Review the {len(markers)} debt marker(s); start with the file with the most markers: "
        + (payload["top_files"][0][0] if payload["top_files"] else "n/a")
    ]
    payload["summary"] = f"{len(markers)} debt markers across {len(by_file)} files (deterministic clustering)."
    return payload


__all__ = ["DebtMarker", "debt_payload", "extract_markers"]
