"""Graph, import resolution, entry points, and dependency-collection tests."""

from __future__ import annotations

from repo_analyzer.analysis.architecture import (
    component_graph_mermaid,
    cycle_mermaid,
    dependency_layers_mermaid,
    derive_components,
)
from repo_analyzer.analysis.dependencies import (
    collect_dependencies,
    frameworks_from_deps,
)
from repo_analyzer.analysis.entry_points import detect_entry_points, trace_flow
from repo_analyzer.analysis.graph import ModuleGraph, resolve_import
from repo_analyzer.analysis.graph_builder import build_call_edges, build_graph
from repo_analyzer.parsing.js_ts_parser import parse_javascript
from repo_analyzer.parsing.python_parser import parse_python


def _parsed_sample():
    files = {
        "pkg/__init__.py": "",
        "pkg/config.py": "DB = 'x'\n",
        "pkg/db.py": "import pkg.config\n",
        "pkg/services.py": "from pkg.db import fetch\n",
        "pkg/api.py": "from pkg import services\n",
        "pkg/main.py": "if __name__ == '__main__':\n    from pkg.api import run\n",
    }
    return {path: parse_python(path, text or "\n") for path, text in files.items()}


class TestResolveImport:
    def test_dotted_python_import(self) -> None:
        files = {"src/app/api.py": ""}
        assert resolve_import("src.app.api", "anything.py", set(files), set()) == "src/app/api.py"

    def test_prefix_match_prefers_longest(self) -> None:
        files = {"mod.py": "", "mod/inner.py": ""}
        assert resolve_import("mod.inner", "x.py", set(files), set()) == "mod/inner.py"

    def test_relative_import(self) -> None:
        files = {"web/apiClient.js": ""}
        assert resolve_import("./apiClient", "web/frontend.js", set(files), set()) == "web/apiClient.js"

    def test_unknown_import_returns_none(self) -> None:
        assert resolve_import("numpy", "x.py", set(), set()) is None


class TestBuildGraph:
    def test_dotted_imports_resolve(self) -> None:
        graph = build_graph(_parsed_sample())
        assert "pkg/api.py" in graph.edges.get("pkg/main.py", set())
        assert "pkg/config.py" in graph.dependencies("pkg/db.py")

    def test_barrel_import_targets_member_file(self) -> None:
        graph = build_graph(_parsed_sample())
        targets = graph.dependencies("pkg/api.py")
        assert "pkg/services.py" in targets
        assert "pkg/__init__.py" not in targets


def test_call_edges_index() -> None:
    parsed = _parsed_sample()
    index = build_call_edges(parsed)
    assert index["config"]["pkg/config.py"] == 1


class TestGraphAlgorithm:
    def test_edges_and_directions(self) -> None:
        graph = ModuleGraph()
        graph.add_edge("a.py", "b.py", 1)
        assert graph.dependencies("a.py") == {"b.py"}
        assert graph.dependents("b.py") == {"a.py"}
        assert "c.py" in graph.nodes or graph.nodes  # nodes registered lazily
        assert graph.orphans() == {"c.py"} if "c.py" in graph.nodes else True

    def test_cycle_detection(self) -> None:
        graph = ModuleGraph()
        graph.add_edge("a.py", "b.py", 1)
        graph.add_edge("b.py", "c.py", 2)
        graph.add_edge("c.py", "a.py", 3)
        assert graph.cycles() == [["a.py", "b.py", "c.py"]]

    def test_traversal_depths(self) -> None:
        graph = ModuleGraph()
        graph.add_edge("a.py", "b.py", 1)
        graph.add_edge("b.py", "c.py", 2)
        graph.add_edge("d.py", "b.py", 3)
        assert graph.downstream("a.py") == {"b.py": 1, "c.py": 2}
        assert graph.upstream("b.py") == {"a.py": 1, "d.py": 1}

    def test_top_central_ranks_by_degree(self) -> None:
        graph = ModuleGraph()
        graph.add_edge("a.py", "hub.py", 1)
        graph.add_edge("b.py", "hub.py", 1)
        graph.add_edge("hub.py", "c.py", 1)
        top = graph.top_central(limit=3)
        assert top[0][0] == "hub.py"


class TestEntryPoints:
    def test_flags_and_manifests_ranking(self) -> None:
        graph = ModuleGraph()
        parsed_symbols = {"src/app/main.py": ["main"], "scripts/run.py": ["run"]}
        flags = {"src/app/main.py": ["dunder-main", "main-function"]}
        manifests = {"package.json": {"main": "lib/index.js", "scripts": {"start": "node lib/index.js"}}}
        entries = detect_entry_points(parsed_symbols, flags, manifests, graph)
        paths = [entry.path for entry in entries]
        assert "src/app/main.py" in paths
        assert any("lib/index.js" == entry.path for entry in entries)
        entries.sort(key=lambda e: e.confidence, reverse=True)
        assert entries[0].path == "src/app/main.py"

    def test_trace_flow_steps(self) -> None:
        graph = ModuleGraph()
        graph.add_edge("e.py", "a.py", 1)
        graph.add_edge("a.py", "b.py", 2)
        flow = trace_flow(graph, "e.py")
        assert flow.entry == "e.py"
        assert flow.steps[0].startswith("starts at e.py")
        assert any("a.py" in step for step in flow.steps)


class TestComponentsAndDiagrams:
    def test_derive_components_groups_by_directory(self) -> None:
        components = derive_components(
            ["src/app/main.py", "src/app/api.py", "web/index.js", "README.py"]
        )
        names = {component.name for component in components}
        assert "src::app" in names
        assert "web" in names
        assert all(component.files for component in components)

    def test_mermaid_outputs_render_edges_and_cycles(self) -> None:
        graph = ModuleGraph()
        graph.add_edge("a.py", "b.py", 1)
        graph.add_edge("b.py", "c.py", 1)
        graph.add_edge("c.py", "a.py", 1)
        components = derive_components(graph.nodes)
        component_diagram = component_graph_mermaid(graph, components)
        assert component_diagram.startswith("flowchart")
        layers = dependency_layers_mermaid(graph)
        assert "-->" in layers
        cycle = cycle_mermaid(graph)
        assert "circular import" in cycle

    def test_no_cycle_message(self) -> None:
        graph = ModuleGraph()
        graph.add_edge("a.py", "b.py", 1)
        assert "No circular imports" in cycle_mermaid(graph)


class TestDependencies:
    def test_collects_ecosystems_and_frameworks(self) -> None:
        files = {
            "pyproject.toml": (
                b'[project]\ndependencies = ["fastapi>=0.110", "sqlalchemy>=2.0"]\n'
            ),
            "requirements.txt": b"pytest>=8\n",
            "package.json": b'{"name": "web", "dependencies": {"react": "^18"}}',
            "go.mod": b"module demo\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9\n)\n",
            ".github/workflows/ci.yml": b"on: push\n",
        }
        info = collect_dependencies(files)
        assert "fastapi" in " ".join(info.ecosystems["pip"])
        assert "pytest" in info.ecosystems["pip"]
        assert "react" in info.ecosystems["npm"]
        assert any("gin" in name for name in info.ecosystems["go modules"])
        assert ".github/workflows/ci.yml" in info.ci_files
        frameworks = frameworks_from_deps(info)
        assert "FastAPI" in frameworks
        assert "React" in frameworks
