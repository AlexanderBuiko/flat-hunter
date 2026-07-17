"""
Hard filtering (pure code) — the automation half.

``passes_hard`` decides whether a listing clears a requirement's hard gates: price,
rooms, floor, district, area. Soft preferences are judged separately by the AI layer
(``ai/rank.py``); a soft pref flagged ``critical`` is applied here too once its feature
value is known (post-extraction), but the base gate is deterministic.
"""

from __future__ import annotations

from .models import Listing, Requirement


def passes_hard(listing: Listing, req: Requirement) -> bool:
    """True if the listing satisfies every hard constraint that is set on ``req``."""
    if req.price_max is not None:
        if listing.price is None or listing.price > req.price_max:
            return False
        # Only compare when currencies match; unknown currency is not auto-rejected.
        if listing.currency and req.currency and listing.currency != req.currency:
            return False
    if req.rooms and (listing.rooms not in req.rooms):
        return False
    if req.floor_min is not None and (listing.storey is None or listing.storey < req.floor_min):
        return False
    if req.floor_not_top and listing.is_top_floor():
        return False
    if req.area_min is not None and (listing.area_total is None or listing.area_total < req.area_min):
        return False
    if req.districts:
        hay = " ".join(filter(None, [listing.district, listing.address, listing.town])).lower()
        if not any(d.lower() in hay for d in req.districts):
            return False
    return True


def filter_listings(listings: list[Listing], req: Requirement) -> list[Listing]:
    return [l for l in listings if passes_hard(l, req)]
