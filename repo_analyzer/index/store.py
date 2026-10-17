"""SQLite-backed cache for file snapshots, parses, and LLM analyses.

Content-addressed by blob SHA-256 so re-analyzing a repository that changed
only a handful of files reuses every unchanged parse result.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS parses (
    blob_hash TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    version TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (path, version)
);
CREATE INDEX IF NOT EXISTS idx_parses_blob ON parses(blob_hash);
CREATE TABLE IF NOT EXISTS analyses (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


@dataclass(frozen=True)
class StoredParse:
    """A cached parse result for one file."""

    path: str
    language: str
    data: dict[str, Any]


class CacheStore:
    """Thread-safe SQLite cache rooted at ``db_path``."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "CacheStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- blobs ---------------------------------------------------------------

    def remember_blob(self, blob_hash: str, size: int) -> None:
        """Record that a blob with this hash exists (idempotent)."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO blobs(hash, size, created_at) VALUES (?,?,?)",
                (blob_hash, size, time.time()),
            )
            self._conn.commit()

    def has_blob(self, blob_hash: str) -> bool:
        """True when this content hash was seen before."""
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM blobs WHERE hash=?", (blob_hash,)).fetchone()
        return row is not None

    # -- parses ----------------------------------------------------------------

    def get_parse(self, path: str, version: str) -> StoredParse | None:
        """Fetch the cached parse for ``path`` at parser ``version``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT path, language, data FROM parses WHERE path=? AND version=?",
                (path, version),
            ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[2])
        except ValueError:
            return None
        return StoredParse(path=row[0], language=row[1], data=data)

    def put_parse(
        self,
        path: str,
        version: str,
        language: str,
        blob_hash: str,
        data: dict[str, Any],
    ) -> None:
        """Cache a parse result keyed by path+version, indexed by blob hash."""
        payload = json.dumps(data, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO blobs(hash, size, created_at) VALUES (?,?,?)",
                (blob_hash, 0, time.time()),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO parses(blob_hash, path, language, version, data, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (blob_hash, path, language, version, payload, time.time()),
            )
            self._conn.commit()

    def parse_for_blob(self, blob_hash: str) -> StoredParse | None:
        """Fetch any cached parse for content hash ``blob_hash``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT path, language, data FROM parses WHERE blob_hash=? ORDER BY created_at DESC LIMIT 1",
                (blob_hash,),
            ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[2])
        except ValueError:
            return None
        return StoredParse(path=row[0], language=row[1], data=data)

    # -- analyses ----------------------------------------------------------------

    def get_analysis(self, cache_key: str) -> Any | None:
        """Fetch a cached feature payload (JSON) or ``None``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM analyses WHERE cache_key=?", (cache_key,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            return None

    def put_analysis(self, cache_key: str, payload: Any) -> None:
        """Cache a feature payload under ``cache_key``."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO analyses(cache_key, payload, created_at) VALUES (?,?,?)",
                (cache_key, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            self._conn.commit()

    def drop_repo(self, prefix: str) -> None:
        """Delete all cached analyses whose key starts with ``prefix``."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM analyses WHERE cache_key LIKE ?",
                (prefix + "%",),
            )
            self._conn.commit()


__all__ = ["CacheStore", "StoredParse"]
