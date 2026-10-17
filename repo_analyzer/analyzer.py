"""Orchestrates scanning, parsing, and caching for one repository.

Given a GitHub URL or local directory this produces an
:class:`AnalyzedRepository`: the file inventory, language stats, parsed
symbols, module graph, dependencies, entry points, execution flows, and
deterministic diagrams. Parse results are cached by content hash so repeated
or incremental analyses only pay for changed files.
"""

from __future__ import annotations

import logging
import posixpath
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from repo_analyzer.analysis.architecture import (
    Component,
    Diagram,
    component_graph_mermaid,
    cycle_mermaid,
    dependency_layers_mermaid,
    derive_components,
)
from repo_analyzer.analysis.dependencies import (
    DependencyInfo,
    collect_dependencies,
    frameworks_from_deps,
)
from repo_analyzer.analysis.entry_points import (
    ExecutionFlow,
    EntryPoint,
    detect_entry_points,
    trace_flow,
)
from repo_analyzer.analysis.graph import ModuleGraph
from repo_analyzer.analysis.graph_builder import build_call_edges, build_graph
from repo_analyzer.config import AppConfig
from repo_analyzer.errors import AnalyzerError, GitHubError, IngestError
from repo_analyzer.github.client import GitHubClient
from repo_analyzer.github.urls import RepoSlug, file_url, parse_target
from repo_analyzer.index.store import CacheStore
from repo_analyzer.ingest import filters
from repo_analyzer.ingest.archives import extract_tarball
from repo_analyzer.parsing.registry import parse_file
from repo_analyzer.parsing.symbols import ParsedFile, PARSER_VERSION
from repo_analyzer.utils import sha256_hex

logger = logging.getLogger(__name__)


@dataclass
class AnalyzedRepository:
    """Everything analysis holds about one repository snapshot."""

    full_name: str
    ref: str
    commit_sha: str
    description: str
    workspace: Path
    file_count: int
    total_loc: int
    languages: list[tuple[str, int, int]]  # (language, files, loc) sorted
    dependencies: DependencyInfo
    graph: ModuleGraph
    components: list[Component]
    entry_points: list[EntryPoint]
    flows: list[ExecutionFlow]
    parsed: dict[str, ParsedFile] = field(default_factory=dict)
    file_stats: dict[str, int] = field(default_factory=dict)  # path -> loc
    file_sizes: dict[str, int] = field(default_factory=dict)
    all_paths: list[str] = field(default_factory=list)
    config_paths: list[str] = field(default_factory=list)
    docs_paths: list[str] = field(default_factory=list)
    parse_errors: int = 0
    source: str = "local"
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    github_language: str = ""
    pushed_at: str = ""

    @property
    def is_github(self) -> bool:
        """Whether the snapshot came from a GitHub tarball."""
        return self.source == "github"

    def link_for(self, path: str, line: int | None = None) -> str | None:
        """Web link to ``path`` when the snapshot came from GitHub."""
        if not self.is_github:
            return None
        slug = parse_target(self.full_name)
        if slug is None:
            return None
        ref = self.commit_sha or self.ref
        return str(file_url(slug, path, ref, start_line=line))

    def component_of(self, path: str) -> Component | None:
        """Component whose file list contains ``path``."""
        for comp in self.components:
            if path in comp.files:
                return comp
        return None

    @property
    def frameworks(self) -> list[str]:
        """Well-known frameworks inferred from dependencies."""
        return frameworks_from_deps(self.dependencies)

    _NON_CODE_LANGUAGES = frozenset(
        {"json", "yaml", "toml", "ini", "cfg", "xml", "sql", "shell", "docs-markdown", "docs-text"}
    )

    def primary_language(self) -> str:
        """Dominant *code* language label by LOC (config/data files skipped)."""
        for name, _files, _loc in self.languages:
            if name not in self._NON_CODE_LANGUAGES:
                return name
        if self.languages:
            return self.languages[0][0]
        return "unknown"

    def loc_for(self, path: str) -> int:
        """Line count for ``path`` (minimum 1 for citation validation)."""
        return max(self.file_stats.get(path, 0), 1)


