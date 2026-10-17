"""Markdown rendering for analysis reports."""

from __future__ import annotations

from typing import Any


def _escape(text: str) -> str:
    """Escape pipes and backticks that would break tables/code fences."""
    return text.replace("|", "\\|").replace("`", "'")


def h(title: str, level: int = 2) -> str:
    """Markdown heading."""
    return f"{'#' * level} {title}"


def code_block(text: str, language: str = "") -> str:
    """Fenced code block."""
    return f"```{language}\n{text}\n```"


def citation_ref(path: str, line: int | None, end_line: int | None = None) -> str:
    """Render a file:line reference."""
    if line is None:
        return _escape(path)
    if end_line and end_line > line:
        return f"`${path}:{line}-{end_line}$"
    return f"`${path}:{line}$"


def mermaid_block(title: str, mermaid: str) -> str:
    """Diagram with heading."""
    return f"{h(title, 3)}\n\n{code_block(mermaid, 'mermaid')}\n"


def report_markdown(
    title: str,
    sections: list[tuple[str, str]],
    subtitle: str = "",
) -> str:
    """Join titled sections into a full document."""
    parts = [f"# {title}"]
    if subtitle:
        parts.append(subtitle)
    for heading, body in sections:
        parts.append(f"\n## {heading}\n")
        parts.append(body.strip() + "\n")
    return "\n".join(parts)


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Simple markdown table; escapes pipe characters."""
    if not rows:
        return "_empty_"
    head = "| " + " | ".join(_escape(cell) for cell in headers) + " |"
    divider = "|" + "|".join([" --- " for _ in headers]) + "|"
    body_rows = [
        "| " + " | ".join(_escape(str(cell)) for cell in row) + " |" for row in rows
    ]
    return "\n".join([head, divider, *body_rows])


def bullet_list(items: list[str]) -> str:
    """Bulleted markdown list."""
    if not items:
        return "_none_"
    return "\n".join(f"- {item}" for item in items)


def key_value_block(data: dict[str, Any]) -> str:
    """Render dict as a markdown table (Key, Value)."""
    rows = [[str(key), str(value)] for key, value in data.items()]
    return table(["Key", "Value"], rows)


__all__ = [
    "bullet_list",
    "citation_ref",
    "code_block",
    "h",
    "key_value_block",
    "mermaid_block",
    "report_markdown",
    "table",
]
