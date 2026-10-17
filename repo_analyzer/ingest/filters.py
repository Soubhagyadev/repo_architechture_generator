"""Filtering rules that decide which repository files are analyzable."""

from __future__ import annotations

import fnmatch
import os
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass, field

DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".github",
        ".hg",
        ".svn",
        ".jj",
        ".turbo",
        ".vercel",
        ".next",
        ".nuxt",
        ".output",
        ".svelte-kit",
        ".parcel-cache",
        ".docusaurus",
        ".cypress-cache",
        ".terraform",
        ".vagrant",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "env",
        ".eggs",
        "node_modules",
        "bower_components",
        "jspm_packages",
        "vendor",
        "packages",
        "third_party",
        "target",
        "build",
        "dist",
        "out",
        "dist-newstyle",
        "cmake-build-debug",
        "cmake-build-release",
        "deps",
        "_build",
        "coverage",
        ".nyc_output",
        "htmlcov",
        ".coverage",
        "site-packages",
        "shared",
        "Pods",
        "DerivedData",
        "gradle",
        ".idea",
        ".vscode",
        ".vs",
        "bin",
        "obj",
        "generated",
        "generated-src",
        "_generated",
        "protobuf",
        "proto-gen",
        ".cache",
        "cache",
        "tmp",
        "temp",
        ".terraform.lock.hdf",
        "bindgen",
    }
)

DEFAULT_IGNORED_SUFFIXES: tuple[str, ...] = (
    # compiled / binary artifacts
    ".pyc",
    ".pyo",
    ".pyd",
    ".class",
    ".jar",
    ".war",
    ".so",
    ".o",
    ".obj",
    ".a",
    ".lib",
    ".dll",
    ".exe",
    ".bin",
    ".elf",
    ".ko",
    ".wasm",
    # archives
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".zst",
    # executables & disk images
    ".apk",
    ".aab",
    ".ipa",
    ".deb",
    ".rpm",
    ".msi",
    ".dmg",
    ".iso",
    ".img",
    # media
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".ico",
    ".tiff",
    ".psd",
    ".ai",
    ".sketch",
    ".svg",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".mp3",
    ".wav",
    ".flac",
    ".ogg",
    ".m4a",
    ".aac",
    # documents & design
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".epub",
    # data dumps & caches
    ".sqlite",
    ".sqlite3",
    ".db",
    ".mdb",
    ".parquet",
    ".arrow",
    ".feather",
    ".h5",
    ".hdf5",
    ".pkl",
    ".pickle",
    ".npy",
    ".npz",
    ".pt",
    ".pth",
    ".onnx",
    ".tflite",
    ".ckpt",
    ".safetensors",
    ".tfrecord",
    # lock designs and misc binaries
    ".ds_store",
    ".sublime-workspace",
    ".keystore",
    ".jks",
    ".p12",
    ".pem",
    ".crt",
    ".key",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".snap",
    ".o.tmp",
)

DEFAULT_IGNORED_FILES: frozenset[str] = frozenset(
    {
        ".ds_store",
        "thumbs.db",
        "desktop.ini",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "npm-shrinkwrap.json",
        "poetry.lock",
        "pipfile.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "packages.lock.json",
        "shrinkwrap.yaml",
        "go.sum",
        "pubspec.lock",
        "codecov.yml",
        ".travis.yml",
        "makefile",  # handled specially; see note below
        "dockerfile.dev",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".prettierrc",
        ".eslintrc",
    }
)

# Language extensions we will actually parse for symbols.
SOURCE_SUFFIXES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".rb": "ruby",
    ".cs": "csharp",
    ".php": "php",
    ".swift": "swift",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".scala": "scala",
    ".dart": "dart",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".json": "json",
    ".md": "docs-markdown",
    ".txt": "docs-text",
    ".html": "markup-html",
    ".htm": "markup-html",
    ".css": "style-css",
    ".scss": "style-scss",
    ".vue": "framework-vue",
    ".svelte": "framework-svelte",
}

# Config / manifest files worth reading even though they are not "code".
CONFIG_FILE_NAMES: frozenset[str] = frozenset(
    {
        "package.json",
        "tsconfig.json",
        "jsconfig.json",
        "vite.config.ts",
        "vite.config.js",
        "vue.config.js",
        "nuxt.config.ts",
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
        "astro.config.mjs",
        "svelte.config.js",
        "webpack.config.js",
        "rollup.config.js",
        "babel.config.js",
        ".babelrc",
        "angular.json",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-prod.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "environment.yml",
        "go.mod",
        "composer.json",
        "gemfile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "makefile",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".env.example",
        ".dockerignore",
        ".gitignore",
        "contributing.md",
        "architecture.md",
        "code_of_conduct.md",
        "developers.md",
        "codeql.yml",
    }
)

