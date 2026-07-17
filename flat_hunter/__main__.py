"""
CLI for the P1 spine — proves the pipeline end to end.

    python -m flat_hunter scrape --req sample_requirement.json          # live fetch
    python -m flat_hunter scrape --fixture tests/fixtures/listings.json # offline

Fetches (or loads) listings, stores them, applies the requirement's HARD filter, and
prints new matches with their URL. The AI steps (extract → rank → notify) layer on top
in later phases; this is the deterministic backbone.
"""

from __future__ import annotations

import argparse
import json
import sys

from .matching import filter_listings
from .models import Listing, Requirement
from .scraper import fetch_listings, objects_to_listings, parse_html
from .store import Store


def _load_fixture(path: str) -> list[Listing]:
    """Load listings from a saved page (.html) or a raw objects array (.json)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if path.endswith(".json"):
        return objects_to_listings(json.loads(text))
    return parse_html(text)


def _load_requirement(path: str | None) -> Requirement:
    if not path:
        # A sensible default so `scrape` works out of the box.
        return Requirement(price_max=800, currency="USD", rooms=[1, 2], floor_not_top=True)
    with open(path, encoding="utf-8") as fh:
        return Requirement.from_dict(json.load(fh))


def cmd_scrape(args: argparse.Namespace) -> int:
    listings = _load_fixture(args.fixture) if args.fixture else fetch_listings(pages=args.pages)
    req = _load_requirement(args.req)
    store = Store(args.db)

    new_matches: list[Listing] = []
    for lst in listings:
        is_new = store.upsert(lst)
        if is_new and not store.already_notified("cli", lst.code):
            new_matches.append(lst)
    matches = filter_listings(new_matches, req)

    print(f"fetched {len(listings)} · new {len(new_matches)} · hard-matched {len(matches)}\n")
    for m in matches:
        price = f"{m.price:.0f} {m.currency}" if m.price else "?"
        print(f"• [{m.rooms}-room, floor {m.storey}/{m.storeys}, {price}] {m.address}")
        print(f"  {m.title or ''}".rstrip())
        print(f"  {m.url}")
        store.mark_notified("cli", m.code)
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flat_hunter")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scrape", help="fetch/parse, store, hard-filter, print new matches")
    sc.add_argument("--fixture", help="parse a saved .html or .json instead of the network")
    sc.add_argument("--req", help="requirement JSON (hard/soft); omitted → a demo default")
    sc.add_argument("--pages", type=int, default=1, help="listing pages to fetch (1..10)")
    sc.add_argument("--db", default="flat_hunter.db", help="SQLite path")
    sc.set_defaults(func=cmd_scrape)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
