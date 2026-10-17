"""Regex-based parser for Go, Java, and other C-family languages."""

from __future__ import annotations

import re
from repo_analyzer.parsing.symbols import ImportRef, ParsedFile, Symbol

_GO_IMPORT_RE = re.compile(r"^\s*import\s+\"(?P<module>[^\"]+)\"", re.M)
_GO_BLOCK_IMPORT_RE = re.compile(r"import\s*\((?P<body>[^)]*)\)", re.S)
_GO_FUNC_RE = re.compile(r"^[ \t]*(?:func\s+(?:\([^)]*\)\s+)?)(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)", re.M)
_GO_TYPE_RE = re.compile(r"^[ \t]*type\s+(?P<name>[A-Za-z_]\w*)\s+(?P<kind>struct|interface)\b", re.M)

_JAVA_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", re.M)
_JAVA_CLASS_RE = re.compile(r"^[ \t]*(?:public|private|protected)?\s*(?:abstract|final|static)?\s*(?:class|interface|enum)\s+([A-Za-z_]\w*)", re.M)
_JAVA_METHOD_RE = re.compile(
    r"^[ \t]*(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\], .]+\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:throws [\w, .]+)?\{",
    re.M,
)


def parse_go(path: str, source: str) -> ParsedFile:
    """Parse Go source into imports, functions, and types."""
    imports: list[ImportRef] = []
    for match in _GO_IMPORT_RE.finditer(source):
        imports.append(
            ImportRef(module=match.group("module"), names=[], line=source.count("\n", 0, match.start()) + 1, kind="import")
        )
    for match in _GO_BLOCK_IMPORT_RE.finditer(source):
        base_line = source.count("\n", 0, match.start()) + 1
        for line_offset, line in enumerate(match.group("body").splitlines()):
            stripped = line.strip().strip('"')
            if stripped:
                imports.append(ImportRef(module=stripped, names=[], line=base_line + line_offset, kind="import"))
    symbols: list[Symbol] = []
    for match in _GO_FUNC_RE.finditer(source):
        name = match.group("name")
        if name == "main":
            kind = "function"
        else:
            kind = "function"
        symbols.append(
            Symbol(
                name=name,
                kind=kind,
                path=path,
                start_line=source.count("\n", 0, match.start()) + 1,
                end_line=source.count("\n", 0, match.end()) + 1,
                signature=f"func {name}({match.group('params').strip()})",
            )
        )
    for match in _GO_TYPE_RE.finditer(source):
        symbols.append(
            Symbol(
                name=match.group("name"),
                kind="class" if match.group("kind") == "struct" else "type",
                path=path,
                start_line=source.count("\n", 0, match.start()) + 1,
                end_line=source.count("\n", 0, match.start()) + 1,
                signature=f"type {match.group('name')} {match.group('kind')}",
            )
        )
    entry_points = ["go-main"] if re.search(r"\bpackage\s+main\b", source) and "func main(" in source else []
    return ParsedFile(
        path=path,
        language="go",
        loc=source.count("\n") + 1,
        symbols=symbols,
        imports=imports,
        entry_points=entry_points,
    )


def parse_java(path: str, source: str) -> ParsedFile:
    """Parse Java-ish source (class/method/import extraction only)."""
    package_match = _JAVA_PACKAGE_RE.search(source)
    package = package_match.group(1) if package_match else ""
    imports: list[ImportRef] = []
    for match in _JAVA_IMPORT_RE.finditer(source):
        imports.append(
            ImportRef(module=match.group(1), names=[], line=source.count("\n", 0, match.start()) + 1, kind="import")
        )
    symbols: list[Symbol] = []
    for match in _JAVA_CLASS_RE.finditer(source):
        symbols.append(
            Symbol(
                name=match.group(1),
                kind="class",
                path=path,
                start_line=source.count("\n", 0, match.start()) + 1,
                end_line=source.count("\n", 0, match.end()) + 1,
                signature=f"class {match.group(1)}",
            )
        )
    for match in _JAVA_METHOD_RE.finditer(source):
        name = match.group(1)
        symbols.append(
            Symbol(
                name=name,
                kind="method",
                path=path,
                start_line=source.count("\n", 0, match.start()) + 1,
                end_line=source.count("\n", 0, match.end()) + 1,
                signature=f"{name}({match.group(2).strip()})",
            )
        )
    entry_points = []
    if package == "" or "public static void main" in source:
        entry_points.append("java-main")
    return ParsedFile(
        path=path,
        language="java",
        loc=source.count("\n") + 1,
        symbols=symbols,
        imports=imports,
        entry_points=entry_points,
    )


__all__ = ["parse_go", "parse_java"]
