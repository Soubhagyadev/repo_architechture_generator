"""Shared utilities for hashing, tree flattening, and text snippets."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def sha256_hex(data: bytes) -> str:
    """Return the hex sha256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def flatten_tree(tree: dict[str, Any] | None, prefix: str = "") -> Iterator[str]:
    """Yield every leaf path of a nested dict tree.

    Dict values are treated as sub-dictionaries; anything else (``None``,
    strings, ints) is a leaf. ``prefix`` is prepended to every yielded path.
    """
    if not isinstance(tree, dict):
        return
    for key, value in tree.items():
        path = f"{prefix}/{key}" if prefix else key
        if isinstance(value, dict):
            yield from flatten_tree(value, path)
        else:
            yield path


def first_text(value: Any, limit: int = 280) -> str:
    """Best-effort single-line text from an unknown JSON value."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip().replace("\r", " ")
    elif isinstance(value, (list, tuple)):
        text = " | ".join(str(item) for item in value[:4])
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False)[:limit]
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def unique_extend(target: list[str], additions: Iterable[str]) -> None:
    """Append not-yet-present items from ``additions`` onto ``target``."""
    known = set(target)
    for item in additions:
        if item and item not in known:
            known.add(item)
            target.append(item)


def rel_path(base: Path, target: Path) -> str:
    """POSIX-style relative path even across drives."""
    try:
        return target.relative_to(base).as_posix()
    except ValueError:
        return target.as_posix()


def read_json(path: Path) -> Any | None:
    """Read JSON, returning ``None`` on any failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def write_json(path: Path, payload: Any) -> None:
    """Write pretty JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


__all__ = [
    "first_text",
    "flatten_tree",
    "read_json",
    "rel_path",
    "sha256_hex",
    "unique_extend",
    "write_json",
]
