"""Append-only JSONL store and SQLite key-value store for clearing state."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, List, Optional

log = logging.getLogger("store")


class JSONLStore:
    """Append-only JSONL log file."""

    def __init__(self, path: str = "data/clearing_log.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def read_all(self) -> List[dict]:
        if not self.path.exists():
            return []
        records = []
        with open(self.path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("Corrupt JSONL line %d in %s, skipping", line_num, self.path)
        return records

    def last(self) -> Optional[dict]:
        records = self.read_all()
        return records[-1] if records else None


class StateDB:
    """SQLite key-value store for mutable state.

    Faster than scanning JSONL for recovery — O(1) reads instead of O(n).
    Stores JSON-serialised values by string key.
    """

    def __init__(self, path: str = "data/state.db"):
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            "  key TEXT PRIMARY KEY,"
            "  value TEXT NOT NULL,"
            "  updated_at REAL NOT NULL"
            ")"
        )
        self._conn.commit()

    def get(self, key: str) -> Optional[Any]:
        """Read a JSON value by key. Returns None if not found or corrupt."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM kv WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            log.warning("Corrupt JSON for key '%s' in StateDB, returning None", key)
            return None

    def put(self, key: str, value: Any) -> None:
        """Write a JSON value by key (upsert)."""
        import time
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, default=str), time.time()),
            )
            self._conn.commit()

    def delete(self, key: str) -> None:
        """Delete a key."""
        with self._lock:
            self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            self._conn.commit()

    def keys(self) -> List[str]:
        """Return all keys."""
        with self._lock:
            rows = self._conn.execute("SELECT key FROM kv ORDER BY key").fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
