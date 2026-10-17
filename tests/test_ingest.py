"""Tests for safe extraction and file filtering rules."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from repo_analyzer.errors import IngestError
from repo_analyzer.ingest import filters
from repo_analyzer.ingest.archives import extract_tarball, extract_zip, safe_join
from repo_analyzer.ingest.filters import (
    is_ignored_path,
    is_source_file,
    language_for_path,
)


def _make_tar(members: dict[str, bytes], symlinks: list[str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for name in symlinks or []:
            info = tarfile.TarInfo(name=name)
            info.type = tarfile.SYMTYPE
            archive.addfile(info)
    return buffer.getvalue()


def _make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class TestTarballExtraction:
    def test_strips_single_top_level_dir(self, tmp_path: Path) -> None:
        tar = _make_tar({"repo-abc/src/main.py": b"print('hi')"})
        root = extract_tarball(tar, tmp_path / "out")
        assert (root / "src" / "main.py").is_file()
        assert not (root / "repo-abc").exists()

    def test_symlinks_are_skipped(self, tmp_path: Path) -> None:
        tar = _make_tar({"repo/src/main.py": b"x = 1"}, symlinks=["repo/link"])
        root = extract_tarball(tar, tmp_path / "out")
        assert not (root / "link").exists()

    def test_traversal_member_aborts(self, tmp_path: Path) -> None:
        tar = _make_tar({"repo/../../evil.py": b"bad"})
        with pytest.raises(IngestError):
            extract_tarball(tar, tmp_path / "out")

    def test_absolute_member_aborts(self, tmp_path: Path) -> None:
        tar = _make_tar({"repo" + "/".join([""]) + "": b""})  # degenerate ok
        evil = _make_tar({"/etc/evil": b"bad"})
        with pytest.raises(IngestError):
            extract_tarball(evil, tmp_path / "out")
        _ = tar  # keep helper exercised

    def test_writes_content_correctly(self, tmp_path: Path) -> None:
        payload = b"def main():\n    return 42\n"
        tar = _make_tar({"repo/app.py": payload})
        root = extract_tarball(tar, tmp_path / "out")
        assert (root / "app.py").read_bytes() == payload


class TestZipExtraction:
    def test_extracts_and_strips_root(self, tmp_path: Path) -> None:
        zip_data = _make_zip({"repo/src/app.py": b"x = 1"})
        root = extract_zip(zip_data, tmp_path / "z")
        assert (root / "src" / "app.py").is_file()

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        zip_data = _make_zip({"../evil.py": b"bad"})
        with pytest.raises(IngestError):
            extract_zip(zip_data, tmp_path / "z")


def test_safe_join_rejects_traversal() -> None:
    base = Path("/tmp/whatever-base")
    assert safe_join(base, "../escape") is None
    assert safe_join(base, "a/../../escape") is None
    assert safe_join(base, "/etc/passwd") is None
    assert safe_join(base, "normal/file.txt") is not None


class TestFilters:
    def test_binary_and_vendored_paths_ignored(self) -> None:
        assert is_ignored_path("node_modules/leftpad/index.js")
        assert is_ignored_path("build/output.bin")
        assert is_ignored_path("src/bundle.min.js")
        assert is_ignored_path("assets/logo.png")
        assert is_ignored_path("Cargo.lock")
        assert not is_ignored_path("src/app/main.py")

    def test_language_detection(self) -> None:
        assert language_for_path("a/b/main.py") == "python"
        assert language_for_path("web/index.js") == "javascript"
        assert language_for_path("x.tsx") == "typescript"
        assert language_for_path("go/main.go") == "go"
        assert language_for_path("README.md") == "docs-markdown"

    def test_source_check(self) -> None:
        assert is_source_file("main.py")
        assert is_source_file("main.go")
        assert not is_source_file("main.png")

    def test_sample_repo_prepares_expected_files(self, sample_repo: Path) -> None:
        languages = {
            filters.language_for_path(str(path.relative_to(sample_repo)))
            for path in sample_repo.rglob("*")
            if path.is_file()
        }
        assert "python" in languages
        assert "javascript" in languages