# Exact filenames that should always be skipped (case-insensitive).
ALWAYS_IGNORED_FILES: frozenset[str] = frozenset(
    {
        ".ds_store",
        "thumbs.db",
    }
)


@dataclass
class IgnoreContext:
    """Context for filtering and truncating repository files."""

    root_names: set[str] = field(default_factory=set)

    def is_ignored_dir(self, dirname: str) -> bool:
        """True when a directory should be skipped entirely."""
        lowered = dirname.lower()
        if dirname.startswith("__") and dirname.endswith("__"):
            return False  # allow dunder packages like __init__ content dirs
        if lowered in DEFAULT_IGNORED_DIRS:
            return True
        if lowered in {"docs", "doc"}:
            return False
        return False

    def is_ignored_file(self, path: str, suffix: str) -> bool:
        """True when a file should be skipped."""
        lowered = path.lower()
        base = posixpath.basename(lowered)
        if base in ALWAYS_IGNORED_FILES:
            return True
        if base in DEFAULT_IGNORED_FILES:
            return True
        for pattern in _glob_ignores():
            if fnmatch.fnmatch(lowered, pattern):
                return True
        for ignored in DEFAULT_IGNORED_SUFFIXES:
            if lowered.endswith(ignored):
                return True
        return self._is_binary_like_suffix(suffix)

    @staticmethod
    def _is_binary_like_suffix(suffix: str) -> bool:
        return suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip"}


_GLOB_PATTERNS: tuple[str, ...] = (
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
    "*.bundle.css",
    "*.chunk.js",
    "*.map",
    "*.d.ts",
    "*.snap",
    "*.pyc",
    "*.class",
    "*.o",
    ".env*",
    "Cargo.lock",
    "*.lock",
    "*.sum",
    "*.docpickle",
)

_EXTRA_IGNORED_DIR_PATTERNS: tuple[str, ...] = (
    "*-egg-info",
    "*.egg-info",
    "__pycache__",
    "node_modules",
)


def _glob_ignores() -> tuple[str, ...]:
    """Glob patterns checked against the full (lowercased) repo path."""
    return _GLOB_PATTERNS


def is_ignored_dir(name: str) -> bool:
    """Standalone directory-name check used by the GitHub tree walker."""
    if name in {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox"}:
        return True
    context = IgnoreContext()
    return context.is_ignored_dir(name)


def is_ignored_path(path: str) -> bool:
    """Full-path check against globs and directory segments."""
    lowered = path.lower()
    for segment in lowered.split("/"):
        if segment in DEFAULT_IGNORED_DIRS and segment not in {"docs", "doc"}:
            return True
    for pattern in _EXTRA_IGNORED_DIR_PATTERNS:
        for segment in lowered.split("/"):
            if fnmatch.fnmatch(segment, pattern):
                return True
    base = posixpath.basename(lowered)
    if base in ALWAYS_IGNORED_FILES:
        return True
    for pattern in _GLOB_PATTERNS:
        if fnmatch.fnmatch(lowered, pattern) or fnmatch.fnmatch(base, pattern):
            return True
    return False


def is_source_file(path: str) -> bool:
    """True when ``path`` has a known source extension."""
    _base, ext = posixpath.splitext(path.lower())
    return ext in SOURCE_SUFFIXES


def is_config_file(path: str) -> bool:
    """True when basename or extension marks the file as config/docs."""
    base = posixpath.basename(path.lower())
    if base in CONFIG_FILE_NAMES:
        return True
    _base, ext = posixpath.splitext(base)
    return ext in {".toml", ".cfg", ".xml"}


def language_for_path(path: str) -> str:
    """Best-effort language label for ``path``."""
    _base, ext = posixpath.splitext(path.lower())
    return SOURCE_SUFFIXES.get(ext, "other")


def is_binary_bytes(head: bytes) -> bool:
    """Heuristic binary detection from ``head`` bytes."""
    if not head:
        return False
    if b"\x00" in head:
        return True
    printable = sum(byte in (9, 10, 13) or 32 <= byte < 127 or byte >= 128 for byte in head)
    return printable / len(head) < 0.30


def iter_files(root: str | os.PathLike[str]) -> Iterable[str]:
    """Yield POSIX-style repo-relative paths for files under ``root``."""
    base = str(root)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in {".git", "__pycache__", "node_modules"} and not d.endswith(".egg-info")
        )
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            if os.path.isfile(full):
                yield full


__all__ = [
    "ALWAYS_IGNORED_FILES",
    "CONFIG_FILE_NAMES",
    "DEFAULT_IGNORED_DIRS",
    "DEFAULT_IGNORED_FILES",
    "DEFAULT_IGNORED_SUFFIXES",
    "IgnoreContext",
    "SOURCE_SUFFIXES",
    "is_binary_bytes",
    "is_config_file",
    "is_ignored_dir",
    "is_ignored_path",
    "is_source_file",
    "iter_files",
    "language_for_path",
]
