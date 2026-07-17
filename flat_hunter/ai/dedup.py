"""
Relist / duplicate detection via embeddings.

Aggregators repost the same flat with reworded text and a new price. Rules alone miss
that; embeddings of the free text catch it. We gate on structure first (same rooms, same
district, similar area) — cheap and precise — then confirm with cosine similarity of the
listing texts. If a new listing is a relist of one we've seen, we can say "same flat,
price dropped $40 since <date>".

Embeddings come through the jarvis adapter (``adapter/embeddings.py``); ``find_relist``
takes vectors so it's testable without a model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import Listing

SIMILARITY_THRESHOLD = 0.90
_AREA_BAND = 0.15


@dataclass
class RelistInfo:
    code: int                 # the earlier listing this duplicates
    similarity: float
    price_change: float | None = None   # new price − old price (same currency)

    def line(self) -> str:
        if self.price_change is None or self.price_change == 0:
            return f"relist of #{self.code} (same price)"
        arrow = "↓" if self.price_change < 0 else "↑"
        return f"relist of #{self.code} ({arrow}{abs(self.price_change):.0f} vs before)"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _structurally_similar(a: Listing, b: Listing) -> bool:
    if a.rooms != b.rooms:
        return False
    if a.district and b.district and a.district != b.district:
        return False
    if a.area_total and b.area_total:
        lo, hi = a.area_total * (1 - _AREA_BAND), a.area_total * (1 + _AREA_BAND)
        if not (lo <= b.area_total <= hi):
            return False
    return True


def find_relist(
    candidate: Listing,
    candidate_vec: list[float],
    corpus: list[tuple[Listing, list[float]]],
    threshold: float = SIMILARITY_THRESHOLD,
) -> RelistInfo | None:
    """Return the best structural+semantic duplicate of ``candidate`` in ``corpus``, or None."""
    best: RelistInfo | None = None
    for other, vec in corpus:
        if other.code == candidate.code or not _structurally_similar(candidate, other):
            continue
        sim = cosine(candidate_vec, vec)
        if sim >= threshold and (best is None or sim > best.similarity):
            change = None
            if candidate.price and other.price and candidate.currency == other.currency:
                change = candidate.price - other.price
            best = RelistInfo(code=other.code, similarity=round(sim, 3), price_change=change)
    return best
