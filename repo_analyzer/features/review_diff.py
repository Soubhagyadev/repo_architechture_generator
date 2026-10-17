"""Commit-range code review: GitHub compare API + LLM narrative."""

from __future__ import annotations

from typing import Any

from repo_analyzer.github.client import GitHubClient
from repo_analyzer.github.urls import RepoSlug
from repo_analyzer.llm.schemas import ReviewOut
from repo_analyzer.llm.service import LLMService
from repo_analyzer.features.context import sanitize_for_prompt

_MAX_PATCH_TOTAL = 30_000

_REVIEW_SYSTEM = """You review the supplied commit-range diff for a repository.
Rules:
- Comment ONLY on changes visible in the diff (filenames and patch hunks provided).
- `findings` entries: file = exact filename from the diff; line = a line number INSIDE the hunk shown for that file (or null when unsure).
- Severity your call: low|medium|high|critical. `commits_summary` = one short paragraph characterizing the change set.
- `improvements` = max 3 concrete suggestions.
- Output only JSON matching: {"summary": str, "commits_summary": str, "findings": [{"file": str, "line": int | null, "severity": str, "comment": str}], "improvements": [str]}
"""


def review_payload(
    diff: dict[str, Any],
    llm: LLMService | None = None,
) -> dict[str, Any]:
    """Build a review payload from a compare-API diff (client.compare output)."""
    files: list[dict[str, Any]] = diff.get("files") or []
    commits: list[dict[str, str]] = diff.get("commits") or []
    changed_files = [entry["filename"] for entry in files if entry.get("filename")]
    payload: dict[str, Any] = {
        "base_status": diff.get("status"),
        "total_commits": diff.get("total_commits"),
        "commits": commits,
        "changed_files": changed_files,
        "findings": [],
        "improvements": [],
        "summary": "",
        "llm_enriched": False,
    }
    if not files:
        payload["summary"] = "No file changes between the requested refs."
        return payload

    if llm is not None:
        pack_parts: list[str] = []
        used = 0
        for entry in files:
            patch = (entry.get("patch") or "").strip()
            if not patch:
                continue
            block = (
                f"## {entry['filename']} ({entry.get('status')}, +{entry.get('additions', 0)}"
                f"/-{entry.get('deletions', 0)})\n{patch}"
            )
            if used + len(block) > _MAX_PATCH_TOTAL:
                break
            pack_parts.append(block)
            used += len(block)
        commit_lines = "\n".join(f"- {c['message']}" for c in commits[:40])
        evidence = (
            f"Commits ({len(commits)}):\n{commit_lines}\n\n"
            f"Changed files ({len(changed_files)}):\n"
            + "\n".join(f"- {name}" for name in changed_files[:80])
            + "\n\nUnified diffs (line numbers inside hunk headers are authoritative):\n"
            + sanitize_for_prompt("\n\n".join(pack_parts))
        )
        try:
            out, _provider = llm.generate(ReviewOut, _REVIEW_SYSTEM, evidence, max_output_tokens=2400)
        except Exception:
            out = None
        if out is not None:
            known = set(changed_files)
            payload["findings"] = [
                finding.model_dump()
                for finding in out.findings
                if finding.file in known
            ][:20]
            payload["improvements"] = list(out.improvements)[:4]
            payload["summary"] = out.summary
            payload["commits_summary"] = out.commits_summary
            payload["llm_enriched"] = bool(payload["findings"])
            return payload

    # Deterministic fallback
    total_add = sum(entry.get("additions", 0) for entry in files)
    total_del = sum(entry.get("deletions", 0) for entry in files)
    payload["summary"] = (
        f"{len(commits)} commit(s), {len(changed_files)} file(s), +{total_add}/-{total_del} (deterministic diff view; no LLM)."
    )
    payload["commits_summary"] = " · ".join(c["message"] for c in commits[:8])
    return payload


__all__ = ["review_payload"]


def fetch_diff(client: GitHubClient, slug: RepoSlug, base: str, head: str) -> dict[str, Any]:
    """Fetch the compare payload for ``base...head``."""
    return client.compare(slug, base, head)
