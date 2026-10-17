"""Parser registry dispatching by file extension."""

from __future__ import annotations

from repo_analyzer.parsing.c_family import parse_go, parse_java
from repo_analyzer.parsing.js_ts_parser import parse_javascript, parse_typescript
from repo_analyzer.parsing.python_parser import parse_python
from repo_analyzer.parsing.symbols import ParsedFile, PARSER_VERSION

type ParseFn = "callable[[str, str], ParsedFile]"


def parse_file(path: str, language: str, source: str) -> ParsedFile:
    """Parse ``source`` with the parser registered for ``language``.

    Unsupported languages return a stub :class:`ParsedFile` with no symbols,
    which callers treat as "not analyzable in depth".
    """
    if language == "python":
        return parse_python(path, source)
    if language in {"javascript", "framework-vue", "framework-svelte"}:
        return parse_javascript(path, source)
    if language == "typescript":
        return parse_typescript(path, source)
    if language == "go":
        return parse_go(path, source)
    if language == "java":
        return parse_java(path, source)
    return ParsedFile(path=path, language=language, loc=source.count("\n") + 1)


SUPPORTED_LANGUAGES = {"python", "javascript", "typescript", "go", "java"}

__all__ = ["PARSER_VERSION", "SUPPORTED_LANGUAGES", "parse_file"]
