# ARCHITECTURE.md — repo-analyzer project map & handoff

> **Read this first in any new session.** It describes the system layout,
> what is implemented and verified, where each concern lives, and what is
> left to do. Last updated: 2026-10-17.

## 1. What this project is

A Python CLI tool that analyzes GitHub repositories (or local directories)
and produces: tech-stack overview, architecture diagrams (Mermaid), module
relationship graphs, execution-flow explanations, source-grounded Q&A with
`file:line` citations, and bug-investigation guidance.

Two-layer design:

1. **Deterministic static analysis** (no LLM): archive fetch/extract →
   file filtering → AST/regex parsing → import-graph construction →
   entry points → diagrams. Always available.
2. **Optional LLM enrichment** (Gemini / OpenRouter): feature code sends a
   small, budgeted, *source-grounded* context pack (never the whole repo) and
   validates the response against Pydantic schemas; invalid/citation-failing
   responses are retried with error feedback, then all providers fall back,
   then features degrade to deterministic output.

## 2. Layout & module map

```
Python_Capstone_Project/
├── repo_analyzer/                  Core package
│   ├── analyzer.py         ★  RepoAnalyzer orchestrator; AnalyzedRepository dataclass
│   │                          analyze(target, is_local, refresh) -> AnalyzedRepository
│   ├── cli.py               ★  typer CLI: analyze / architecture / docs / ask / bug
│   ├── config.py               AppConfig (pydantic); env: REPO_ANALYZER_CONFIG,
│   │                           REPO_ANALYZER_PROVIDER, REPO_ANALYZER_CACHE
│   │                           Providers get injected by default (gemini/openrouter)
│   ├── errors.py               Exception hierarchy (AnalyzerError base)
│   ├── utils.py                hashing, tree walking, json helpers
│   ├── github/
│   │   ├── urls.py             parse_target() -> RepoSlug; file_url() line anchors
│   │   └── client.py           GitHubClient: metadata/tree/languages/tarball;
│   │                           403/429 rate-limit waits, 5xx backoff, mocked-http testable
│   ├── ingest/
│   │   ├── archives.py         tarball/zip extraction; traversal-safe (top-level strip,
│   │   │                       symlink skip, absolute-path reject) — VERIFIED by tests
│   │   └── filters.py          IgnoreContext, SOURCE_SUFFIXES, is_config_file,
│   │                           is_ignored_dirs, language_for_path
│   ├── index/
│   │   └── store.py            CacheStore (sqlite): blobs by sha256, parses keyed
│   │                           blob_hash+path+version, analyses (key->json payloads)
│   ├── parsing/
│   │   ├── symbols.py          Symbol/ImportRef/ParsedFile dataclasses (json roundtrip)
│   │   ├── python_parser.py    ast-based; symbols/imports/calls/entry flags
│   │   ├── js_ts_parser.py     regex; imports/require, functions, classes
│   │   ├── c_family.py         parse_go, parse_java (regex)
│   │   └── registry.py         parse_file(path, language, source) dispatch
│   ├── analysis/
│   │   ├── graph.py            ModuleGraph: cycles(SCC), fan-in/out, top_central,
│   │   │                       upstream/downstream BFS, resolve_import()
│   │   ├── graph_builder.py    build_graph(): resolves `from X import y` to real files
│   │   ├── entry_points.py     detect_entry_points(), trace_flow()
│   │   ├── dependencies.py     collect_dependencies() reads package.json, requirements,
│   │   │                       pyproject (tomllib), go.mod, cargo; frameworks_from_deps()
│   │   ├── architecture.py     derive_components(), mermaid diagrams; Component/Diagram
│   │   └── result.py           RepoIndex/AnalysisResult pydantic models (partially used)
│   ├── llm/
│   │   ├── base.py             BaseProvider (retry/backoff), extract_json_object()
│   │   ├── gemini.py           GeminiProvider.generateContent, json MIME mode
│   │   ├── openrouter.py       OpenRouterProvider chat-completions, json_object mode
│   │   ├── service.py          LLMService.generate(): per-provider validation retry,
│   │   │                       transport-error provider fallback; agenerate()
│   │   └── schemas.py          OverviewOut, ArchitectureOut, DocsOut, QAOut,
│   │                           BugInvestigationOut, Citation (extra=forbid)
│   ├── features/
│   │   ├── context.py          relevance scoring, ranked_paths, build_pack (char budget),
│   │   │                       sanitize_for_prompt (secret redaction)
│   │   ├── overview.py         deterministic_overview(), llm_overview()
│   │   ├── architecture_feature.py  architecture_payload(), llm_architecture()
│   │   ├── documentation.py    documentation_payload(), llm_documentation()
│   │   ├── qa.py               retrieve_chunks (BM25-lite over symbol-aligned chunks),
│   │   │                       ask() -> Answer (LLM-grounded or deterministic fallback)
│   │   └── contributor.py      investigate() suspects/trace/tests, trace_impact(),
│   │                           find_test_files()
│   └── report/
│       ├── render.py           markdown helpers (tables, mermaid blocks)
│       ├── html.py             self-contained HTML report w/ mermaid CDN
│       └── html_report.py      rich single-artifact analyze report builder
│                               (11 sections: snapshot/overview/deps/architecture/
│                                flows/relationships/symbols/docs/decisions/entries/
│                                inventory; GitHub blob links via result.link_for)
├── tests/
│   ├── conftest.py             sample_repo + temp_config + clean_env fixtures
│   ├── fixtures/sample_repo/   deterministic fixture (FastAPI app + JS web + CLI + tests,
│   │                           plus node_modules/build/vendor to assert exclusion)
│   ├── test_ingest.py, test_github.py, test_parsers.py, test_graph.py
│   ├── test_index_store.py, test_llm.py, test_analyzer.py, test_features.py
│   ├── test_cli.py, test_end_to_end.py, test_utils_config.py
├── pyproject.toml              package metadata; `repo-analyzer` console script
├── config.example.toml, .env.example, README.md, ARCHITECTURE.md
└── .github/workflows/tests.yml
```

