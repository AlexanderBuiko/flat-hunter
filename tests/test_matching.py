"""Hard-filter tests — the deterministic gate before the AI ranking."""

import json
import pathlib

from flat_hunter.matching import passes_hard, filter_listings
from flat_hunter.models import Requirement
from flat_hunter.scraper import objects_to_listings

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "listings.json"
_LISTINGS = objects_to_listings(json.loads(_FIXTURE.read_text(encoding="utf-8")))
_PENTHOUSE = _LISTINGS[0]   # 2-room, floor 32/32, 4000 USD, Минск


def test_price_cap_rejects_over_budget():
    assert passes_hard(_PENTHOUSE, Requirement(price_max=800, currency="USD")) is False
    assert passes_hard(_PENTHOUSE, Requirement(price_max=5000, currency="USD")) is True


def test_rooms_filter():
    assert passes_hard(_PENTHOUSE, Requirement(rooms=[2, 3])) is True
    assert passes_hard(_PENTHOUSE, Requirement(rooms=[1])) is False


def test_floor_not_top_rejects_top_floor():
    assert passes_hard(_PENTHOUSE, Requirement(floor_not_top=True)) is False


def test_floor_min():
    assert passes_hard(_PENTHOUSE, Requirement(floor_min=10)) is True
    assert passes_hard(_PENTHOUSE, Requirement(floor_min=40)) is False


def test_district_substring_match():
    assert passes_hard(_PENTHOUSE, Requirement(districts=["минск"])) is True
    assert passes_hard(_PENTHOUSE, Requirement(districts=["гродно"])) is False


def test_filter_listings_returns_subset():
    req = Requirement(rooms=[2], price_max=5000, currency="USD")
    out = filter_listings(_LISTINGS, req)
    assert all(l.rooms == 2 for l in out)
    assert _PENTHOUSE in out
