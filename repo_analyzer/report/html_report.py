"""Rich single-artifact HTML report builder for the ``analyze`` feature.

Relationship-heavy report: architecture diagrams, per-file import maps,
execution/data flows, symbol deep dives — every file reference linked to
GitHub (blob URL with line anchors) when the snapshot came from GitHub.
"""

from __future__ import annotations

import json
import re
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.features.architecture_feature import architecture_payload
from repo_analyzer.features.documentation import documentation_payload
from repo_analyzer.features.overview import deterministic_overview
from repo_analyzer.ingest import filters
from repo_analyzer.llm.schemas import (
    ArchitectureOut,
    Citation,
    DataFlow,
    DocsOut,
    OverviewOut,
    citation_lines,
)
from repo_analyzer.report.html import (
    data_footnote,
    esc,
    mermaid_diagram,
    render_html_report,
    section,
)

_STEP_RE = re.compile(r"^(?P<path>[^:\s]+)(?P<loc>:\d+(?:-\d+)?)?$")
_SIG_CAP = 140


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    """Render a table WITHOUT escaping — cells are pre-escaped HTML here."""
    if not rows:
        return "<p class='muted'>none</p>"
    head = "".join(f"<th>{esc(cell)}</th>" for cell in headers)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><tr>{head}</tr>{body_rows}</table>"


# --------------------------------------------------------------------- helpers

def _link(result: AnalyzedRepository, path: str, line: int | None = None, label: str = "") -> str:
    """Linkified ``path`` (or ``path:line``) cell content."""
    href = result.link_for(path, line)
    shown = escaped_path(path) + (f"<i>:{line}</i>" if line else "")
    if href:
        return f'<a href="{esc(href)}">{shown}</a>'
    return shown


def escaped_path(path: str) -> str:
    """Bold the basename of a path for scannability."""
    name = path.rsplit("/", 1)[-1]
    head = path[: len(path) - len(name) - 1] if "/" in path else ""
    prefix = f"{esc(head)}/" if head else ""
    return f"{prefix}<b>{esc(name)}</b>"


def _citation_cells(result: AnalyzedRepository, citations: list[Citation]) -> str:
    """One cell holding a list of linked citations."""
    if not citations:
        return "<span class='muted'>—</span>"
    items = []
    for cit in citations:
        label = esc(citation_lines(cit))
        href = result.link_for(cit.file, cit.line)
        note = f' <span class="muted">({esc(cit.note)})</span>' if cit.note else ""
        if href:
            items.append(f'<a href="{esc(href)}">{label}</a>{note}')
        else:
            items.append(f"{label}{note}")
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def _listed(result: AnalyzedRepository, paths: list[str], cap: int = 8) -> str:
    """Compact list of linked paths with trailing '+N more'."""
    shown = paths[:cap]
    overflow = len(paths) - len(shown)
    items = [_link(result, path) for path in shown]
    if overflow > 0:
        items.append(f'<span class="muted">+ {overflow} more</span>')
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>" if items else "<span class='muted'>none</span>"


def _flow_list(result: AnalyzedRepository, flow_name: str, steps: list[str]) -> str:
    """Deterministic flow rendered as an ordered list with linked steps."""
    if not steps:
        return "<p class='muted'>no import chain recorded</p>"
    items = []
    for step in steps:
        match = _STEP_RE.match(step)
        if match and match.group("path") in result.all_paths:
            loc_txt = match.group("loc") or ""
            items.append(_link(result, match.group("path")) + (loc_txt and f"<i>{esc(loc_txt)}</i>" or ""))
        else:
            items.append(esc(step))
    ordered = "".join(f"<li>{item}</li>" for item in items)
    return f"<h3>{esc(flow_name)}</h3><ol class='flow'>{ordered}</ol>"


def _llm_flow(result: AnalyzedRepository, flow: DataFlow) -> str:
    """LLM data flow as trigger + ordered steps with links."""
    rows = []
    for idx, step in enumerate(flow.steps, start=1):
        loc = f":{step.line}" if step.line else ""
        ref = _link(result, step.file) + (loc and f"<i>{esc(loc)}</i>" or "") if step.file else "—"
        rows.append((str(idx), ref, esc(step.description)))
    trigger = esc(flow.trigger) if flow.trigger else "<span class='muted'>—</span>"
    body = _table(["#", "ref", "step"], rows)
    return f"<h3>{esc(flow.name)}</h3><p><b>trigger:</b> {trigger}</p>{body}"


