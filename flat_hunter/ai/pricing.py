"""
Price-vs-comparables — retrieve similar flats from the collected corpus and judge whether
a listing is cheap or overpriced. This is the RAG-lite step: "retrieve" comparable
listings (same rooms, same town/district, similar area, same currency), then aggregate to
a median and a verdict. It only gets useful as the DB fills up over runs.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from ..models import Listing

MIN_COMPARABLES = 3
_AREA_BAND = 0.25   # ±25% of area counts as comparable
_CHEAP, _PRICEY = -10.0, 10.0   # % vs median thresholds for the verdict


@dataclass
class PriceInfo:
    price: float
    currency: str
    n_comparables: int
    median: float | None = None
    delta_pct: float | None = None    # (price - median) / median * 100
    verdict: str = "n/a"              # below market | about right | above market | n/a

    def line(self) -> str:
        if self.verdict == "n/a" or self.median is None:
            return f"{self.price:.0f} {self.currency} (not enough comparables yet)"
        sign = "+" if self.delta_pct >= 0 else ""
        return (f"{self.price:.0f} {self.currency} — {self.verdict} "
                f"({sign}{self.delta_pct:.0f}% vs median {self.median:.0f} of "
                f"{self.n_comparables} similar)")


def comparables(listing: Listing, corpus: list[Listing]) -> list[Listing]:
    """Flats in the corpus that are a fair price comparison to ``listing``."""
    out = []
    for other in corpus:
        if other.code == listing.code or not other.price:
            continue
        if other.rooms != listing.rooms:
            continue
        if listing.currency and other.currency and other.currency != listing.currency:
            continue
        # Same town, and same district when both known.
        if listing.town and other.town and other.town != listing.town:
            continue
        if listing.district and other.district and other.district != listing.district:
            continue
        # Area band when both areas are known.
        if listing.area_total and other.area_total:
            lo, hi = listing.area_total * (1 - _AREA_BAND), listing.area_total * (1 + _AREA_BAND)
            if not (lo <= other.area_total <= hi):
                continue
        out.append(other)
    return out


def price_verdict(listing: Listing, corpus: list[Listing]) -> PriceInfo:
    """Compare a listing's price to comparable flats in the corpus."""
    info = PriceInfo(price=listing.price or 0.0, currency=listing.currency or "?", n_comparables=0)
    if not listing.price:
        return info
    comps = comparables(listing, corpus)
    info.n_comparables = len(comps)
    if len(comps) < MIN_COMPARABLES:
        return info
    med = statistics.median(c.price for c in comps)
    info.median = med
    info.delta_pct = (listing.price - med) / med * 100 if med else 0.0
    info.verdict = ("below market" if info.delta_pct <= _CHEAP
                    else "above market" if info.delta_pct >= _PRICEY else "about right")
    return info
