"""Public-API reference: deterministic symbol inventory + LLM descriptions."""

from __future__ import annotations

import json
from typing import Any

from repo_analyzer.analyzer import AnalyzedRepository
from repo_analyzer.features.context import sanitize_for_prompt
from repo_analyzer.llm.schemas import ApiDocsOut, ApiModuleDoc, ApiSymbolDoc
from repo_analyzer.llm.service import LLMService

_MAX_MODULES = 8
_MAX_SYMBOLS_PER_MODULE = 14

_API_SYSTEM = """You generate a public-API reference from the exact symbol inventory supplied.
Rules:
- `modules` must use the exact module paths provided in the inventory.
- `symbols` entries must use the exact symbol names provided; explain behavior from the signatures/docstrings given. Never invent additional symbols.
- Keep each description to one sentence.
- Output only JSON matching: {"summary": str, "modules": [{"module": str, "role": str, "symbols": [{"name": str, "signature": str, "description": str}]}]}
"""


def api_inventory(result: AnalyzedRepository) -> list[dict[str, Any]]:
    """Public (non-underscore) symbols grouped by file, capped to the biggest modules."""
    by_module: dict[str, list[dict[str, Any]]] = {}
    for path, parsed in result.parsed.items():
        rows = []
        for symbol in parsed.symbols:
            if symbol.name.startswith("_") and not symbol.name.startswith("__"):
                continue
            sig = symbol.signature.strip() or symbol.name
            if len(sig) > 120:
                sig = sig[:120] + "…"
            lines = max(1, symbol.end_line - symbol.start_line + 1)
            doc = ""
            for chunk in (symbol.doc or "").strip().splitlines():
                if chunk.strip():
                    doc = chunk.strip()[:160]
                    break
            rows.append(
                {
                    "name": symbol.name,
                    "kind": symbol.kind,
                    "signature": sig,
                    "lines": lines,
                    "doc": doc,
                }
            )
        if rows:
            rows.sort(key=lambda row: row["name"].lower())
            by_module[path] = rows
    ranked = sorted(by_module.items(), key=lambda item: -len(item[1]))
    return [
        {"module": path, "symbols": rows[:_MAX_SYMBOLS_PER_MODULE], "symbol_count": len(rows)}
        for path, rows in ranked[:_MAX_MODULES]
    ]


def api_docs_payload(result: AnalyzedRepository, llm: LLMService | None = None) -> dict[str, Any]:
    """Inventory payload plus optional LLM module roles/descriptions."""
    inventory = api_inventory(result)
    payload: dict[str, Any] = {
        "summary": "",
        "inventory": inventory,
        "modules": [],
        "llm_enriched": False,
    }
    if not inventory:
        payload["summary"] = "No parsed symbols found; nothing to document."
        return payload

    if llm is not None:
        evidence = (
            f"Repository: {result.full_name} ({result.primary_language()}, ref {result.ref})\n"
            f"Public symbol inventory ({len(inventory)} modules):\n"
            + "\n".join(
                f"## {entry['module']} ({entry['symbol_count']} public symbols)\n"
                + "\n".join(
                    f"- [{row['kind']}] {row['name']} :: {row['signature']}"
                    + (f" // doc: {row['doc']}" if row["doc"] else "")
                    for row in entry["symbols"]
                )
                for entry in inventory
            )
            + "\n\nRaw docstrings may be truncated; rely on signatures + doc evidence only.\n"
            + sanitize_for_prompt("")
        )
        try:
            out, _provider = llm.generate(ApiDocsOut, _API_SYSTEM, evidence, max_output_tokens=2600)
        except Exception:
            out = None
        if out is not None:
            known_modules = {entry["module"] for entry in inventory}
            known_symbols = {
                entry["module"]: {row["name"] for row in entry["symbols"]} for entry in inventory
            }
            clean_modules: list[ApiModuleDoc] = []
            for module in out.modules:
                if module.module not in known_modules:
                    continue
                module.symbols = [sym for sym in module.symbols if sym.name in known_symbols[module.module]]
                if module.symbols:
                    clean_modules.append(module)
            payload["modules"] = [module.model_dump() for module in clean_modules]
            payload["summary"] = out.summary
            payload["llm_enriched"] = bool(clean_modules)
            return payload

    payload["summary"] = (
        f"{len(inventory)} module(s) with public symbols (deterministic inventory; "
        "no LLM descriptions available)."
    )
    return payload


__all__ = ["api_docs_payload", "api_inventory"]