def _chart_blocks(result: AnalyzedRepository, diagrams: list[dict[str, Any]], notes: dict[str, str] | None = None) -> str:
    """All deterministic mermaid diagrams with captions and optional notes."""
    notes = notes or {}
    parts = []
    for diagram in diagrams:
        title = diagram.get("title") or ""
        block = f"<h3>{esc(title)}</h3>{mermaid_diagram(diagram.get('mermaid') or '')}"
        if title in notes:
            block += f"<p class='flow-note'>{esc(notes[title])}</p>"
        parts.append(block)
    return "".join(parts)


def _edge_map(result: AnalyzedRepository) -> tuple[dict[str, list[tuple[str, int]]], dict[str, list[tuple[str, int]]]]:
    """Outgoing (file -> dep, first-line) and reverse (file -> importer, first-line) edge maps."""
    outgoing: dict[str, list[tuple[str, int]]] = {}
    for src, dsts in result.graph.edges.items():
        rows: list[tuple[str, int]] = [
            (dst, (result.graph.edge_reasons.get((src, dst)) or [0])[0]) for dst in dsts
        ]
        rows.sort()
        outgoing[src] = rows
    incoming: dict[str, list[tuple[str, int]]] = {}
    for src, dsts in result.graph.edges.items():
        for dst in dsts:
            incoming.setdefault(dst, []).append((src, (result.graph.edge_reasons.get((src, dst)) or [0])[0]))
    for entries in incoming.values():
        entries.sort()
    return outgoing, incoming


def _paragraphs(text: str) -> str:
    """Escape and split narrative text into <p> blocks on blank lines."""
    return "".join(f"<p>{esc(part.strip())}</p>" for part in text.split("\n\n") if part.strip())


# -------------------------------------------------------------------- sections

def _meta_table(result: AnalyzedRepository) -> str:
    sha = result.commit_sha[:10] if result.commit_sha else "—"
    origin = result.full_name if result.is_github else "local directory"
    rows = [
        ("repository", esc(result.full_name)),
        ("origin", esc(origin)),
        ("ref / commit", esc(f"{result.ref} @ {sha}")),
        ("files analyzed", f"{result.file_count} ({result.total_loc} LOC)"),
        ("primary language", esc(result.primary_language())),
        ("frameworks", esc(", ".join(result.frameworks)) or "—"),
        ("parse errors", str(result.parse_errors)),
    ]
    if result.is_github:
        if result.stars or result.forks or result.open_issues:
            pushed = f" · pushed {esc(result.pushed_at[:10])}" if result.pushed_at else ""
            rows.append(
                (
                    "GitHub stats",
                    f"★ {result.stars} · forks {result.forks} · issues {result.open_issues}{pushed}",
                )
            )
        if result.github_language:
            rows.append(("GitHub language", esc(result.github_language)))
    table = _table(["field", "value"], rows)
    body = table + (f"<p>{esc(result.description)}</p>" if result.description else "")
    return section("Repository snapshot", body)


def _model_note(when_no_narrative: bool, message: str = "model layer unavailable for this section (credentials missing, LLM error, or response dropped)") -> str:
    """Muted explanatory note when an LLM layer was expected but failed."""
    return f"<p class='muted'>({esc(message)})</p>" if when_no_narrative else ""


def _overview_section(
    result: AnalyzedRepository,
    payload: dict[str, Any],
    overview: OverviewOut | None,
    llm_enabled: bool = False,
) -> str:
    stats = payload["stats"]
    languages = [
        (lang, files, loc) for lang, files, loc in result.languages
    ]
    lang_table = _table(
        ["language", "files", "LOC"], [(esc(lang), files, loc) for lang, files, loc in languages]
    )
    body = lang_table
    if payload.get("summary_text"):
        body += f"<p><b>quick digest:</b> {esc(payload['summary_text'])}</p>"
    if overview:
        body += f"<h3>{esc(overview.headline)}</h3>{_paragraphs(overview.summary)}"
        if overview.tech_stack:
            rows = [
                (
                    f"<b>{esc(item.name)}</b>",
                    esc(item.category),
                    _citation_cells(result, item.evidence),
                )
                for item in overview.tech_stack
            ]
            body += "<h3>technology stack (evidence-backed)</h3>" + _table(
                ["technology", "role", "evidence"], rows
            )
        if overview.key_files:
            body += "<h3>key files</h3>" + _citation_cells(result, overview.key_files)
        if overview.gotchas:
            gotchas = "".join(f"<li>{esc(g)}</li>" for g in overview.gotchas)
            body += f"<div class='warn'><h3>gotchas</h3><ul>{gotchas}</ul></div>"
    else:
        body += _model_note(llm_enabled, "model narrative unavailable for the overview")
    return section("Overview", body)


