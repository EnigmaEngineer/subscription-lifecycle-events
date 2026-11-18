"""The sqlite side of the handler. Two tables and one decision.

`deliveries` is the idempotency ledger. `events` is the append only log that
state reconstruction will read later. They live in one file because the insert
into `events` and the insert into `deliveries` have to be one transaction. If
they are not, a crash between the two leaves either a logged event nobody can
dedupe or a dedupe entry for an event nobody stored.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from enum import Enum

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    event_id       TEXT PRIMARY KEY,
    body_sha256    TEXT NOT NULL,
    first_seen_at  INTEGER NOT NULL,
    last_seen_at   INTEGER NOT NULL,
    delivery_count INTEGER NOT NULL DEFAULT 1,
    conflict_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL UNIQUE,
    event_type  TEXT NOT NULL,
    created     INTEGER NOT NULL,
    received_at INTEGER NOT NULL,
    body        BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS events_by_created ON events (created, seq);
"""


class Decision(str, Enum):
    NEW = "new"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    event_id: str
    delivery_count: int


def body_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class EventStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def accept(
        self,
        event_id: str,
        event_type: str,
        created: int,
        received_at: int,
        payload: bytes,
    ) -> Outcome:
        """Record one delivery and return what it was.

        NEW means the event was stored. DUPLICATE means the same id arrived
        again carrying the same bytes, which is what a Stripe retry looks like.
        CONFLICT means the same id arrived carrying different bytes. That is not
        a retry and the body is not stored, because the first one was already
        accepted and nothing here can say which of the two is right.
        """
        digest = body_digest(payload)
        cur = self.conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            row = cur.execute(
                "SELECT body_sha256, delivery_count FROM deliveries WHERE event_id = ?",
                (event_id,),
            ).fetchone()

            if row is None:
                cur.execute(
                    "INSERT INTO events (event_id, event_type, created, received_at, body)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (event_id, event_type, created, received_at, payload),
                )
                cur.execute(
                    "INSERT INTO deliveries (event_id, body_sha256, first_seen_at,"
                    " last_seen_at, delivery_count) VALUES (?, ?, ?, ?, 1)",
                    (event_id, digest, received_at, received_at),
                )
                cur.execute("COMMIT")
                return Outcome(Decision.NEW, event_id, 1)

            stored_digest, count = row
            if stored_digest == digest:
                cur.execute(
                    "UPDATE deliveries SET delivery_count = delivery_count + 1,"
                    " last_seen_at = ? WHERE event_id = ?",
                    (received_at, event_id),
                )
                cur.execute("COMMIT")
                return Outcome(Decision.DUPLICATE, event_id, count + 1)

            cur.execute(
                "UPDATE deliveries SET conflict_count = conflict_count + 1,"
                " last_seen_at = ? WHERE event_id = ?",
                (received_at, event_id),
            )
            cur.execute("COMMIT")
            return Outcome(Decision.CONFLICT, event_id, count)
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def delivery_total(self) -> int:
        return self.conn.execute(
            "SELECT COALESCE(SUM(delivery_count), 0) FROM deliveries"
        ).fetchone()[0]

    def conflict_total(self) -> int:
        return self.conn.execute(
            "SELECT COALESCE(SUM(conflict_count), 0) FROM deliveries"
        ).fetchone()[0]

    def event_ids(self) -> list[str]:
        rows = self.conn.execute("SELECT event_id FROM events ORDER BY seq").fetchall()
        return [r[0] for r in rows]
