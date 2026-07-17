"""Scheduler: per-user sweep sends new matches once (dedup across runs)."""

from flat_hunter.models import Listing
from flat_hunter.pipeline import Match
from flat_hunter.scheduler import run_for_user, run_once
from flat_hunter.store import Store


def _fetch():
    return [Listing(code=1, rooms=2, price=500, currency="USD"),
            Listing(code=2, rooms=2, price=600, currency="USD")]


def _extract(listing):
    return {"dishwasher": True, "scam_risk": "low", "red_flags": []}


def test_run_for_user_new_then_none(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    req = {"hard": {"rooms": [2], "price_max": 800, "currency": "USD"},
           "soft": {"dishwasher": {"want": True, "weight": 2}}, "notes": ""}
    first = run_for_user(store, "u1", req, fetch_fn=_fetch, extract_fn=_extract)
    assert {m.listing.code for m in first} == {1, 2}
    second = run_for_user(store, "u1", req, fetch_fn=_fetch, extract_fn=_extract)
    assert second == []                       # already notified


def test_run_once_sends_per_user(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    store.save_requirement("u1", {"hard": {"rooms": [2], "price_max": 800, "currency": "USD"},
                                  "soft": {}, "notes": ""})
    sent: list[tuple[str, int]] = []
    n = run_once(store, fetch_fn=_fetch, extract_fn=_extract,
                 send_fn=lambda uid, m: sent.append((uid, m.listing.code)))
    assert n == 2
    assert {c for _, c in sent} == {1, 2}
