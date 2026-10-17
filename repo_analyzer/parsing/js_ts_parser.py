"""Regex-based parser for JavaScript/TypeScript (imports, functions, classes)."""

from __future__ import annotations

import re
from repo_analyzer.parsing.symbols import ImportRef, ParsedFile, Symbol

_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(?:\w+\s*,?\s*)?(?:\{[^}]*\}\s*)?(?:\w+\s*)?(?:from\s+)?)?['\"](?P<module>[^'\"]+)['\"]",
    re.M,
)
_FROM_IMPORT_RE = re.compile(
    r"^\s*import\s+(?P<names>[^'\"\n]+?)\s+from\s+['\"](?P<module>[^'\"]+)['\"]", re.M
)
_REQUIRE_RE = re.compile(
    r"^\s*(?:const|let|var)\s+(?P<names>[\w\s,{}]+?)\s*=\s*require\(\s*['\"](?P<module>[^'\"]+)['\"]\s*\)",
    re.M,
)
_BARE_REQUIRE_RE = re.compile(r"require\(\s*['\"](?P<module>[^'\"]+)['\"]\s*\)", re.M)
_FUNCTION_RE = re.compile(
    r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^)]*)\)",
    re.M,
)
_ARROW_RE = re.compile(
    r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\((?P<params>[^)]*)\)\s*(?::[^=]*)?=>",
    re.M,
)
_CLASS_RE = re.compile(
    r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?:extends\s+[\w$.]+)?(?:\s+implements\s+[\w,\s.]+)?\{",
    re.M,
)
_METHOD_RE = re.compile(
    r"^[ \t]{4,}(?:static\s+)?(?:async\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^)]*)\)\s*(?::[^{]*)?\{",
    re.M,
)
_MODULE_PATTERNS = ("__main__", "main", "index")


def _line_of(text: str, position: int) -> int:
    """1-based line number for a character offset."""
    return text.count("\n", 0, position) + 1


def _dedupe(symbols: list[Symbol]) -> list[Symbol]:
    """Drop duplicate (name, start) pairs keeping the first."""
    seen: set[tuple[str, int]] = set()
    unique: list[Symbol] = []
    for symbol in symbols:
        key = (symbol.name, symbol.start_line)
        if key not in seen:
            seen.add(key)
            unique.append(symbol)
    return unique


def _end_line_for(text: str, start: int) -> int:
    """Estimate a declaration's end line by brace or line heuristics."""
    line = _line_of(text, start)
    return line


def parse_javascript(path: str, source: str, language: str = "javascript") -> ParsedFile:
    """Parse JS/TS source into symbols and imports using regex heuristics."""
    symbols: list[Symbol] = []
    imports: list[ImportRef] = []

    for match in _FROM_IMPORT_RE.finditer(source):
        raw_names = match.group("names").strip()
        names = [part.strip() for part in re.split(r"[{},]", raw_names) if part.strip()]
        imports.append(
            ImportRef(module=match.group("module"), names=names, line=_line_of(source, match.start()), kind="from")
        )
    for match in _REQUIRE_RE.finditer(source):
        names = [part.strip() for part in re.split(r"[{},]", match.group("names")) if part.strip()]
        imports.append(
            ImportRef(module=match.group("module"), names=names, line=_line_of(source, match.start()), kind="require")
        )
    if not imports:
        for match in _IMPORT_RE.finditer(source):
            imports.append(
                ImportRef(module=match.group("module"), names=[], line=_line_of(source, match.start()), kind="import")
            )
        for match in _BARE_REQUIRE_RE.finditer(source):
            module = match.group("module")
            if not any(ref.module == module for ref in imports):
                imports.append(
                    ImportRef(module=module, names=[], line=_line_of(source, match.start()), kind="require")
                )

    for match in _FUNCTION_RE.finditer(source):
        name = match.group("name")
        symbols.append(
            Symbol(
                name=name,
                kind="function",
                path=path,
                start_line=_line_of(source, match.start()),
                end_line=_end_line_for(source, match.end()),
                signature=f"function {name}({match.group('params').strip()})",
            )
        )
    for match in _ARROW_RE.finditer(source):
        name = match.group("name")
        symbols.append(
            Symbol(
                name=name,
                kind="function",
                path=path,
                start_line=_line_of(source, match.start()),
                end_line=_end_line_for(source, match.end()),
                signature=f"const {name} = ({match.group('params').strip()}) => ...",
            )
        )
    for match in _CLASS_RE.finditer(source):
        name = match.group("name")
        symbols.append(
            Symbol(
                name=name,
                kind="class",
                path=path,
                start_line=_line_of(source, match.start()),
                end_line=_end_line_for(source, match.end()),
                signature=f"class {name}",
            )
        )
    symbols = _dedupe(symbols)

    base = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    entry_points: list[str] = []
    if base in _MODULE_PATTERNS:
        entry_points.append("module-convention")

    return ParsedFile(
        path=path,
        language=language,
        loc=source.count("\n") + (0 if source.endswith("\n") or not source else 1),
        symbols=symbols,
        imports=imports,
        entry_points=entry_points,
    )


def parse_typescript(path: str, source: str) -> ParsedFile:
    """TS variant simply reuses the JS heuristic parser."""
    return parse_javascript(path, source, language="typescript")


__all__ = ["parse_javascript", "parse_typescript"]