def _deps_section(
    result: AnalyzedRepository,
    payload: dict[str, Any],
    docs_payload: dict[str, Any],
) -> str:
    deps = result.dependencies
    rows = [
        (
            esc(ecosystem),
            len(names),
            esc(", ".join(sorted(names)[:20])) + (f" <span class='muted'>(+{len(names) - 20})</span>" if len(names) > 20 else ""),
        )
        for ecosystem, names in sorted(deps.ecosystems.items())
    ]
    body = _table(["ecosystem", "packages", "list"], rows) if rows else "<p class='muted'>no manifests found</p>"
    parts: list[str] = [body]
    if result.frameworks:
        pills = "".join("<span class='pill'>{}</span>".format(esc(name)) for name in result.frameworks)
        parts.insert(0, f"<p>{pills}</p>")
    stats = payload.get("stats", {})
    manifest_list = _listed(result, stats.get("manifests", []))
    config_list = _listed(result, stats.get("config_files", []))
    ci_list = _listed(result, stats.get("ci_files", []))
    entry_flow = docs_payload.get("entry_flow") or []
    doc_flow = _flow_list(result, "primary execution flow", entry_flow) if entry_flow else ""
    return section(
        "Dependencies & Packaging",
        "".join(parts)
        + f"<h3>manifests</h3>{manifest_list}<h3>configuration</h3>{config_list}<h3>CI / tooling</h3>{ci_list}{doc_flow}",
    )


def _architecture_section(
    result: AnalyzedRepository,
    payload: dict[str, Any],
    architecture: ArchitectureOut | None,
    llm_enabled: bool = False,
    diagram_notes: dict[str, str] | None = None,
) -> str:
    body = _chart_blocks(result, payload.get("diagrams") or [], diagram_notes)
    if architecture:
        body += f"<h3>{esc('architectural summary')}</h3>{_paragraphs(architecture.summary)}"
        if architecture.design_notes:
            notes = "".join(f"<li>{esc(note)}</li>" for note in architecture.design_notes)
            body += f"<h3>design notes</h3><ul>{notes}</ul>"
        if architecture.components:
            comp_rows = [
                (f"<b>{esc(info.name)}</b>", _paragraphs(info.description), _citation_cells(result, info.files))
                for info in architecture.components
            ]
            body += "<h3>components (LLM-curated)</h3>" + _table(["component", "responsibility", "files"], comp_rows)
    else:
        rows = [
            (
                f"<b>{esc(comp.name)}</b>",
                _listed(result, comp.files, cap=6),
                str(sum(len(result.parsed[path].symbols) for path in comp.files if path in result.parsed)),
            )
            for comp in result.components
        ]
        body += "<h3>components (heuristic)</h3>" + _table(["component", "files", "symbols"], rows)
        body += _model_note(llm_enabled, "LLM-curated component narrative unavailable")
    layers = payload.get("layers") or []
    if layers:
        layer_rows = [
            (f"<b>{esc(row['component'])}</b>", esc(row["layer"]), str(row["files"])) for row in layers
        ]
        body += "<h3>architecture layers (heuristic)</h3>" + _table(
            ["component", "inferred layer", "files"], layer_rows
        )
    fan = payload.get("fan_in_out") or []
    if fan:
        fan_rows = [(_link(result, row["path"]), row["imported_by"], row["imports"]) for row in fan]
        body += "<h3>coupling hotspots (top by fan-in)</h3>" + _table(["file", "imported by", "imports"], fan_rows)
    cycles = payload.get("cycles") or []
    if cycles:
        cycle_items = []
        for cycle in cycles:
            cycle_items.append("<li>" + " → ".join(_link(result, path) for path in cycle) + "</li>")
        body += f"<div class='warn'><h3>import cycles</h3><ul>{''.join(cycle_items)}</ul></div>"
    return section("Architecture", body)