★ = entry points a new session cares about most: `analyzer.py:analyze()` and
`cli.py`.

## 3. Data flow (how a run works)

```
URL ──parse_target()──▶ RepoSlug
     │
     ├─ local dir ──────────────► _index_workspace()
     └─ github: GitHubClient
          metadata ─ branch_head ─ tarball ─ extract_* ─ cache/workspaces/
                                     │
                                     └──▶ _index_workspace():
            rglob(*) → is_ignored_path → classify (config|docs|source)
            source&supported-lang → _parse_cached (CacheStore by sha256)
            manifests bytes → collect_dependencies
            parsed dict → build_graph → detect_entry_points → trace_flow
            ───▶ AnalyzedRepository (dataclass, in-memory)

Feature commands call deterministic payloads (+ optional llm.generate
with validated schemas) → report/html write.
```

## 4. Verified so far (manual smoke tests, all offline-friendly)

| Area | Status |
| --- | --- |
| tar/zip extraction + traversal/symlink safety | ✅ verified in script |
| GitHub url parser (urls, owner/repo, .git, non-github reject) | ✅ tests written |
| GitHubClient mocked (metadata/tree/langs/tarball; 403 rate-limit retry) | ✅ tests written |
| Python/JS/Go/Java parsers produce symbols/imports/entries | ✅ verified + tests |
| Graph edges incl. `from X import y` member resolution | ✅ verified + tests |
| Entry points + flows (sample: src/app/main.py 0.98) | ✅ verified + tests |
| collect_dependencies + frameworks (FastAPI/SQLAlchemy/Uvicorn/pytest) | ✅ verified + tests |
| SQLite cache (blob-hash reuse, analysis cache) | ✅ tests |
| LLM service: same-provider payload retry, transport fallback, schema feedback retry | ✅ verified + tests |
| Features (overview/arch/docs/qa/contributor) deterministic paths | ✅ verified + tests |
| CLI all 5 commands on sample fixture | ✅ verified + tests |
| GitHub e2e through full pipeline (mocked transport) | ✅ tests |
| **Live GitHub e2e**: octocat/Hello-World + pallets/flask (225 files, 19k LOC), reports written | ✅ ran manually |
| **Live QA/bug at flask scale** | ✅ ran manually (correct file hits) |

