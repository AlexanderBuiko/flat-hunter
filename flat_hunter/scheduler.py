"""
Per-user hunt runner + the interval loop.

``run_for_user`` fetches current listings, runs the hunt against a user's requirement,
and returns the *new* matches (those not already notified), marking them notified. It's
I/O-injectable (``fetch_fn`` / ``extract_fn``) so it's testable without network or a model.
``run_loop`` just calls it for every registered user on a fixed interval.
"""

from __future__ import annotations

import time
from typing import Callable

from .models import Listing, Requirement
from .pipeline import Match, hunt
from .store import Store


def run_for_user(
    store: Store,
    user_id: str,
    req_dict: dict,
    *,
    fetch_fn: Callable[[], list[Listing]],
    extract_fn: Callable[[Listing], dict] | None,
) -> list[Match]:
    """Return new (not-yet-notified) ranked matches for one user; record them as notified."""
    listings = fetch_fn()
    for lst in listings:
        store.upsert(lst)
    req = Requirement.from_dict(req_dict)
    matches = hunt(listings, req, extract_fn=extract_fn)
    fresh: list[Match] = []
    for m in matches:
        if not store.already_notified(user_id, m.listing.code):
            fresh.append(m)
            store.mark_notified(user_id, m.listing.code)
    return fresh


def run_once(
    store: Store,
    *,
    fetch_fn: Callable[[], list[Listing]],
    extract_fn: Callable[[Listing], dict] | None,
    send_fn: Callable[[str, Match], None],
) -> int:
    """One sweep across all registered users; send each their new matches. Returns count."""
    sent = 0
    for user_id in store.users_with_requirements():
        req = store.get_requirement(user_id)
        if not req:
            continue
        for m in run_for_user(store, user_id, req, fetch_fn=fetch_fn, extract_fn=extract_fn):
            send_fn(user_id, m)
            sent += 1
    return sent


def run_loop(store: Store, interval_s: int, *, fetch_fn, extract_fn, send_fn,
             stop: Callable[[], bool] | None = None) -> None:  # pragma: no cover - loop
    """Run ``run_once`` forever on an interval (until ``stop()`` returns True, if given)."""
    while not (stop and stop()):
        try:
            run_once(store, fetch_fn=fetch_fn, extract_fn=extract_fn, send_fn=send_fn)
        except Exception as exc:  # noqa: BLE001 — never let one sweep kill the loop
            print(f"[scheduler] sweep failed: {exc}")
        time.sleep(interval_s)
