# repo-analyzer

**AI-powered GitHub repository analyzer** — point it at any repo and get a single,
self-contained HTML report: architecture diagrams, per-file relationship maps,
execution & data flows, symbol deep dives and engineering-decision notes — with
every claim pinned to a clickable `file:line` citation on GitHub.

Deterministic static analysis does the heavy lifting (graph building, entry-point
detection, dependency extraction, cycle detection). An LLM (Gemini, OpenRouter,
or Groq) only *narrates* on top of that evidence — never invents it.

```text
$ repo-analyzer analyze https://github.com/pallets/flask
 Overview                                   
 repository       pallets/flask
 ref              main
 files            225 · total LOC ~19,000 · parse errors 0
 primary language python · frameworks Flask
 entry points     src/flask/app.py, CLI, examples/

 report written: reports/pallets__flask.html
```

---

## Features

| Command | What it gives you |
| --- | --- |
| `analyze` | The full single-artifact HTML report (12 sections below) |
| `architecture` | Mermaid component/dependency diagrams + narrative in terminal |
| `docs` | Directory responsibilities, tech choices, execution walk-through |
| `ask` | Grounded Q&A — answers cite exact files & lines |
| `bug` | Bug-investigation assistant: suspect files, blast radius, suggested tests |
| `config` | Show resolved config with API keys masked |
| `clean` | Delete the analysis cache (and optionally reports) |
| `version` | Installed version |

**Handy `analyze` flags:** `--quiet` (scripting — prints just the report path),
`--out reports` writes `<name>.html`, `--csv inventory.csv`,
`--refresh`, `--no-llm`, `--open` (open the report in your browser).
The HTML report stars/forks/issues/language from GitHub metadata in its header.

**The HTML report anatomy** (sections, all cross-linked):

1. **Repository snapshot** — ref, commit, LOC, frameworks, GitHub stars/forks/issues/language
2. **Overview** — evidence-backed tech stack, key files, gotchas
3. **Dependencies & Packaging** — manifests, configs, CI files, primary flow
4. **Architecture** — mermaid component/dependency/cycle diagrams + LLM narrative
5. **Execution & Data Flows** — traced import chains + LLM data-flow step tables
6. **File Relationships** — fan-in/fan-out maps: who imports what, at which line
7. **Modules Deep Dive** — per component: files, LOC, symbols, imported modules
8. **Directory Guide** — heuristic + LLM-curated responsibilities
9. **Engineering Decisions** — technology → rationale → trade-offs
10. **Entry Points** — detected by flags, symbols, and graphs
11. **File Inventory** — every file with language/LOC/size (collapsible)
12. **Raw data** — the underlying JSON for debugging

Every file reference links to `github.com/<owner>/<repo>/blob/<sha>/<path>#L<line>`.

## Supported languages

Python · JavaScript · TypeScript · Go · Java (function/class extraction, import
resolution, call-edge tracing, entry-point flags) — home-grown extractors in
`repo_analyzer/parsing/`, no parser packages vendored. Config/data formats
(JSON/YAML/TOML/INI) and docs are indexed but not parsed for symbols.

**Language analysis runs entirely offline/locally** — the GitHub tarball is the
only network dependency. LLM features are optional (`--no-llm`).

## Quickstart

```bash
git clone <this-repo> && cd Python_Capstone_Project
python -m venv .venv && source .venv/bin/activate   # optional
pip install -e .

cp .env.example .env          # add your Groq API key (free), see table below
repo-analyzer analyze https://github.com/owner/repo
```

Run from source in the project directory via `python -m repo_analyzer.cli <cmd>`, or via the `repo-analyzer` entrypoint.

## LLM providers (any one suffices)

Put a key into `.env`:

```bash
GROQ_API_KEY=gsk_...
OPENROUTER_API_KEY=sk-or-...
GEMINI_API_KEY=AIz...
```

| Provider | Default model | Env model override |
| --- | --- | --- |
| Groq (free tier) | `qwen/qwen3.8-27b` | `GROQ_MODEL` |
| OpenRouter | `openai/gpt-4o-mini` | `OPENROUTER_MODEL` |
| Gemini | `gemini-2.5-flash` | `GEMINI_MODEL` |

- **Auto-configuration**: whichever key exists gets injected; multiple keys
  follow the engine's fallback chain `gemini → openrouter → groq`.
- Config-file overrides: copy `config.example.toml` and point
  `REPO_ANALYZER_CONFIG` at it.
- LLM calls are: schema-validated (pydantic), retried on invalid JSON (same
  provider, up to 2), then fall through the chain. No key? Everything still
  works with `--no-llm` (deterministic mode).

