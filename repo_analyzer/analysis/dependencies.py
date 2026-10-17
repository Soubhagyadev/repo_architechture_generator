"""Read dependency manifests and classify languages for a repository."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - tomllib is stdlib on 3.11+
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class DependencyInfo:
    """Aggregated dependency information for one repository."""

    ecosystems: dict[str, list[str]]  # ecosystem -> package names
    manifests: list[str]  # manifest file paths
    raw_manifests: dict[str, dict[str, Any]]  # path -> parsed JSON manifests
    config_files: list[str]  # detected config/infra files
    ci_files: list[str]  # detected CI workflow files

    def to_json(self) -> dict[str, Any]:
        """Serialize for caching."""
        return {
            "ecosystems": self.ecosystems,
            "manifests": self.manifests,
            "raw_manifests": self.raw_manifests,
            "config_files": self.config_files,
            "ci_files": self.ci_files,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "DependencyInfo":
        """Deserialize from cached JSON."""
        return cls(
            ecosystems=dict(payload.get("ecosystems") or {}),
            manifests=list(payload.get("manifests") or []),
            raw_manifests=dict(payload.get("raw_manifests") or {}),
            config_files=list(payload.get("config_files") or []),
            ci_files=list(payload.get("ci_files") or []),
        )


_RE_FRAMEWORKS = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "pydantic": "Pydantic",
    "sqlalchemy": "SQLAlchemy",
    "celery": "Celery",
    "uvicorn": "Uvicorn",
    "pytest": "pytest",
    "express": "Express",
    "react": "React",
    "vue": "Vue.js",
    "svelte": "Svelte",
    "next": "Next.js",
    "nestjs": "NestJS",
    "spring": "Spring",
    "gin-gonic": "Gin",
    "actix": "Actix",
}

_CONFIG_PATTERNS = (
    "dockerfile",
    "docker-compose",
    "makefile",
    ".env.example",
    "tsconfig.json",
    "vite.config",
    "webpack.config",
    "babel.config",
    "pyproject.toml",
    "setup.cfg",
    "go.mod",
    "cargo.toml",
    ".nvmrc",
    "sonar-project.properties",
    "ansible.cfg",
)

_CI_PATTERNS = (
    ".github/workflows/",
    ".gitlab-ci.yml",
    ".circleci/",
    "jenkinsfile",
    ".travis.yml",
    ".drone.yml",
    "cloudbuild.yaml",
)


def _package_names(raw_names: list[Any]) -> list[str]:
    """Flatten dependency dict keys into sorted names."""
    names: list[str] = []
    for raw in raw_names:
        if isinstance(raw, dict):
            names.extend(str(key) for key in raw)
        elif isinstance(raw, str):
            names.append(raw)
    return sorted(set(names))


def collect_dependencies(files: dict[str, bytes], max_manifest_bytes: int = 200_000) -> DependencyInfo:
    """Build dependency info from manifest bytes keyed by repo-relative path."""
    ecosystems: dict[str, list[str]] = {}
    manifests: list[str] = []
    raw_manifests: dict[str, dict[str, Any]] = {}
    config_files: list[str] = []
    ci_files: list[str] = []

    for path in sorted(files):
        lowered = path.lower()
        base = posixpath.basename(lowered)
        if any(pattern in lowered for pattern in _CI_PATTERNS):
            ci_files.append(path)
            continue
        if any(pattern in lowered or pattern == base for pattern in _CONFIG_PATTERNS):
            if base not in {".env", ".env.local"}:
                config_files.append(path)

        data = files[path][:max_manifest_bytes]
        if base == "package.json" or lowered.endswith("package.json"):
            manifests.append(path)
            try:
                payload = json.loads(data.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(payload, dict):
                raw_manifests[path] = payload
                deps: list[str] = []
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    section = payload.get(key)
                    if isinstance(section, dict):
                        deps.extend(str(name) for name in section)
                if deps:
                    ecosystems.setdefault("npm", sorted(set(deps)))
        elif base in {"requirements.txt", "requirements-dev.txt", "requirements-prod.txt"} or (
            base.startswith("requirements") and base.endswith(".txt")
        ):
            manifests.append(path)
            names = []
            for line in data.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                names.append(re.split(r"[<>=~!\[; ]", line, maxsplit=1)[0].strip())
            names = [name for name in names if name]
            if names:
                existing = set(ecosystems.get("pip", []))
                ecosystems["pip"] = sorted(existing | set(names))
        elif base == "pyproject.toml":
            manifests.append(path)
            if tomllib is not None:
                try:
                    payload = tomllib.loads(data.decode("utf-8", "replace"))
                except (tomllib.TOMLDecodeError, UnicodeDecodeError):
                    payload = {}
                project = payload.get("project", {})
                if isinstance(project, dict):
                    deps = _package_names(project.get("dependencies") or [])
                    for optional in (project.get("optional-dependencies") or {}).values():
                        deps.extend(_package_names(optional if isinstance(optional, list) else []))
                    if deps:
                        existing = set(ecosystems.get("pip", []))
                        ecosystems["pip"] = sorted(existing | set(deps))
        elif base == "go.mod":
            manifests.append(path)
            text = data.decode("utf-8", "replace")
            names = re.findall(r"^\t([\w./-]+) v", text, re.M)
            if names:
                ecosystems.setdefault("go modules", sorted(set(names)))
        elif base == "cargo.toml":
            manifests.append(path)
            if tomllib is not None:
                try:
                    payload = tomllib.loads(data.decode("utf-8", "replace"))
                except (tomllib.TOMLDecodeError, UnicodeDecodeError):
                    payload = {}
                section = payload.get("dependencies", {})
                if isinstance(section, dict):
                    ecosystems.setdefault("cargo", sorted(section))

    return DependencyInfo(
        ecosystems=ecosystems,
        manifests=manifests,
        raw_manifests=raw_manifests,
        config_files=config_files,
        ci_files=ci_files,
    )


def frameworks_from_deps(dep_info: DependencyInfo) -> list[str]:
    """Map dependency names onto well-known framework labels."""
    found: list[str] = []
    all_names: set[str] = set()
    for names in dep_info.ecosystems.values():
        for name in names:
            all_names.add(name.lower().replace("_", "-"))
            all_names.add(name.lower())
    for key, label in _RE_FRAMEWORKS.items():
        if any(key in name or name == key for name in all_names):
            if label not in found:
                found.append(label)
    return found


__all__ = ["DependencyInfo", "collect_dependencies", "frameworks_from_deps"]
