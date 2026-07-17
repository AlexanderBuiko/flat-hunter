"""
Hard filtering (pure code) — the automation half.

``passes_hard`` decides whether a listing clears a requirement's hard gates: price,
rooms, floor, district, area. Soft preferences are judged separately by the AI layer
(``ai/rank.py``); a soft pref flagged ``critical`` is applied here too once its feature
value is known (post-extraction), but the base gate is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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


@dataclass
class SoftResult:
    """How well a listing's LLM-extracted features match the soft preferences."""

    score: int                              # 0..100, weighted satisfaction
    fits: list[str] = field(default_factory=list)         # prefs clearly satisfied
    concerns: list[str] = field(default_factory=list)     # prefs clearly contradicted
    unconfirmed: list[str] = field(default_factory=list)  # critical prefs not mentioned
    disqualified: str | None = None         # reason a *critical* pref was contradicted
    scam_risk: str = "low"
    red_flags: list[str] = field(default_factory=list)


def soft_evaluate(features: dict, req: Requirement) -> SoftResult:
    """Score extracted features against the requirement's soft prefs.

    A feature value is ``True``/``False``/``None`` (None = the ad didn't mention it —
    never penalised, but a *critical* unmentioned pref is flagged 'unconfirmed'). A
    critical pref that is clearly *contradicted* disqualifies the listing.
    """
    fits: list[str] = []
    concerns: list[str] = []
    unconfirmed: list[str] = []
    disq: str | None = None
    total = got = 0
    for name, pref in req.soft.items():
        total += pref.weight
        val = features.get(name)
        if val is None:                     # not mentioned
            if pref.critical:
                unconfirmed.append(name)
            continue
        if bool(val) == pref.want:
            got += pref.weight
            fits.append(name)
        else:
            concerns.append(name)
            if pref.critical and disq is None:
                need = "present" if pref.want else "absent"
                is_ = "present" if val else "absent"
                disq = f"{name} is {is_} (you require it {need})"
    score = round(got / total * 100) if total else 100
    return SoftResult(
        score=score, fits=fits, concerns=concerns, unconfirmed=unconfirmed,
        disqualified=disq,
        scam_risk=features.get("scam_risk", "low"),
        red_flags=list(features.get("red_flags", [])),
    )
