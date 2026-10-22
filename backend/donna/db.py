"""Friday's database: data/Friday.db (SQLite). One connection per call, one lock for writes. Owner-only files."""
from __future__ import annotations

import importlib
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL);
-- Message-IDs of every mail Friday sent herself (DJ07 fills it). The learner never reads these (DJ02).
CREATE TABLE IF NOT EXISTS sent_ids (message_id TEXT PRIMARY KEY, ts REAL NOT NULL);
-- The Sent-folder learner (DJ02): one meta row, the profile as items (each with Keep or Remove), and the example mails.
-- No raw mail text is ever stored: items and examples are scrubbed first.
CREATE TABLE IF NOT EXISTS learner_meta (id INTEGER PRIMARY KEY CHECK (id = 1), mails_read INTEGER NOT NULL, headers_seen INTEGER NOT NULL,
    candidates INTEGER NOT NULL, applications INTEGER NOT NULL, built_ts REAL NOT NULL, approved_ts REAL);
CREATE TABLE IF NOT EXISTS learner_items (id TEXT PRIMARY KEY, grp TEXT NOT NULL, pos INTEGER NOT NULL, text TEXT NOT NULL, keep INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS learner_examples (id INTEGER PRIMARY KEY, subject TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL);
"""

# Every part keeps its own tables in its own module. They are all made here, once, when the database opens (DJ11 K2), so a query
# never depends on which part was built first.
PART_MODULES = ("board", "cv", "dataset", "dedupe", "fetch", "intel", "route", "score", "sender", "sheets", "writer")
SCHEMA_VERSION = 2
# version -> SQL that takes a database from the version before to this one. `CREATE TABLE IF NOT EXISTS` never adds a column to a
# table that exists, so the first column added after the owner has real data goes here (DJ11 K3).
# One statement per step: a database that already has a column skips that one step and still runs the steps after it (DJ14KF L6).
MIGRATIONS: dict[int, tuple[str, ...]] = {2: ("ALTER TABLE company_replies ADD COLUMN from_addr TEXT NOT NULL DEFAULT ''",)}

_ID_CLEAN = " \t\r\n<>"


def norm_message_id(mid: str) -> str:
    return (mid or "").strip(_ID_CLEAN).lower()


class DonnaDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._conn() as c:
            if c.execute("PRAGMA user_version").fetchone()[0] == 0:
                c.execute("PRAGMA synchronous = OFF")      # a brand-new file holds nothing yet; the next start repeats any lost step
            c.executescript(SCHEMA + "".join(importlib.import_module(f".{name}", __package__).SCHEMA for name in PART_MODULES))   # one commit
            self._migrate(c)
        try:                       # the owner's private data: this account only
            os.chmod(self.path.parent, 0o700)
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        have = c.execute("PRAGMA user_version").fetchone()[0]
        if have > SCHEMA_VERSION:
            raise RuntimeError("donna.db was made by a newer version of The Office. Update The Office first.")
        for version in range(have + 1, SCHEMA_VERSION + 1):
            if version in MIGRATIONS:
                for statement in MIGRATIONS[version]:
                    try:
                        c.execute(statement)
                    except sqlite3.OperationalError as e:   # a new database already has the column
                        if "duplicate column" not in str(e).lower():
                            raise
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def get(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def put(self, key: str, value: str) -> None:
        with self.lock, self._conn() as c:
            c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def take(self, key: str, cap: int) -> bool:
        """Use one of `cap` a day (or any count) in one step: True when there was one left. Two threads can never take the last one
        twice (DJ11 K4)."""
        with self.lock, self._conn() as c:
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, '0')", (key,))
            return c.execute("UPDATE settings SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key=? AND CAST(value AS INTEGER) < ?",
                             (key, cap)).rowcount == 1

    def give_back(self, key: str) -> None:
        with self.lock, self._conn() as c:
            c.execute("UPDATE settings SET value = CAST(MAX(CAST(value AS INTEGER) - 1, 0) AS TEXT) WHERE key=?", (key,))

    def get_json(self, key: str, default, kind=None):
        """A JSON setting. `default` when it is missing, is not valid JSON, or is not of type `kind` (list, dict ...)."""
        import json
        try:
            v = json.loads(self.get(key, "") or "null")
        except ValueError:
            return default
        return v if v is not None and (kind is None or isinstance(v, kind)) else default

    def add_sent_id(self, message_id: str, clock=time.time) -> None:
        mid = norm_message_id(message_id)
        if mid:
            with self.lock, self._conn() as c:
                c.execute("INSERT OR IGNORE INTO sent_ids(message_id, ts) VALUES(?, ?)", (mid, clock()))

    def sent_ids(self) -> set[str]:
        with self._conn() as c:
            return {r["message_id"] for r in c.execute("SELECT message_id FROM sent_ids")}

    @contextmanager
    def tx(self):
        """One write transaction under the lock: it commits when the block ends, or rolls back on an error."""
        with self.lock:
            c = self._conn()
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise
            finally:
                c.close()

    def query(self, sql: str, params=()) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, params)]

    def event(self, kind: str, text: str, clock=time.time) -> None:
        with self.lock, self._conn() as c:
            c.execute("INSERT INTO events(ts, kind, text) VALUES(?, ?, ?)", (clock(), kind, text[:500]))

    def events(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT ts, kind, text FROM events ORDER BY id DESC LIMIT ?", (limit,))]
