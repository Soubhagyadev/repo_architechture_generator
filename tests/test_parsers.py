"""Parser tests: Python AST, JS/TS heuristics, Go, Java."""

from __future__ import annotations

from repo_analyzer.parsing.c_family import parse_go, parse_java
from repo_analyzer.parsing.js_ts_parser import parse_javascript
from repo_analyzer.parsing.python_parser import parse_python
from repo_analyzer.parsing.registry import parse_file, SUPPORTED_LANGUAGES

MAIN_PY = '''"""Module docstring."""
import json
from os import path

MAX_RETRIES = 3
_threshold: int = 10


class Engine:
    def __init__(self, size: int):
        self.size = size

    @staticmethod
    def prime() -> int:
        """Return the prime."""
        return 1


def startup(name: str = "app") -> None:
    """Boots the engine."""
    print(name)


if __name__ == "__main__":
    startup()
'''


def test_python_symbols_and_imports() -> None:
    parsed = parse_python("app/engine.py", MAIN_PY)
    assert parsed.parse_error == ""
    names = {symbol.name: symbol for symbol in parsed.symbols}
    assert names["Engine"].kind == "class"
    assert names["Engine"].signature == "class Engine"
    assert names["Engine"].doc == ""  # module docstring is not the class docstring
    assert names["prime"].kind == "method"
    assert names["prime"].doc == "Return the prime."
    assert names["startup"].kind == "function"
    assert "def startup(name)" in names["startup"].signature
    constant = next(symbol for symbol in parsed.symbols if symbol.name == "MAX_RETRIES")
    assert constant.kind == "constant"
    modules = {imp.module for imp in parsed.imports}
    assert "json" in modules
    assert "os" in modules
    kinds = {imp.module: imp.kind for imp in parsed.imports}
    assert kinds["json"] == "import"
    assert kinds["os"] == "from"


def test_python_entry_point_flags() -> None:
    parsed = parse_python("app/main.py", MAIN_PY + "\nimport uvicorn\nuvicorn.run(app)\n")
    assert "dunder-main" in parsed.entry_points
    assert "server-bootstrap" in parsed.entry_points


def test_python_decorators_recorded() -> None:
    source = "@app.get('/x')\ndef handler() -> None:\n    pass\n"
    parsed = parse_python("app/route.py", source)
    handler = parsed.symbols[0]
    assert handler.decorators == ["@app.get('/x')"]
    assert handler.start_line <= handler.end_line


def test_python_syntax_error_is_soft() -> None:
    parsed = parse_python("bad.py", "def oops(:\n")
    assert parsed.parse_error != ""
    assert parsed.symbols == []


def test_javascript_requires_and_functions() -> None:
    source = """const http = require('http');
const { Router } = require('./router');
import { render } from './ui';

function boot() { return 1; }
const handler = async (event) => { return event; };

class Server {
  start() { }
}

module.exports = { boot };
"""
    parsed = parse_javascript("src/server.js", source)
    modules = {imp.module for imp in parsed.imports}
    assert "http" in modules
    assert "./router" in modules
    assert "./ui" in modules
    names = {symbol.name for symbol in parsed.symbols}
    assert {"boot", "handler", "Server"} <= names


def test_javascript_convention_entry() -> None:
    parsed = parse_javascript("web/index.js", "module.exports = 1;\n")
    assert "module-convention" in parsed.entry_points


def test_go_parser() -> None:
    source = """package main

import (
    "fmt"
    "net/http"
)

type Server struct{ Port int }

func main() {
    fmt.Println("hi")
}

func serve(port int) error { return nil }
"""
    parsed = parse_go("cmd/server/main.go", source)
    symbols = {symbol.name: symbol for symbol in parsed.symbols}
    assert symbols["main"].kind == "function"
    assert symbols["Server"].signature == "type Server struct"
    modules = {imp.module for imp in parsed.imports}
    assert {"fmt", "net/http"} <= modules
    assert "go-main" in parsed.entry_points


def test_java_parser() -> None:
    source = """package com.example;

import java.util.List;

public class Api {
    public int handle(String input) { return 0; }
}
"""
    parsed = parse_java("src/main/java/com/example/Api.java", source)
    symbols = {symbol.name: symbol for symbol in parsed.symbols}
    assert symbols["Api"].kind == "class"
    assert symbols["handle"].kind == "method"
    assert any(imp.module == "java.util.List" for imp in parsed.imports)


def test_registry_dispatch_and_stub() -> None:
    parsed = parse_file("x.py", "python", "a = 1\n")
    assert parsed.language == "python"
    unsupported = parse_file("x.rb", "ruby", "puts 1\n")
    assert unsupported.symbols == []
    assert "java" in SUPPORTED_LANGUAGES
