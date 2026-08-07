"""
CLI for the pipeline.

    python -m flat_hunter scrape --fixture tests/fixtures/listings.json --req req.json
    python -m flat_hunter scrape --pages 1 --req req.json            # live
    python -m flat_hunter scrape --fixture ... --no-ai               # P1 hard-only

Fetches (or loads) listings, stores them, runs the hunt (hard filter → AI feature
extraction → soft ranking), and prints new matches with a "why it fits" line. AI
extraction is cached per listing and degrades gracefully if the model/core is offline.
"""

from __future__ import annotations

import argparse
import json
import sys

from .models import Listing, Requirement
from .pipeline import Match, hunt
from .scraper import fetch_listings, objects_to_listings, parse_html
from .store import Store


def _load_fixture(path: str) -> list[Listing]:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if path.endswith(".json"):
        return objects_to_listings(json.loads(text))
    return parse_html(text)


def _load_requirement(path: str | None) -> Requirement:
    if not path:
        return Requirement(price_max=5000, currency="USD", rooms=[1, 2, 3])
    with open(path, encoding="utf-8") as fh:
        return Requirement.from_dict(json.load(fh))


def _make_extractor(store: Store, provider: str):
    """A cached feature extractor, or None if the AI layer is unavailable.

    Returns a function listing→features that reads the store cache first and only calls
    the LLM (once) on a miss. On the first failure (no jarvis core / model offline) it
    disables AI for the run and tells the user.
    """
    from .ai.extract import extract_features

    disabled = {"flag": False}

    def extract(listing: Listing) -> dict:
        cached = store.get_features(listing.code)
        if cached is not None:
            return cached
        feats = extract_features(listing, provider=provider)
        store.set_features(listing.code, feats)
        return feats

    def guarded(listing: Listing) -> dict:
        if disabled["flag"]:
            return {}
        try:
            return extract(listing)
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash the run
            disabled["flag"] = True
            print(f"⚠  AI extraction unavailable ({exc}); ranking by hard filters only.\n")
            return {}

    return guarded


def _print_match(m: Match) -> None:
    lst = m.listing
    price = f"{lst.price:.0f} {lst.currency}" if lst.price else "?"
    head = f"• [{lst.rooms}-room · floor {lst.storey}/{lst.storeys} · {price}] {lst.address}"
    if m.soft is not None:
        head = f"{head}   —  match {m.score}%"
    print(head)
    if lst.title:
        print(f"  {lst.title.strip()}")
    if m.price is not None and m.price.verdict != "n/a":
        print(f"  💰 {m.price.line()}")
    if m.relist is not None:
        print(f"  ♻️ {m.relist.line()}")
    if m.soft is not None:
        if m.soft.fits:
            print(f"  ✓ fits: {', '.join(m.soft.fits)}")
        if m.soft.concerns:
            print(f"  ✗ concerns: {', '.join(m.soft.concerns)}")
        if m.soft.unconfirmed:
            print(f"  ? unconfirmed: {', '.join(m.soft.unconfirmed)}")
        if m.soft.scam_risk != "low" or m.soft.red_flags:
            print(f"  ⚠ scam risk {m.soft.scam_risk}: {', '.join(m.soft.red_flags) or '—'}")
    print(f"  {lst.url}")


def cmd_scrape(args: argparse.Namespace) -> int:
    listings = _load_fixture(args.fixture) if args.fixture else fetch_listings(pages=args.pages)
    req = _load_requirement(args.req)
    store = Store(args.db)

    new: list[Listing] = []
    for lst in listings:
        is_new = store.upsert(lst)
        if is_new and not store.already_notified("cli", lst.code):
            new.append(lst)

    extractor = None if args.no_ai else _make_extractor(store, args.provider)
    matches = hunt(new, req, extract_fn=extractor)
    # Enrich with price-vs-comparables (from the accumulated corpus). Dedup needs
    # embeddings; skip it in the CLI unless a model is wired.
    from .pipeline import enrich_matches
    enrich_matches(matches, corpus=store.all_listings())

    print(f"fetched {len(listings)} · new {len(new)} · matched {len(matches)}\n")
    for m in matches:
        _print_match(m)
        store.mark_notified("cli", m.listing.code)
    store.close()
    return 0


def cmd_bot(args: argparse.Namespace) -> int:
    """Run the Telegram bot (long-poll) + the interval scheduler."""
    import threading

    from .bot import FlatBot
    from .config import Config
    from .scheduler import run_loop

    cfg = Config.from_env()
    if not cfg.is_configured():
        print("Set FLAT_HUNTER_BOT_TOKEN and FLAT_HUNTER_ALLOWED_USER_IDS (see .env.example).")
        return 1
    store = Store(cfg.db_path)
    bot = FlatBot(cfg.bot_token, cfg.allowed_user_ids, store, provider=cfg.provider,
                  rate_max=cfg.user_rate_max, rate_window_s=cfg.user_rate_window_s,
                  burst_max=cfg.user_burst_max, burst_window_s=cfg.user_burst_window_s)
    # Background sweep: every interval, hunt for each user and push new matches.
    threading.Thread(
        target=run_loop, name="flat-scheduler", daemon=True,
        kwargs=dict(store=store, interval_s=cfg.interval_s, fetch_fn=bot._live_fetch,
                    extract_fn=bot._extractor(), send_fn=bot.notify),
    ).start()
    print(f"flat-hunter bot running (provider={cfg.provider}, interval={cfg.interval_s}s). Ctrl+C to stop.")
    bot.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flat_hunter")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bot", help="run the Telegram bot + scheduler").set_defaults(func=cmd_bot)
    sc = sub.add_parser("scrape", help="fetch/parse, store, hunt, print new matches")
    sc.add_argument("--fixture", help="parse a saved .html or .json instead of the network")
    sc.add_argument("--req", help="requirement JSON (hard/soft); omitted → a permissive default")
    sc.add_argument("--pages", type=int, default=1, help="listing pages to fetch (1..10)")
    sc.add_argument("--db", default="flat_hunter.db", help="SQLite path")
    sc.add_argument("--provider", default="ollama", help="LLM provider for extraction (ollama|openrouter)")
    sc.add_argument("--no-ai", action="store_true", help="hard filter only (skip AI extraction)")
    sc.set_defaults(func=cmd_scrape)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
