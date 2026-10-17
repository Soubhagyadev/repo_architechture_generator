"""Security sweep: deterministic pattern scan narrated by the LLM.

The scanner only reports file:line hits it actually found; the LLM adds
severity ordering and fix advice on top of that evidence, never invents hits.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.features.context import sanitize_for_prompt
from repo_analyzer.llm.schemas import SecurityFinding, SecurityOut
from repo_analyzer.llm.service import LLMService

# (id, regex, severity, why)
_PATTERNS: list[tuple[str, str, str, str]] = [
    ("eval-exec", r"\b(?:eval|exec)\s*\(", "high", "dynamic code execution"),
    ("pickle-load", r"\b(?:pickle|marshal)\s*\.\s*loads?\s*\(", "medium", "deserialization of possibly untrusted data"),
    ("unsafe-yaml", r"yaml\s*\.\s*load\s*\((?![^)]*Loader\s*=)", "medium", "yaml.load without SafeLoader"),
    ("shell-true", r"shell\s*=\s*True", "high", "subprocess with shell interpolation"),
    ("os-system", r"os\s*\.\s*(?:system|popen)\s*\(", "high", "shell command execution"),
    ("hardcoded-secret", r"(?:api[_-]?key|secret|password|passwd|pwd|token)\b\s*[:=]\s*[\"'][^\"']{8,}[\"']", "critical", "hardcoded credential-looking literal"),
    ("sql-fstring", r"(?:execute|query)\s*\(\s*f[\"']", "high", "f-string SQL (injection risk)"),
    ("sql-percent", r"[\"']\s*(?:SELECT|INSERT|UPDATE|DELETE)\b[^\"']*%s[\"']\s*%", "medium", "percent-formatted SQL (injection risk)"),
    ("ssl-off", r"verify\s*=\s*False", "high", "TLS verification disabled"),
    ("cert-none", r"CERT_NONE", "high", "certificate validation disabled"),
    ("flask-debug", r"app\s*\.\s*run\s*\([^)]*debug\s*=\s*True", "medium", "Flask debug mode enabled"),
    ("cors-wildcard", r"allow_origins\s*=\s*\[\s*['\"]\*['\"]", "medium", "CORS wildcard origin"),
    ("weak-hash", r"hashlib\s*\.\s*(?:md5|sha1)\s*\(", "low", "weak hash (if used for passwords/tokens)"),
    ("js-eval", r"\beval\s*\(", "high", "JS eval"),
    ("js-innerhtml", r"\.innerHTML\s*=", "medium", "innerHTML assignment (XSS vector)"),
]  # type: ignore[var-annotated]

_REDACT_MATCH = re.compile(r"(api[_-]?key|secret|password|passwd|pwd|token)\b(\s*[:=]\s*[\"'])[^\"']{3,}([\"'])", re.IGNORECASE)

_SECURITY_SYSTEM = """You summarize a deterministic security scan of a repository. The findings below were found by real regex scans with exact file:line locations.
Rules:
- Do NOT invent findings. Use only the provided findings, keeping their file and line intact.
- You may adjust severity (low|medium|high|critical) with justification in `why`, and provide one-sentence `fix` advice per finding.
- `general_notes` may reference patterns visible in the findings list only.
- Output only JSON matching: {"summary": str, "findings": [{"title": str, "severity": "low"|"medium"|"high"|"critical", "file": str, "line": int, "why": str, "fix": str}], "general_notes": [str]}
"""


@dataclass
class ScanHit:
    """One regex finding with its location."""

    pattern: str
    severity: str
    why: str
    file: str
    line: int
    text: str

    def to_json(self) -> dict[str, Any]:
        """Serialize for caching/redacted display."""
        return {
            "pattern": self.pattern,
            "severity": self.severity,
            "why": self.why,
            "file": self.file,
            "line": self.line,
            "text": self.text,
        }


def _redact(text: str) -> str:
    """Mask any secret-looking literal before it reaches prompts or reports."""
    return _REDACT_MATCH.sub(r"\1\2***\3", text)


def scan(result: AnalyzedRepository, max_hits: int = 60) -> list[ScanHit]:
    """Run the deterministic pattern scan over every analyzed file."""
    compiled = [(name, re.compile(pattern), severity, why) for name, pattern, severity, why in _PATTERNS]
    hits: list[ScanHit] = []
    for path in result.all_paths:
        abs_path = result.workspace / path
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for offset, line in enumerate(text.splitlines(), start=1):
            if len(line) > 400 or not line.strip():
                continue
            for name, regex, severity, why in compiled:
                if regex.search(line):
                    snippet = _redact(line.strip())
                    if len(snippet) > 180:
                        snippet = snippet[:180] + "…"
                    hits.append(
                        ScanHit(pattern=name, severity=severity, why=why, file=path, line=offset, text=snippet)
                    )
                    if len(hits) >= max_hits:
                        return hits
    return hits


def security_payload(result: AnalyzedRepository, llm: LLMService | None = None) -> dict[str, Any]:
    """Scan + optional LLM narration; deterministic structure either way."""
    hits = scan(result)
    payload: dict[str, Any] = {
        "count": len(hits),
        "hits": [hit.to_json() for hit in hits],
        "findings": [],
        "general_notes": [],
        "summary": "",
        "llm_enriched": False,
    }
    if not hits:
        payload["summary"] = "No suspicious patterns matched in the analyzed files."
        return payload

    if llm is not None:
        evidence = (
            f"Repository: {result.full_name}\n"
            f"Scan findings ({len(hits)}):\n"
            + "\n".join(
                f"- [{hit.severity}] {hit.pattern}: {hit.file}:{hit.line} {hit.why} :: {_redact(hit.text)}"
                for hit in hits[:40]
            )
            + "\n\nRaw lines above are already redacted; do not quote secrets."
                        "\nSource excerpts for context (optional; line numbers exact):\n"
            + sanitize_for_prompt("\n".join(
                f"{hit.file}:{hit.line}| {hit.text}" for hit in hits[:20]
            ))
        )
        try:
            out, _provider = llm.generate(SecurityOut, _SECURITY_SYSTEM, evidence, max_output_tokens=2400)
        except Exception:
            out = None
        if out is not None:
            known = {hit.file for hit in hits}
            locs = {path: result.loc_for(path) for path in known}
            cleaned: list[SecurityFinding] = []
            for finding in out.findings:
                if finding.file not in known or finding.line > locs[finding.file]:
                    continue
                cleaned.append(finding)
            payload["findings"] = [finding.model_dump() for finding in cleaned]
            payload["general_notes"] = list(out.general_notes)[:6]
            payload["summary"] = out.summary
            payload["llm_enriched"] = bool(cleaned)
            return payload

    # Deterministic fallback: findings straight from the scanner.
    payload["findings"] = [
        {
            "title": f"{hit.pattern} ({hit.why})",
            "severity": hit.severity,
            "file": hit.file,
            "line": hit.line,
            "why": hit.why,
            "fix": "",
        }
        for hit in hits
    ]
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    payload["findings"].sort(key=lambda item: (severity_rank.get(item["severity"], 9), item["file"], item["line"]))
    payload["summary"] = f"{len(hits)} security-relevant pattern hits (deterministic scan)."
    return payload


__all__ = ["ScanHit", "scan", "security_payload"]
