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
    embedding   TEXT,           -- JSON: free-text embedding (P5 dedup), else NULL
    data        TEXT NOT NULL   -- JSON snapshot of the Listing
);
CREATE TABLE IF NOT EXISTS notified (
    user_id  TEXT NOT NULL,
    code     INTEGER NOT NULL,
    sent_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, code)
);
CREATE TABLE IF NOT EXISTS requirements (
    user_id     TEXT PRIMARY KEY,
    data        TEXT NOT NULL,   -- JSON: the requirement dict (hard/soft/notes)
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
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

    def get_features(self, code: int) -> dict | None:
        """Cached LLM features for a listing, or None if not extracted yet."""
        cur = self.conn.execute("SELECT features FROM listings WHERE code=?", (code,))
        row = cur.fetchone()
        return json.loads(row["features"]) if row and row["features"] else None

    def set_embedding(self, code: int, vec: list[float]) -> None:
        self.conn.execute("UPDATE listings SET embedding=? WHERE code=?",
                          (json.dumps(vec), code))
        self.conn.commit()

    def get_embedding(self, code: int) -> list[float] | None:
        cur = self.conn.execute("SELECT embedding FROM listings WHERE code=?", (code,))
        row = cur.fetchone()
        return json.loads(row["embedding"]) if row and row["embedding"] else None

    def all_listings(self) -> list["Listing"]:
        """Every stored listing, reconstructed (for price comparables / dedup corpus)."""
        from dataclasses import fields as _fields
        keep = {f.name for f in _fields(Listing)}
        cur = self.conn.execute("SELECT data FROM listings")
        out = []
        for row in cur.fetchall():
            d = {k: v for k, v in json.loads(row["data"]).items() if k in keep}
            out.append(Listing(**d))
        return out

    def corpus_with_embeddings(self) -> list[tuple["Listing", list[float]]]:
        """Stored listings that already have an embedding (dedup candidates)."""
        from dataclasses import fields as _fields
        keep = {f.name for f in _fields(Listing)}
        cur = self.conn.execute("SELECT data, embedding FROM listings WHERE embedding IS NOT NULL")
        out = []
        for row in cur.fetchall():
            d = {k: v for k, v in json.loads(row["data"]).items() if k in keep}
            out.append((Listing(**d), json.loads(row["embedding"])))
        return out

    def already_notified(self, user_id: str, code: int) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM notified WHERE user_id=? AND code=?", (user_id, code))
        return cur.fetchone() is not None

    def mark_notified(self, user_id: str, code: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO notified (user_id, code) VALUES (?, ?)", (user_id, code))
        self.conn.commit()

    # ── per-user requirements ────────────────────────────────────────────────

    def save_requirement(self, user_id: str, req: dict) -> None:
        self.conn.execute(
            "INSERT INTO requirements (user_id, data, updated_at) VALUES (?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP",
            (user_id, json.dumps(req, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_requirement(self, user_id: str) -> dict | None:
        cur = self.conn.execute("SELECT data FROM requirements WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return json.loads(row["data"]) if row else None

    def delete_requirement(self, user_id: str) -> None:
        self.conn.execute("DELETE FROM requirements WHERE user_id=?", (user_id,))
        self.conn.commit()

    def users_with_requirements(self) -> list[str]:
        cur = self.conn.execute("SELECT user_id FROM requirements")
        return [r["user_id"] for r in cur.fetchall()]
