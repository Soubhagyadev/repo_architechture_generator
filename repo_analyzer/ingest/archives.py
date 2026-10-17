"""Archive download and safe extraction with path-traversal protection."""

from __future__ import annotations

import io
import os
import re
import tarfile
import zipfile
from pathlib import Path

from repo_analyzer.errors import IngestError
from repo_analyzer.ingest import filters

_TRAVERSAL = re.compile(r"(^|/)\.\.(/|$)")
_ABSOLUTE = re.compile(r"^(/|[A-Za-z]:[\\/])")


def safe_join(base: Path, relative: str) -> Path | None:
    """Join ``relative`` under ``base`` without allowing traversal.

    Returns ``None`` when the resulting path would escape ``base`` or is
    absolute; also rejects Windows drive letters and NUL bytes.
    """
    if not relative or "\x00" in relative or _TRAVERSAL.search(relative) or _ABSOLUTE.match(relative):
        return None
    candidate = (base / relative).resolve()
    base_resolved = base.resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        return None
    return candidate


def extract_tarball(tar_bytes: bytes, destination: Path) -> Path:
    """Extract a GitHub tarball into ``destination`` and return the repo root.

    GitHub archives wrap the repository in a single top-level folder such as
    ``owner-repo-<sha>/``; that layer is stripped so ``destination`` maps
    directly to the repository root. All member paths are validated before
    extraction; symlinks and other non-regular members are skipped, and any
    traversal attempt aborts the whole extraction.
    """
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as archive:
            root_names = _top_level_names(archive)
            strip = 1 if len(root_names) == 1 else 0
            for member in archive.getmembers():
                if not member.isfile():
                    continue  # ignore symlink/hardlink/dir members entirely
                target = _member_destination(destination, member.name, strip)
                if target is None:
                    raise IngestError(f"unsafe archive member path: {member.name!r}")
                _extract_member(archive, member, target)
    except (tarfile.TarError, OSError) as exc:
        raise IngestError(f"tarball extraction failed: {exc}") from exc
    return destination


def extract_zip(zip_bytes: bytes, destination: Path) -> Path:
    """Extract a zip archive into ``destination`` and return the repo root.

    Applies the same traversal checks as the tarball path; entries that end
    with ``/`` are treated as directories and created (without failing) but
    only regular files are written.
    """
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            roots = {name.split("/", 1)[0] for name in names if "/" in name}
            strip = 1 if len(roots) == 1 and not any(name.startswith("../") or name.startswith("/") for name in names) else 0
            for info in archive.infolist():
                if dir_name := _zip_dir_name(info):
                    _safe_mkdir(destination, dir_name, strip)
                    continue
                if info.is_dir():
                    continue
                target = _member_destination(destination, info.filename, strip)
                if target is None:
                    raise IngestError(f"unsafe zip member path: {info.filename!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
    except (zipfile.BadZipFile, OSError) as exc:
        raise IngestError(f"zip extraction failed: {exc}") from exc
    return destination


def _zip_dir_name(info: zipfile.ZipInfo) -> str | None:
    name = info.filename
    if name.endswith("/"):
        return name.rstrip("/")
    # Zip files written on Windows sometimes omit the trailing slash.
    if info.is_dir():
        return name.rstrip("/")
    return None


def _safe_mkdir(destination: Path, dir_name: str, strip: int) -> None:
    parts = _split_parts(dir_name, strip)
    if parts is None:
        return
    target = safe_join(destination, "/".join(parts))
    if target is not None:
        target.mkdir(parents=True, exist_ok=True)


def _top_level_names(archive: tarfile.TarFile) -> set[str]:
    """Collect distinct first path components of the archive's files."""
    names: set[str] = set()
    for member in archive.getmembers():
        if member.isfile():
            names.add(member.name.split("/", 1)[0])
    return names


def _split_parts(member_name: str, strip: int) -> list[str] | None:
    """Normalize an archive member path into safe relative components."""
    normalized = member_name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if strip:
        parts = parts[strip:]
        if not parts:
            return None
    relative = "/".join(parts)
    if not relative or _TRAVERSAL.search(relative) or _ABSOLUTE.match(relative):
        return None
    return parts


def _member_destination(base: Path, member_name: str, strip: int) -> Path | None:
    """Resolve a member path under ``base`` after validation and stripping."""
    parts = _split_parts(member_name, strip)
    if parts is None:
        return None
    candidate = base.joinpath(*parts)
    resolved = candidate.resolve()
    base_resolved = base.resolve()
    if resolved != base_resolved and base_resolved not in resolved.parents:
        return None
    return candidate


def _extract_member(archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    """Write one regular-file member to disk with symlink resistance."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():  # pragma: no cover - race guard
        raise IngestError(f"refusing to overwrite existing path: {target}")
    source = archive.extractfile(member)
    if source is None:  # pragma: no cover - isfile member always yields data
        raise IngestError(f"no data for archive member: {member.name!r}")
    with source, open(target, "wb") as handle:
        handle.write(source.read())


def read_source_file(path: Path, max_bytes: int) -> tuple[str, bool]:
    """Read a text file, decoding defensively and flagging truncation.

    Returns ``(text, truncated)``; undecodable bytes are replaced so parsers
    never crash on malformed encodings.
    """
    with open(path, "rb") as handle:
        raw = handle.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    data = raw[:max_bytes]
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding), truncated
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), truncated


def file_head(path: Path, limit: int = 8192) -> bytes:
    """Return the first ``limit`` bytes of a file for binary sniffing."""
    with open(path, "rb") as handle:
        return handle.read(limit)


def prune_empty_dirs(root: Path) -> int:
    """Remove directories that contain no files (recursively, bottom-up).

    Returns the number of directories removed. Used to tidy archives that
    contain many empty folders.
    """
    removed = 0
    for dirpath, dirnames, _filenames in os.walk(root, topdown=False):
        current = Path(dirpath)
        if current == root:
            continue
        if not dirnames and not any(current.iterdir()):
            current.rmdir()
            removed += 1
    return removed


__all__ = [
    "extract_tarball",
    "extract_zip",
    "file_head",
    "prune_empty_dirs",
    "read_source_file",
    "safe_join",
]
