"""
SQLite store — listings we've seen, and per-user notification bookkeeping.

Deliberately tiny and file-based so the whole bot runs on a laptop or a small VPS with
no external services. Two responsibilities: (1) remember every listing (so "new since
last run" is a set difference) and (2) remember what each user was already notified about.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from .models import Listing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    code        INTEGER PRIMARY KEY,
    price       REAL,
    currency    TEXT,
    rooms       INTEGER,
    first_seen  TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT DEFAULT CURRENT_TIMESTAMP,
    features    TEXT,           -- JSON: LLM-extracted features (filled in P2), else NULL
    data        TEXT NOT NULL   -- JSON snapshot of the Listing
);
CREATE TABLE IF NOT EXISTS notified (
    user_id  TEXT NOT NULL,
    code     INTEGER NOT NULL,
    sent_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, code)
);
"""


class Store:
    def __init__(self, path: str = "flat_hunter.db") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert(self, listing: Listing) -> bool:
        """Insert or refresh a listing. Returns True if it's the first time we've seen it."""
        payload = json.dumps(asdict(listing), ensure_ascii=False, default=str)
        cur = self.conn.execute("SELECT code FROM listings WHERE code = ?", (listing.code,))
        is_new = cur.fetchone() is None
        if is_new:
            self.conn.execute(
                "INSERT INTO listings (code, price, currency, rooms, data) VALUES (?,?,?,?,?)",
                (listing.code, listing.price, listing.currency, listing.rooms, payload),
            )
        else:
            self.conn.execute(
                "UPDATE listings SET price=?, currency=?, rooms=?, data=?, "
                "last_seen=CURRENT_TIMESTAMP WHERE code=?",
                (listing.price, listing.currency, listing.rooms, payload, listing.code),
            )
        self.conn.commit()
        return is_new

    def set_features(self, code: int, features: dict) -> None:
        self.conn.execute("UPDATE listings SET features=? WHERE code=?",
                          (json.dumps(features, ensure_ascii=False), code))
        self.conn.commit()

    def already_notified(self, user_id: str, code: int) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM notified WHERE user_id=? AND code=?", (user_id, code))
        return cur.fetchone() is not None

    def mark_notified(self, user_id: str, code: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO notified (user_id, code) VALUES (?, ?)", (user_id, code))
        self.conn.commit()