def _flows_section(
    result: AnalyzedRepository,
    payload: dict[str, Any],
    architecture: ArchitectureOut | None,
    llm_enabled: bool = False,
) -> str:
    parts: list[str] = []
    for flow in payload["flows"]:
        parts.append(_flow_list(result, flow["name"], flow["steps"]))
    for flow in result.flows[len(payload["flows"]):]:
        parts.append(_flow_list(result, flow.name, flow.steps))
    if architecture:
        for flow in architecture.data_flows:
            parts.append(_llm_flow(result, flow))
    else:
        parts.append(_model_note(llm_enabled, "LLM data-flow narratives unavailable"))
    if not parts:
        parts.append("<p class='muted'>no executable entry points detected</p>")
    return section("Execution & Data Flows", "".join(parts))


def _relationships_section(result: AnalyzedRepository) -> str:
    """Per-file import/export relationship deep dive."""
    outgoing, incoming = _edge_map(result)
    degrees = {
        path: (len(incoming.get(path, ())), len(outgoing.get(path, ())))
        for path in result.graph.nodes
    }
    interesting = sorted(
        ((path, fin, fout) for path, (fin, fout) in degrees.items() if fin + fout > 0),
        key=lambda item: (-(item[1] + item[2]), item[0]),
    )[:40]
    if not interesting:
        return section("File Relationships", "<p class='muted'>no inter-file imports detected</p>")

    def edge_cell(entries: list[tuple[str, int]], total: int) -> str:
        cell = "<ul>" + "".join(
            f"<li>{_link(result, dst, line)}</li>" for dst, line in entries[:6]
        ) + (f'<li><span class="muted">+ {total - 6} more</span></li>' if total > 6 else "") + "</ul>"
        return cell if entries else "<span class='muted'>—</span>"

    rows = []
    for path, fin, fout in interesting:
        rows.append(
            (
                _link(result, path),
                esc(result.parsed[path].symbols[0].name) if path in result.parsed and result.parsed[path].symbols else "—",
                fin,
                fout,
                edge_cell(incoming.get(path, []), fin),
                edge_cell(outgoing.get(path, []), fout),
            )
        )
    body = _table(
        ["file", "main symbol", "in", "out", "imported by (file:line)", "imports (file:line)"],
        rows,
    )
    orphans = result.graph.orphans()
    if orphans:
        body += f"<h3>orphans (no imports either way)</h3>{_listed(result, sorted(orphans), cap=10)}"
    return section("File Relationships", body)


def _components_section(result: AnalyzedRepository) -> str:
    """Per-component file/symbol deep dive."""
    if not result.components:
        return section("Modules Deep Dive", "<p class='muted'>no components derived</p>")
    blocks: list[str] = []
    for comp in result.components:
        rows = []
        for path in sorted(comp.files):
            parsed = result.parsed.get(path)
            loc = result.file_stats.get(path) or result.file_sizes.get(path, 0)
            if parsed and parsed.symbols:
                symbols = sorted(
                    parsed.symbols,
                    key=lambda sym: (0 if sym.kind in {"class"} else 1, sym.start_line),
                )
                top = " · ".join(
                    f"{esc(sym.name)}<i>({esc(sym.kind)}:{sym.start_line})</i>"
                    for sym in symbols[:8]
                )
                more = f" <span class='muted'>+{len(symbols) - 8}</span>" if len(symbols) > 8 else ""
                symbol_cell = top + more
                sym_count = len(symbols)
            else:
                symbol_cell = "<span class='muted'>—</span>"
                sym_count = 0
            if parsed and parsed.imports:
                modules = sorted({ref.module for ref in parsed.imports})
                ext_cell = "<ul>" + "".join(f"<li>{esc(name)}</li>" for name in modules[:5]) + (f'<li><span class="muted">+ {len(modules) - 5}</span></li>' if len(modules) > 5 else "") + "</ul>"
            else:
                ext_cell = ""
            rows.append((_link(result, path), esc(parsed.language if parsed else filters.language_for_path(path)), loc, sym_count, symbol_cell, ext_cell))
        table = _table(
            ["file", "lang", "LOC", "symbols", "key symbols (kind:line)", "imported modules"], rows
        )
        blocks.append(
            f"<details open><summary><b>{esc(comp.name)}</b> — {len(comp.files)} files</summary>{table}</details>"
        )
    return section("Modules Deep Dive", "".join(blocks))