## 5. Testing instructions (run in a fresh session)

```bash
cd Python_Capstone_Project
python -m pip install -e ".[dev]"          # deps available env-wide already
python -m pytest -q                        # full suite: 121 tests, all offline
python -m pytest tests/test_analyzer.py -q # just orchestrator
```

121 tests collected cleanly across 11 suites (verified via
`pytest --collect-only`: analyzer 11, cli 8, e2e 2, features 16, github 12,
graph 17, index 4, ingest 12, llm 15, parsers 9, utils/config 15).
Collection excludes `tests/fixtures/**` (conftest `collect_ignore`); the
fixture repo's own `test_services.py` is intentionally not a pytest target.
Suites use tmp-path-scoped cache roots; nothing writes outside pytest tmpdirs.
CLI tests set `REPO_ANALYZER_CACHE` env (see `config.py` hook).

## 6. Known gaps / next steps (pick up here)

1. **Run the test suite** — all suites are written but not yet executed
   (`pytest -q`). Expect green on most; fix any assertion drift (e.g.
   `test_graph.test_graph_algorithm.test_edges_and_directions` had a loose
   orphans assertion, `test_analyzer.test_gitflow_incremental_change` contains
   leftover shutil lines that should be simplified).
2. **`analysis/result.py` (RepoIndex/AnalysisResult pydantic models) are
   partially wired** — `analyzer.py` returns the `AnalyzedRepository`
   dataclass instead of `RepoIndex`. Either finish the migration or delete
   result.py to avoid dead code.
3. **`LLMService.agenerate` is async wrapper** but nothing concurrency-heavy
   uses it; optional to leverage for parallel chunked summaries.
4. **Pause/resume for large repos**: currently parse cache is incremental but
   LLM batch summarization (config.analysis.summarize_symbols etc.) is not
   implemented as a separate pass — citation-grounded feature prompts do the
   compression instead. If token cost becomes a concern, add symbol-summary
   cache content to CacheStore and use it in context packs.
5. **GitHubClient.iter_paginated / api_default_branch** are implemented but
   unused (kept for future features like PR/commit history in contributor).
6. Optional polish: `poetry.lock`-free reproducibility is via pyproject only;
   consider `--provider`/`--model` overrides already on config only.

## 7. Design decisions worth remembering

- **No repository code is ever executed.** Source is only read/extracted.
- **Content-hash (sha256) parse cache** — granularity is file bytes, so
  incremental updates reparse only changed files regardless of commit path.
- **Citation validation is enforced in code** (`qa.py` contributor/LLM path):
  line numbers clipped/validated vs `loc_for(path)` (min 1). This is why
  Answer.citations never exceed file length.
- **Provider fallback order**: `config.fallback_chain(default)`; configured
  provider may be pinned per call (`provider=` kwarg). Built-in auto-injected
  providers: `gemini`, `openrouter`, `groq` (kind "openrouter" =
  OpenAI-compatible protocol, `base_url=https://api.groq.com/openai/v1`,
  model via `GROQ_MODEL` env, default `qwen/qwen3.8-27b`). Missing
  API keys raise LLMError mid-chain, which falls through to the next provider.
- **JSON extraction is lenient** (`extract_json_object` handles fences/prose);
  payload failures retry same provider; transport (auth/HTTP) failures fall
  to next provider. `LLMError` = transport; `ValueError` = payload.
- **Fixtures build real AI flow only when keys exist** — features accept
  `llm=None` and degrade deterministically; CLI warns once.

## 8. Conventions

- Type hints on all public functions; dataclasses for parse products,
  pydantic models for LLM outputs, stdlib sqlite for cache.
- Errors: raise typed subclasses of `AnalyzerError` (see errors.py).
- No comments explaining obvious code; docstrings on public API.
- Exclusions in `ingest/filters.py` are the single source of truth for what
  is scanned.
