"""AST-based Python parser producing symbols, imports, and calls."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from typing import Any

from repo_analyzer.parsing.symbols import ImportRef, ParsedFile, Symbol, first_doc_line

_ENTRY_MARKERS = ("if __name__", "uvicorn.run", "app.run(", "main()")


def _unparse_target(node: ast.expr) -> str:
    """Render an import target (module or dotted name) defensively."""
    try:
        return ast.unparse(node)
    except (ValueError, AttributeError, RecursionError):
        return ""


def _arg_names(args: ast.arguments) -> list[str]:
    """Collect parameter names in order (positional, kw-only, vararg, kwarg)."""
    names: list[str] = []
    for arg in list(args.posonlyargs) + list(args.args):
        if arg.arg != "self" and arg.arg != "cls":
            names.append(arg.arg)
        else:
            names.append(arg.arg)
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    for arg in args.kwonlyargs:
        names.append(arg.arg)
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    return names


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    """One-line signature or class bases string."""
    try:
        text = ast.unparse(node)
    except (ValueError, AttributeError, RecursionError):
        text = ""
    if text:
        head = text.split("\n", 1)[0]
        head = re.sub(r"\s+", " ", head).strip()
        return head[:160]
    return node.name


def _first_line_number(node: ast.AST, fallback: int) -> int:
    """Line where a definition visually starts (decorators included)."""
    lineno = getattr(node, "lineno", fallback)
    decorators = getattr(node, "decorator_list", None) or []
    if decorators:
        lineno = min(lineno, min(getattr(dec, "lineno", lineno) for dec in decorators))
    return int(lineno)


def _call_names(node: ast.Call) -> Iterator[str]:
    """Yield plausible callee identifiers from a call node."""
    target = node.func
    if isinstance(target, ast.Name):
        yield target.id
        return
    parts: list[str] = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    if parts:
        yield ".".join(reversed(parts))


class _Visitor(ast.NodeVisitor):
    """Collects symbols, imports, and call edges from one module."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRef] = []
        self.calls: list[tuple[str, int]] = []  # (callee, line)
        self.scope: list[str] = []

    # -- symbols ---------------------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        bases = [_unparse_target(base) for base in node.bases]
        bases = [base for base in bases if base]
        signature = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
        self.symbols.append(
            Symbol(
                name=node.name,
                kind="class",
                path=self.path,
                start_line=_first_line_number(node, node.lineno),
                end_line=node.end_lineno or node.lineno,
                signature=signature,
                doc=first_doc_line(ast.get_docstring(node) or ""),
                decorators=[_decorator_text(dec) for dec in node.decorator_list],
            )
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:  # noqa: N802
        kind = "method" if self.scope else "function"
        is_async = isinstance(node, ast.AsyncFunctionDef)
        prefix = "async def" if is_async else "def"
        params = _arg_names(node.args)
        signature = f"{prefix} {node.name}({', '.join(params)})"
        self.symbols.append(
            Symbol(
                name=node.name,
                kind=kind,
                path=self.path,
                start_line=_first_line_number(node, node.lineno),
                end_line=node.end_lineno or node.lineno,
                signature=signature,
                doc=first_doc_line(ast.get_docstring(node) or ""),
                decorators=[_decorator_text(dec) for dec in node.decorator_list],
            )
        )
        self.scope.append(node.name)
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.visit_FunctionDef(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        # Module-level UPPER_CASE assignments become constants.
        if not self.scope and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.isupper() and len(name) > 1:
                self.symbols.append(
                    Symbol(
                        name=name,
                        kind="constant",
                        path=self.path,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        signature=f"{name} = ...",
                    )
                )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if not self.scope and isinstance(node.target, ast.Name) and node.target.id.isupper():
            self.symbols.append(
                Symbol(
                    name=node.target.id,
                    kind="constant",
                    path=self.path,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=f"{node.target.id}: ...",
                )
            )
        self.generic_visit(node)

    # -- calls -----------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        for name in _call_names(node):
            self.calls.append((name, node.lineno))
        self.generic_visit(node)

    # -- imports -----------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self.imports.append(ImportRef(module=alias.name, names=[], line=node.lineno, kind="import"))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = ("." * (node.level or 0)) + (node.module or "")
        names = [alias.name for alias in node.names]
        self.imports.append(ImportRef(module=module, names=names, line=node.lineno, kind="from"))
        self.generic_visit(node)


def _decorator_text(node: ast.expr) -> str:
    """Render a decorator expression to a short string."""
    try:
        return ("@" + ast.unparse(node))[:80]
    except (ValueError, AttributeError, RecursionError):
        return "@"


def parse_python(path: str, source: str) -> ParsedFile:
    """Parse ``source``; syntax errors yield a file with ``parse_error`` set."""
    loc = source.count("\n") + (0 if source.endswith("\n") or not source else 1)
    visitor = _Visitor(path)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ParsedFile(
            path=path,
            language="python",
            loc=loc,
            parse_error=f"syntax error line {exc.lineno}: {exc.msg}",
        )
    visitor.visit(tree)
    entry_points = _detect_entry_points(source, [symbol.name for symbol in visitor.symbols])
    return ParsedFile(
        path=path,
        language="python",
        loc=loc,
        symbols=visitor.symbols,
        imports=visitor.imports,
        entry_points=entry_points,
    )


def _detect_entry_points(source: str, symbol_names: list[str]) -> list[str]:
    """Flag files that look like runnable entry points."""
    flags: list[str] = []
    if re.search(r'^if __name__ == ["\']__main__["\']', source, re.M):
        flags.append("dunder-main")
    if "uvicorn.run" in source or "app.run(" in source:
        flags.append("server-bootstrap")
    if re.search(r"\bdef main\s*\(", source):
        flags.append("main-function")
    if "__entry__" in symbol_names:  # pragma: no cover - defensive
        flags.append("declared-entry")
    return flags


__all__ = ["parse_python"]