def _docs_section(
    result: AnalyzedRepository,
    docs_payload: dict[str, Any],
    docs: DocsOut | None,
    llm_enabled: bool = False,
) -> str:
    dir_rows = [
        (
            esc(row["path"]),
            row["files"],
            row["symbols"],
            esc(row["purpose"]),
            _listed(result, row["important_files"], cap=3),
        )
        for row in docs_payload.get("directories", [])
    ]
    body = _table(["directory", "files", "symbols", "inferred purpose", "key files"], dir_rows)
    if docs:
        body += f"<h3>{esc('codebase summary')}</h3>{_paragraphs(docs.summary)}"
        if docs.directories:
            llm_rows = [
                (esc(entry.path), _paragraphs(entry.purpose), _citation_cells(result, entry.important_files))
                for entry in docs.directories
            ]
            body += "<h3>directory guide (LLM-curated)</h3>" + _table(
                ["directory", "responsibility", "important files"], llm_rows
            )
        if docs.execution_flow:
            ordered = "".join(
                f"<li>{_link(result, step.file)}{f'<i>:{step.line}</i>' if step.line else ''} — {esc(step.description)}</li>"
                for step in docs.execution_flow
            )
            body += f"<h3>end-to-end execution walk-through</h3><ol class='flow'>{ordered}</ol>"
    else:
        body += _model_note(llm_enabled, "LLM directory narrative unavailable")
    return section("Directory Guide", body)


def _tech_choices_section(docs: DocsOut | None, llm_enabled: bool = False) -> str:
    if not docs or not docs.tech_choices:
        sections_empty = _model_note(llm_enabled, "engineering decisions narrative unavailable")
        return section("Engineering Decisions", sections_empty) if llm_enabled else ""
    rows = [
        (f"<b>{esc(choice.technology)}</b>", _paragraphs(choice.rationale), esc(choice.trade_offs))
        for choice in docs.tech_choices
    ]
    table = _table(["technology", "why it was chosen", "trade-offs"], rows)
    return section("Engineering Decisions", table)


def _entry_section(result: AnalyzedRepository) -> str:
    rows = [
        (_link(result, entry.path), esc(entry.kind), f"{entry.confidence:.2f}", esc(entry.reason))
        for entry in result.entry_points
    ]
    return section("Entry Points", _table(["file", "kind", "confidence", "why"], rows))


def _inventory_section(result: AnalyzedRepository) -> str:
    rows = []
    for path in result.all_paths:
        loc = result.file_stats.get(path)
        rows.append(
            (
                _link(result, path),
                esc(filters.language_for_path(path)),
                loc if loc is not None else "",
                f"{result.file_sizes.get(path, 0) / 1024:.1f}",
            )
        )
    table = _table(["file", "language", "LOC", "KB"], rows)
    details = f"<details><summary>show all {len(rows)} files</summary>{table}</details>"
    return section("File Inventory", details)


# ---------------------------------------------------------------------- public

def build_full_report(
    result: AnalyzedRepository,
    overview: OverviewOut | None,
    architecture: ArchitectureOut | None,
    docs: DocsOut | None,
    llm_enabled: bool = False,
    diagram_notes: dict[str, str] | None = None,
) -> str:
    """Assemble the single-artifact HTML report with every analysis layer."""
    payload = deterministic_overview(result)
    arch_payload = architecture_payload(result)
    docs_payload = documentation_payload(result)
    sections = [
        _meta_table(result),
        _overview_section(result, payload, overview, llm_enabled),
        _deps_section(result, payload, docs_payload),
        _architecture_section(result, arch_payload, architecture, llm_enabled, diagram_notes),
        _flows_section(result, arch_payload, architecture, llm_enabled),
        _relationships_section(result),
        _components_section(result),
        _docs_section(result, docs_payload, docs, llm_enabled),
        _tech_choices_section(docs, llm_enabled),
        _entry_section(result),
        _inventory_section(result),
    ]
    lang_count = len(result.languages)
    smart_note = ""
    if architecture:
        smart_note = f" · {len(architecture.components)} components · {len(architecture.data_flows)} data flows"
    subtitle = (
        f"{result.full_name} @ {result.ref}"
        f" · {result.file_count} files · {result.total_loc} LOC"
        f" · {lang_count} languages · {len(result.components)} component folders{smart_note}"
    )
    raw = {
        "stats": payload.get("stats", {}),
        "layers": arch_payload.get("layers"),
        "fan_in_out": arch_payload.get("fan_in_out"),
        "cycles": arch_payload.get("cycles"),
        "dependencies": list(result.dependencies.ecosystems.keys()),
    }
    sections.append(data_footnote(raw))
    return render_html_report(
        f"Code Report:{result.full_name}",
        subtitle,
        sections,
    )


__all__ = ["build_full_report", "citation_lines"]