class RepoAnalyzer:
    """Entry point that scans and parses a repository."""

    def __init__(
        self,
        config: AppConfig,
        store: CacheStore | None = None,
        client_factory: "Callable[[], GitHubClient] | None" = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or (lambda: GitHubClient(self.config.github))
        self.store = store or CacheStore(self._cache_db())

    def _cache_db(self) -> Path:
        """Location of the SQLite cache database."""
        base = Path(self.config.cache.root)
        base.mkdir(parents=True, exist_ok=True)
        return base / "cache.sqlite3"

    def close(self) -> None:
        """Release cache resources."""
        self.store.close()

    def __enter__(self) -> "RepoAnalyzer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ public

    def analyze(self, target: str, *, is_local: bool = False, refresh: bool = False) -> AnalyzedRepository:
        """Analyze a GitHub URL or local directory path.

        Raises :class:`AnalyzerError` subclasses on failure; callers should
        handle GitHubError (network/api), IngestError (extraction), and plain
        AnalyzerError (bad target).
        """
        started = time.time()
        if is_local:
            workspace = Path(target).resolve()
            if not workspace.is_dir():
                raise IngestError(f"local path is not a directory: {target}")
            result = self._index_workspace(
                workspace,
                full_name=f"{workspace.name} (local)",
                ref="local",
                commit_sha="",
                description="",
            )
        else:
            slug = parse_target(target)
            if slug is None:
                raise AnalyzerError(
                    "not a GitHub repository target: expected https://github.com/owner/repo"
                )
            result = self._analyze_github(slug, refresh=refresh)
        elapsed = time.time() - started
        logger.info(
            "analyzed %s: %d files, %d LOC in %.1fs",
            result.full_name,
            result.file_count,
            result.total_loc,
            elapsed,
        )
        return result

    # --------------------------------------------------------------- internals

    def _analyze_github(self, slug: RepoSlug, refresh: bool) -> AnalyzedRepository:
        """Fetch metadata + tarball, extract to cache, and index it."""
        cache_key = f"index:{slug.owner}/{slug.repo}"
        if not refresh:
            cached = self.store.get_analysis(cache_key)
            if isinstance(cached, dict):
                restored = self._restore(cached)
                if restored is not None:
                    return restored
        with self._client_factory() as client:
            metadata = client.metadata(slug)
            branch = metadata.default_branch
            head = client.branch_head(slug, branch)
            tarball = client.tarball(slug, branch)
        workspace = self._workspace_dir(slug.owner, slug.repo, head)
        if not workspace.exists():
            logger.info("extracting %s@%s tarball", slug.repo, head[:10])
            extract_tarball(tarball, workspace)
        result = self._index_workspace(
            workspace,
            full_name=f"{slug.owner}/{slug.repo}",
            ref=branch,
            commit_sha=head,
            description=metadata.description,
            source="github",
        )
        result.stars = metadata.stars
        result.forks = metadata.forks
        result.open_issues = metadata.open_issues
        result.github_language = metadata.language
        result.pushed_at = metadata.pushed_at
        self.store.put_analysis(cache_key, self._snapshot(result))
        return result

    def _snapshot(self, result: AnalyzedRepository) -> dict[str, object]:
        """Serialize ``result`` for the analysis cache (excluding parsed blobs)."""
        return {
            "full_name": result.full_name,
            "ref": result.ref,
            "commit_sha": result.commit_sha,
            "description": result.description,
            "source": result.source,
            "stars": result.stars,
            "forks": result.forks,
            "open_issues": result.open_issues,
            "github_language": result.github_language,
            "pushed_at": result.pushed_at,
            "workspace": str(result.workspace),
            "file_stats": result.file_stats,
            "all_paths": result.all_paths,
            "config_paths": result.config_paths,
            "docs_paths": result.docs_paths,
            "graph": result.graph.to_json(),
            "components": [comp.to_json() for comp in result.components],
            "entry_points": [
                {
                    "path": entry.path,
                    "kind": entry.kind,
                    "confidence": entry.confidence,
                    "reason": entry.reason,
                }
                for entry in result.entry_points
            ],
            "flows": [flow.to_json() for flow in result.flows],
        }

    def _restore(self, snapshot: dict[str, object]) -> AnalyzedRepository | None:
        """Rebuild an AnalyzedRepository from a cached snapshot."""
        workspace = Path(str(snapshot.get("workspace", "")))
        if not workspace.is_dir():
            logger.warning("cached workspace vanished: %s", workspace)
            return None
        result = self._index_workspace(
            workspace,
            full_name=str(snapshot.get("full_name") or workspace.name),
            ref=str(snapshot.get("ref") or "HEAD"),
            commit_sha=str(snapshot.get("commit_sha") or ""),
            description=str(snapshot.get("description") or ""),
            source=str(snapshot.get("source") or "github"),
        )
        result.stars = int(snapshot.get("stars") or 0)
        result.forks = int(snapshot.get("forks") or 0)
        result.open_issues = int(snapshot.get("open_issues") or 0)
        result.github_language = str(snapshot.get("github_language") or "")
        result.pushed_at = str(snapshot.get("pushed_at") or "")
        return result

    def _index_workspace(
        self,
        workspace: Path,
        full_name: str,
        ref: str,
        commit_sha: str,
        description: str,
        source: str = "local",
    ) -> AnalyzedRepository:
        """Scan, filter, parse, and graph one extracted tree."""
        all_paths: list[str] = []
        source_paths: list[str] = []
        config_paths: list[str] = []
        docs_paths: list[str] = []
        file_stats: dict[str, int] = {}
        file_sizes: dict[str, int] = {}
        parsed: dict[str, ParsedFile] = {}
        manifest_bytes: dict[str, bytes] = {}
        total_loc = 0

        for abs_path in sorted(workspace.rglob("*")):
            if not abs_path.is_file():
                continue
            rel = abs_path.relative_to(workspace).as_posix()
            if filters.is_ignored_path(rel):
                continue
            all_paths.append(rel)
            try:
                size = abs_path.stat().st_size
            except OSError:
                size = 0
            file_sizes[rel] = size
            language = filters.language_for_path(rel)
            if language in {"docs-markdown", "docs-text"}:
                docs_paths.append(rel)
            elif filters.is_config_file(rel):
                config_paths.append(rel)
                try:
                    manifest_bytes[rel] = abs_path.read_bytes()
                except OSError:
                    pass
            elif filters.is_source_file(rel):
                source_paths.append(rel)
                loc = _count_lines(abs_path)
                file_stats[rel] = loc
                total_loc += loc
                if language in {"python", "javascript", "typescript", "go", "java"}:
                    if not _oversized(size, self.config.limits.max_file_bytes):
                        parsed[rel] = self._parse_cached(abs_path, rel, language)

        dependencies = collect_dependencies(manifest_bytes)
        graph = build_graph(parsed)
        language_counter_files: Counter[str] = Counter(
            filters.language_for_path(path) for path in source_paths
        )
        language_counter_loc: Counter[str] = Counter()
        for path in source_paths:
            language_counter_loc[filters.language_for_path(path)] += file_stats.get(path, 0)
        language_list = [
            (language, language_counter_files[language], language_counter_loc[language])
            for language, _count in language_counter_files.most_common()
        ]
        language_list.sort(key=lambda item: (-item[2], -item[1]))

        flags = {path: pf.entry_points for path, pf in parsed.items()}
        symbols = {path: [symbol.name for symbol in pf.symbols] for path, pf in parsed.items()}
        detected = detect_entry_points(symbols, flags, dependencies.raw_manifests, graph)
        flows = [trace_flow(graph, entry.path) for entry in detected]

        return AnalyzedRepository(
            full_name=full_name,
            ref=ref,
            commit_sha=commit_sha,
            description=description,
            workspace=workspace,
            file_count=len(all_paths),
            total_loc=total_loc,
            languages=language_list,
            dependencies=dependencies,
            graph=graph,
            components=derive_components(source_paths),
            entry_points=detected,
            flows=flows,
            parsed=parsed,
            file_stats=file_stats,
            file_sizes=file_sizes,
            all_paths=all_paths,
            config_paths=config_paths,
            docs_paths=docs_paths,
            parse_errors=sum(1 for pf in parsed.values() if pf.parse_error),
            source=source,
        )

    def _parse_cached(self, abs_path: Path, rel_path: str, language: str) -> ParsedFile:
        """Parse a file, reusing cached output when the content hash matches."""
        try:
            content = abs_path.read_bytes()
        except OSError as exc:
            logger.warning("unreadable file %s: %s", rel_path, exc)
            return ParsedFile(path=rel_path, language=language, loc=0, parse_error=str(exc))
        blob_hash = sha256_hex(content)
        self.store.remember_blob(blob_hash, len(content))
        cached = self.store.parse_for_blob(blob_hash)
        if cached is not None:
            data = cached.data
            if data.get("path") == rel_path or data.get("language") == language:
                restored = ParsedFile.from_json(data)
                # Path may differ if the same content moved; trust the stored parse.
                restored.path = rel_path
                for symbol in restored.symbols:
                    symbol.path = rel_path
                return restored
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError:
            source = content.decode("latin-1")
        result = parse_file(rel_path, language, source)
        self.store.put_parse(
            path=rel_path,
            version=PARSER_VERSION,
            language=language,
            blob_hash=blob_hash,
            data=result.to_json(),
        )
        return result

    def _workspace_dir(self, owner: str, repo: str, sha: str) -> Path:
        """Workspace path for one extracted commit."""
        base = Path(self.config.cache.root) / "workspaces"
        safe_sha = sha.replace("/", "_")[:12] or "head"
        return base / f"{owner}__{repo}__{safe_sha}"


def _oversized(size: int, max_bytes: int) -> bool:
    """Skip files beyond the configured per-file cap."""
    return size > max_bytes


def _count_lines(abs_path: Path) -> int:
    """Count lines, tolerating decode problems."""
    try:
        with open(abs_path, "rb") as handle:
            data = handle.read()
            return data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
    except OSError:
        return 0


def diagrams_for(result: AnalyzedRepository) -> list[Diagram]:
    """Deterministic Mermaid diagrams derived from a parsed repository."""
    return [
        Diagram(
            title="Component relationships",
            kind="flowchart",
            mermaid=component_graph_mermaid(result.graph, result.components),
        ),
        Diagram(
            title="Core module dependencies",
            kind="flowchart",
            mermaid=dependency_layers_mermaid(result.graph),
        ),
        Diagram(
            title="Circular import check",
            kind="flowchart",
            mermaid=cycle_mermaid(result.graph),
        ),
    ]


def call_index_for(result: AnalyzedRepository) -> dict[str, dict[str, int]]:
    """symbol-name -> {file: occurrences} index across all parsed files."""
    index: dict[str, dict[str, int]] = {}
    for path, source in result.parsed.items():
        for symbol in source.symbols:
            bucket = index.setdefault(symbol.name, {})
            bucket[path] = bucket.get(path, 0) + 1
    return index


__all__ = ["AnalyzedRepository", "RepoAnalyzer", "diagrams_for", "call_index_for"]
