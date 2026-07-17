"""
Domain types: a parsed rental Listing and a user's Requirement.

A Listing is the subset of realt.by's ``__NEXT_DATA__`` object we care about, plus a
constructed detail URL and a ``free_text`` view (title + headline + description) that the
AI layer reads. A Requirement splits into ``hard`` filters (evaluated in code — price,
rooms, floor, district) and ``soft`` preferences (weighted, judged by the LLM); a soft
preference marked ``critical`` is promoted to a hard gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ISO 4217 numeric → code, as realt.by stores currency (e.g. 840 = USD).
_CURRENCY = {840: "USD", 933: "BYN", 978: "EUR", 643: "RUB"}

# Detail-page URL. The list view only carries the object `code`; the public detail page
# lives under the rent path (verify the exact slug against the live site if it changes).
DETAIL_URL = "https://realt.by/rent/flat-for-long/object/{code}/"


@dataclass
class Listing:
    code: int                      # stable public id (primary key)
    uuid: str | None = None
    title: str | None = None
    headline: str | None = None    # the free-text pitch (rich; AI reads this)
    description: str | None = None
    comments: str | None = None
    rooms: int | None = None
    storey: int | None = None      # this flat's floor
    storeys: int | None = None     # floors in the building
    area_total: float | None = None
    area_kitchen: float | None = None
    price: float | None = None
    currency: str | None = None
    price_change: int | None = None  # 1 up / -1 down / 0 none (realt's priceChangeDirection)
    address: str | None = None
    district: str | None = None
    town: str | None = None
    metro: str | None = None
    building_year: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # the untouched object, for later

    @property
    def url(self) -> str:
        return DETAIL_URL.format(code=self.code)

    def free_text(self) -> str:
        """All human-written text about this flat, for the LLM feature extractor."""
        parts = [self.title, self.headline, self.description, self.comments]
        return "\n".join(p.strip() for p in parts if p and p.strip())

    def is_top_floor(self) -> bool | None:
        if self.storey is None or self.storeys is None:
            return None
        return self.storey >= self.storeys


@dataclass
class SoftPref:
    want: bool = True          # want the feature present (True) or absent (False)
    weight: int = 1            # 1..5, drives ranking
    critical: bool = False     # if True, a miss disqualifies the listing (hard gate)


@dataclass
class Requirement:
    """A user's search. ``hard`` is code-evaluated; ``soft`` is LLM-judged."""

    price_max: float | None = None
    currency: str = "USD"
    rooms: list[int] = field(default_factory=list)      # any of these room counts
    floor_min: int | None = None
    floor_not_top: bool = False
    districts: list[str] = field(default_factory=list)  # substring match, any
    area_min: float | None = None
    soft: dict[str, SoftPref] = field(default_factory=dict)  # feature name → pref
    notes: str = ""                                     # free-text extra wishes

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Requirement":
        hard = d.get("hard", {})
        soft = {k: SoftPref(**v) for k, v in d.get("soft", {}).items()}
        return cls(
            price_max=hard.get("price_max"),
            currency=hard.get("currency", "USD"),
            rooms=list(hard.get("rooms", [])),
            floor_min=hard.get("floor_min"),
            floor_not_top=bool(hard.get("floor_not_top", False)),
            districts=list(hard.get("districts", [])),
            area_min=hard.get("area_min"),
            soft=soft,
            notes=d.get("notes", ""),
        )
