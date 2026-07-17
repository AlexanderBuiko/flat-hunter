"""
realt.by scraper — robots-compliant.

The rent listing pages are a Next.js app that embeds all listings as JSON in a
``<script id="__NEXT_DATA__">`` tag. We fetch the normal (allowed) HTML listing page and
parse that JSON — we do NOT hit ``/_next/data/...``, which ``robots.txt`` disallows under
``/_*``. Pagination (``?page=2..10``) is explicitly allowed by robots.

Flow:
    fetch_html(url) → extract_next_data(html) → objects_to_listings(objs) → [Listing]
"""

from __future__ import annotations

import json
import re

from .models import Listing, _CURRENCY

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)

# A courteous identifier; keep the interval long (hours) and never hammer the site.
USER_AGENT = "flat-hunter/0.1 (personal apartment watcher; contact: you@example.com)"
BASE_URL = "https://realt.by/rent/flat-for-long/"


def extract_next_data(html: str) -> dict:
    """Return the parsed ``__NEXT_DATA__`` JSON blob from a listing page's HTML."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("no __NEXT_DATA__ found — page layout changed or blocked")
    return json.loads(m.group(1))


def _num(v) -> float | None:
    return v if isinstance(v, (int, float)) and v != 0 else (v if v else None)


def objects_to_listings(objects: list[dict]) -> list[Listing]:
    """Map raw realt.by objects to :class:`Listing`s (opaque enum codes kept in ``raw``)."""
    out: list[Listing] = []
    for o in objects:
        code = o.get("code")
        if code is None:
            continue
        out.append(Listing(
            code=int(code),
            uuid=o.get("uuid"),
            title=o.get("title"),
            headline=o.get("headline"),
            description=o.get("description"),
            comments=o.get("comments"),
            rooms=o.get("rooms"),
            storey=o.get("storey"),
            storeys=o.get("storeys"),
            area_total=_num(o.get("areaTotal")),
            area_kitchen=_num(o.get("areaKitchen")),
            price=_num(o.get("price")),
            currency=_CURRENCY.get(o.get("priceCurrency")),
            price_change=o.get("priceChangeDirection"),
            address=o.get("address"),
            district=o.get("stateDistrictName"),
            town=o.get("townName"),
            metro=o.get("metroStationName"),
            building_year=o.get("buildingYear"),
            created_at=o.get("createdAt"),
            updated_at=o.get("updatedAt"),
            raw=o,
        ))
    return out


def parse_html(html: str) -> list[Listing]:
    """Full parse: HTML → listings."""
    data = extract_next_data(html)
    objects = data.get("props", {}).get("pageProps", {}).get("objects", [])
    return objects_to_listings(objects)


def fetch_html(url: str = BASE_URL, params: dict | None = None, timeout: float = 25) -> str:
    """GET a listing page politely. httpx is imported lazily so offline tests need no network."""
    import httpx

    resp = httpx.get(url, params=params or {}, timeout=timeout,
                     headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def fetch_listings(pages: int = 1, params: dict | None = None) -> list[Listing]:
    """Fetch and parse ``pages`` of listings (page 1..N). robots allows page=2..10."""
    all_: list[Listing] = []
    for page in range(1, pages + 1):
        p = dict(params or {})
        if page > 1:
            p["page"] = page
        all_.extend(parse_html(fetch_html(params=p)))
    return all_
