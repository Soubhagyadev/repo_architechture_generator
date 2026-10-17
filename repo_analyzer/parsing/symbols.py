"""Data model for parsed source files, shared by all language parsers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

PARSER_VERSION = "1"


@dataclass
class Symbol:
    """A named code entity with source range and optional docstring."""

    name: str
    kind: str  # function | class | method | constant | type | route
    path: str
    start_line: int
    end_line: int
    signature: str = ""
    doc: str = ""
    decorators: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """Serialize for the cache."""
        return {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "signature": self.signature,
            "doc": self.doc,
            "decorators": self.decorators,
            "calls": self.calls,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "Symbol":
        """Deserialize from cached JSON."""
        return cls(
            name=str(payload.get("name") or ""),
            kind=str(payload.get("kind") or "function"),
            path=str(payload.get("path") or ""),
            start_line=int(payload.get("start_line") or 0),
            end_line=int(payload.get("end_line") or 0),
            signature=str(payload.get("signature") or ""),
            doc=str(payload.get("doc") or ""),
            decorators=list(payload.get("decorators") or []),
            calls=list(payload.get("calls") or []),
        )


@dataclass
class ImportRef:
    """An import or require statement with its unresolved module path."""

    module: str
    names: list[str]
    line: int
    kind: str = "import"  # import | from | require | use | include

    def to_json(self) -> dict[str, Any]:
        """Serialize for the cache."""
        return {"module": self.module, "names": self.names, "line": self.line, "kind": self.kind}

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ImportRef":
        """Deserialize from cached JSON."""
        return cls(
            module=str(payload.get("module") or ""),
            names=list(payload.get("names") or []),
            line=int(payload.get("line") or 0),
            kind=str(payload.get("kind") or "import"),
        )


@dataclass
class ParsedFile:
    """Full parse output for one source file."""

    path: str
    language: str
    loc: int
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    parse_error: str = ""

    def to_json(self) -> dict[str, Any]:
        """Serialize for the cache."""
        return {
            "path": self.path,
            "language": self.language,
            "loc": self.loc,
            "symbols": [symbol.to_json() for symbol in self.symbols],
            "imports": [imp.to_json() for imp in self.imports],
            "entry_points": self.entry_points,
            "parse_error": self.parse_error,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ParsedFile":
        """Deserialize from cached JSON."""
        return cls(
            path=str(payload.get("path") or ""),
            language=str(payload.get("language") or "other"),
            loc=int(payload.get("loc") or 0),
            symbols=[Symbol.from_json(item) for item in payload.get("symbols", [])],
            imports=[ImportRef.from_json(item) for item in payload.get("imports", [])],
            entry_points=list(payload.get("entry_points") or []),
            parse_error=str(payload.get("parse_error") or ""),
        )

    @classmethod
    def from_cached(cls, stored: dict[str, Any]) -> "ParsedFile":
        """Alias for :meth:`from_json` used by the cache layer."""
        return cls.from_json(stored)


def deserialize_cached(payload: str | bytes) -> ParsedFile:
    """Parse a cached JSON string into a :class:`ParsedFile`."""
    data = json.loads(payload)
    return ParsedFile.from_json(data)


def signature_of(name: str, params: list[str], prefix: str = "def") -> str:
    """Build a one-line signature string."""
    joined = ", ".join(p for p in params if p)
    return f"{prefix} {name}({joined})"


def first_doc_line(doc: str, limit: int = 200) -> str:
    """Return a cleaned first line of a docstring."""
    text = re.sub(r"\s+", " ", doc.strip())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


__all__ = [
    "PARSER_VERSION",
    "ImportRef",
    "ParsedFile",
    "Symbol",
    "deserialize_cached",
    "first_doc_line",
    "signature_of",
]