## Commands

```bash
# Core analyses
repo-analyzer analyze <target> [--local] [--refresh] [--out reports] [--no-llm]
repo-analyzer architecture <target> [--out reports]     # diagrams + narrative + --no-llm mode
repo-analyzer docs <target> [--local] [--out reports]   # responsibilities + tech choices

# Q&A and bug triage
repo-analyzer ask <target> "How does flash messages flow?"     # grounded, cited answer
repo-analyzer bug <target> "<error message/symptom>" --json    # + table-form suspects/impact
repo-analyzer bug <target> "…" --no-llm      # graph-based: suspects, impact, test ranking

# Utilities
repo-analyzer config               # resolved providers/models (keys masked)
repo-analyzer clean [--yes]        # wipe cache (+reports); --no-reports to keep reports
repo-analyzer version
repo-analyzer analyze <target> --quiet --csv inventory.csv --open
```

Targets: full GitHub URL, `owner/repo`, or a local directory path with `--local`.

## Configuration

Set in `.env`:

| Variable | Purpose |
| --- | --- |
| `REPO_ANALYZER_CACHE` | Override the cache root (default `repo_analyzer_cache/`, git-ignored) |
| `REPO_ANALYZER_CONFIG` | Path to an alternative `config.toml` (copy `config.example.toml`) |
| `REPO_ANALYZER_PROVIDER` | pin the LLM provider by name |
| `GITHUB_TOKEN` | optional: GitHub metadata API higher rate limit |

Tuning (pack budgets, summary toggles…) lives in `[limits]` / `[analysis]` /
`[provider_defaults]` tables in `config.example.toml` — e.g.
`pack_char_budget` caps each LLM context pack (~26,000 chars default).

Caching: parse results are content-hash keyed, full analysis snapshots stored in
SQLite, and analyses pin to the commit sha — re-runs are near-instant and can be
fully offline; `--refresh` forces re-analysis.

## Report generation details

- Deterministic extractors (`parsing/`): python/ast, JS/TS via a custom
  grammar-walker, go/java via the c-family parser — symbols, docstrings,
  entry flags (CLI/app/test), per-import references all offline.
- Mermaid diagrams render via CDN when viewed in a browser; the file itself is
  a single self-contained HTML doc you can email or archive.
- `--refresh` is only performance: correctness never depends on the cache.

## Costs & limits

- The **static pipeline** (graph, components, cycles, inventory) is zero-cost
  and runs even with no API keys at all (`--no-llm`).
- LLM enrichment: one call per narrative feature (overview, architecture,
  docs/engineering decisions, QA, bug triage) with a ~26k-char context pack,
  JSON-mode schema validation, and auto-retry/fallback chains.
- Default Groq free tier (~30 req/min, ~200K tokens/day per model) is far more
  than a normal analyze run needs.

## Trust & privacy

- Secrets in prompts are masked (`sanitize_for_prompt` replaces env-var style
  assignments, api keys, etc.). See `repo_analyzer/features/context.py`.
- Only repo *metadata + tarball + LLM narrative* cross the wire; diffs and
  token-by-token source never leave your machine except the LLM context pack.
- All citations are validated (file must exist, line ≤ actual length) before
  reaching the report.

## Project layout

```text
repo_analyzer/
├── analyzer.py             # orchestrates: ingest → parse → graph → cache snapshots
├── cli.py                  # typer entrypoint for the five commands
├── config.py               # AppConfig + provider auto-injection + fallback chain
├── github/                 # client (httpx), URL parsing, API models
├── ingest/                 # tarball extraction, filters, secret scrubbing
├── parsing/                # language extractors → ParsedFile (symbols, imports)
├── analysis/               # module graph, cycles, entry points, components, layers, diagrams
├── index/                  # SQLite cache store (blob-hash keyed)
├── llm/                    # provider clients, retry/fallback service, pydantic schemas
├── features/               # overview, architecture, documentation, qa, contributor, context
└── report/                 # single-artifact HTML report builders
tests/                      # 121 offline tests incl. small fixture repo
ARCHITECTURE.md             # deep-dive module docs + invariants for contributors
```

## Development

```bash
pip install -e ".[dev]"
pytest --collect-only -q    # 121 tests, all offline (fixture repo + mock transports)
pytest -q                   # full offline suite (~1 min)
```

The test suite mocks GitHub (httpx `MockTransport`) and LLM (JSON stubs) so CI
needs no keys. Live key usage only happens when *you* run the CLI.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the module-by-module design,
invariants, and a "pick up here" worklist for contributors.

## License

MIT — see [LICENSE](LICENSE).
