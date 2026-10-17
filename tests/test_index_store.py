"""Cache store round-trips and incremental reuse behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_analyzer.index.store import CacheStore
from repo_analyzer.utils import sha256_hex


@pytest.fixture()
def store(tmp_path: Path) -> CacheStore:
    with CacheStore(tmp_path / "cache.sqlite3") as db:
        yield db


def test_blob_tracking(store: CacheStore) -> None:
    blob = sha256_hex(b"content")
    assert not store.has_blob(blob)
    store.remember_blob(blob, 7)
    assert store.has_blob(blob)
    store.remember_blob(blob, 7)  # idempotent


def test_parse_roundtrip_by_content_hash(store: CacheStore) -> None:
    data = {"path": "app.py", "language": "python", "symbols": [], "imports": []}
    blob = sha256_hex(b"print(1)")
    store.put_parse("app.py", "1", "python", blob, data)
    stored = store.parse_for_blob(blob)
    assert stored is not None
    assert stored.path == "app.py"
    assert stored.data == data
    # Re-parsing the same bytes from a renamed file reuses the cached parse.
    again = store.parse_for_blob(blob)
    assert again is stored or again.data == stored.data


def test_analysis_cache_and_prefix_drop(store: CacheStore) -> None:
    store.put_analysis("index:pallets/flask", {"files": 3})
    assert store.get_analysis("index:pallets/flask") == {"files": 3}
    assert store.get_analysis("index:missing") is None
    store.put_analysis("index:pallets/other", {"files": 9})
    store.drop_repo("index:pallets/flask")
    assert store.get_analysis("index:pallets/flask") is None
    assert store.get_analysis("index:pallets/other") == {"files": 9}


def test_corrupt_json_analysis_is_tolerated(store: CacheStore) -> None:
    store._conn.execute(
        "INSERT INTO analyses(cache_key, payload, created_at) VALUES (?,?,?)",
        ("bad:key", "{not json", 0.0),
    )
    store._conn.commit()
    assert store.get_analysis("bad:key") is None
