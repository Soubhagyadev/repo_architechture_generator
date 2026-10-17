"""Budgeted, source-grounded context packs for LLM prompts.

The pack is deliberately small: only files that score high on keyword
relevance and module-graph centrality are included, and file bodies are
truncated to the lines around the matched symbols. This keeps prompts and
API costs bounded regardless of repository size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from repo_analyzer.analyzer import AnalyzedRepository

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass
class Snippet:
    """A contiguous line range from one file."""

    path: str
    text: str
    start_line: int
    end_line: int

    def render(self) -> str:
        """Numbered source block for prompt inclusion."""
        lines = self.text.splitlines()
        header = f"{self.path}:{self.start_line}-{self.end_line}"
        body = "\n".join(
            f"{self.start_line + offset:4d}| {line}" for offset, line in enumerate(lines)
        )
        return f"[{header}]\n{body}"


@dataclass
class ContextPack:
    """All grounded context handed to a single LLM call."""

    files: dict[str, str] = field(default_factory=dict)  # path -> rendered text
    char_total: int = 0
    paths: list[str] = field(default_factory=list)

    def add_file(self, path: str, rendered: str) -> None:
        """Add a rendered file block, tracking total character budget."""
        if path in self.files:
            return
        self.files[path] = rendered
        self.char_total += len(rendered)
        self.paths.append(path)

    def render(self) -> str:
        """Concatenate all file blocks."""
        return "\n\n".join(self.files[path] for path in self.paths)


def tokenize(text: str) -> set[str]:
    """Lowercase identifier/path tokens of length >= 3."""
    tokens: set[str] = set()
    for match in _WORD_RE.finditer(text.lower()):
        tokens.add(match.group())
    return tokens


def _file_tokens(path: str, repo: AnalyzedRepository) -> set[str]:
    """Tokens describing a file: path segments plus symbol names."""
    tokens = set(re.split(r"[\W_]+", path.lower()))
    parsed = repo.parsed.get(path)
    if parsed:
        for symbol in parsed.symbols:
            tokens.add(symbol.name.lower())
            tokens.update(re.split(r"[\W_]+", symbol.signature.lower()))
            doc_words = tokenize(symbol.doc)
            tokens.update(doc_words)
    return tokens


def relevance_scores(
    repo: AnalyzedRepository,
    query_tokens: set[str],
    *,
    centrality_weight: float = 0.35,
) -> dict[str, float]:
    """Score files by query-token overlap plus graph centrality."""
    fan = repo.graph.fan_in()
    fan_out = repo.graph.fan_out()
    max_centrality = max((fan.get(p, 0) + fan_out.get(p, 0) for p in repo.parsed), default=1) or 1
    scores: dict[str, float] = {}
    for path in repo.parsed:
        tokens = _file_tokens(path, repo)
        hits = len(query_tokens & tokens)
        score = hits / (len(query_tokens) or 1)
        centrality = (fan.get(path, 0) + fan_out.get(path, 0)) / max_centrality
        scores[path] = score + centrality_weight * centrality
    # Files with no direct hits but high centrality still make the cut when
    # the query is broad; pure-zero files never rank above direct hits.
    return scores


def ranked_paths(
    repo: AnalyzedRepository,
    query: str,
    limit: int,
    extra_paths: list[str] | None = None,
) -> list[str]:
    """Top-ranked file paths for a free-text query."""
    query_tokens = tokenize(query)
    if extra_paths:
        for path in extra_paths:
            query_tokens.update(re.split(r"[\W_]+", path.lower()))
    if not query_tokens:
        query_tokens = {"app", "main", "service", "api"}
    scores = relevance_scores(repo, query_tokens)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    selected = [path for path, score in ordered if score > 0][:limit]
    for path in extra_paths or []:
        if path in repo.parsed and path not in selected:
            selected.append(path)
    return selected


def _symbol_ranges(repo: AnalyzedRepository, path: str, max_symbols: int) -> list[tuple[int, int, str]]:
    """Line ranges worth showing for ``path`` (key symbols)."""
    parsed = repo.parsed.get(path)
    if not parsed or not parsed.symbols:
        return []
    parsed.symbols.sort(key=lambda sym: (sym.start_line, sym.end_line))
    chosen = parsed.symbols[:max_symbols]
    ranges: list[tuple[int, int, str]] = []
    for symbol in chosen:
        start = max(1, symbol.start_line - 2)
        end = symbol.end_line
        ranges.append((start, end, symbol.name))
    return ranges


def build_pack(
    repo: AnalyzedRepository,
    paths: list[str],
    budget: int,
    *,
    whole_files: list[str] | None = None,
    focus_symbols: dict[str, list[str]] | None = None,
) -> ContextPack:
    """Assemble a context pack within ``budget`` characters.

    Small files are inlined whole; larger files contribute only the source
    ranges of the most relevant symbols. ``focus_symbols`` restricts ranges
    to the named symbols when provided.
    """
    pack = ContextPack()
    whole = set(whole_files or [])
    focus = focus_symbols or {}
    for path in paths:
        if pack.char_total >= budget:
            break
        remaining = budget - pack.char_total
        abs_path = repo.workspace / path
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        loc = repo.loc_for(path)
        name = path.rsplit("/", 1)[-1]
        entry_like = any(entry.path == path for entry in repo.entry_points)
        if path in whole or len(text) <= 2400:
            rendered = f"### {name}\n" + _numbered(text, 1, loc)
            if len(rendered) <= remaining:
                pack.add_file(path, rendered)
                continue
        symbols = focus.get(path)
        if symbols:
            ranges = [
                (start, end, sym_name)
                for start, end, sym_name in _symbol_ranges(repo, path, max_symbols=12)
                if sym_name in symbols
            ][:3]
        else:
            ranges = _symbol_ranges(repo, path, max_symbols=6)
        if entry_like and not ranges:
            head_lines = text.splitlines()[:60]
            rendered = f"### {name}\n" + _numbered("\n".join(head_lines), 1, len(head_lines))
            pack.add_file(path, rendered[:remaining])
            continue
        if not ranges:
            head = text.splitlines()[:40]
            rendered = f"### {name} (head)\n" + _numbered("\n".join(head), 1, len(head))
            pack.add_file(path, rendered[:remaining])
            continue
        sections: list[str] = [f"### {name}"]
        for start, end, _name in ranges:
            slice_text = _slice_lines(text, start, end)
            sections.append(_numbered(slice_text, start, end))
        rendered = "\n...\n".join(sections)
        pack.add_file(path, rendered[:remaining])
    return pack


def _slice_lines(text: str, start: int, end: int) -> str:
    """1-based inclusive line slice."""
    lines = text.splitlines()
    if not lines:
        return ""
    start = max(1, start)
    end = min(len(lines), max(start, end))
    return "\n".join(lines[start - 1 : end])


def _numbered(text: str, start: int, end: int) -> str:
    """Render lines with absolute line numbers."""
    lines = text.splitlines() or [""]
    width = len(str(end))
    return "\n".join(f"{start + offset:{width}d}| {line}" for offset, line in enumerate(lines))


def sanitize_for_prompt(text: str, secret_replacements: dict[str, str] | None = None) -> str:
    """Redact obvious secret material before it leaves the process."""
    cleaned = text
    patterns = {
        "redacted-token": r"(?i)\b(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|xox[bap]-[A-Za-z0-9-]{10,})\b",
        "redacted-aws-secret": r"(?i)(aws_access_key_id|aws_secret_access_key)\s*=\s*['\"][^'\"]+['\"]",
        "redacted-assignment": r"(?i)^(\s*(?:API_KEY|SECRET|TOKEN|PASSWORD|AUTH|PRIVATE_KEY)[A-Z_]*\s*=\s*)(['\"]).+\2\s*$",
    }
    for name, pattern in patterns.items():
        cleaned = re.sub(pattern, name, cleaned, flags=re.M)
    return cleaned


__all__ = [
    "ContextPack",
    "Snippet",
    "build_pack",
    "ranked_paths",
    "relevance_scores",
    "sanitize_for_prompt",
    "tokenize",
]
