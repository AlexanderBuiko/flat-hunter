"""
The hunt pipeline: hard filter → (AI) extract features → soft-score & rank.

Kept free of I/O so it's easy to test: ``hunt`` takes already-fetched listings and an
``extract_fn`` (the AI seam). Pass ``extract_fn=None`` to run the deterministic P1 path
(hard filter only); pass a real extractor to get soft ranking and critical gating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .matching import SoftResult, passes_hard, soft_evaluate
from .models import Listing, Requirement


@dataclass
class Match:
    listing: Listing
    features: dict | None
    soft: SoftResult | None
    price: "PriceInfo | None" = None      # filled by enrich_matches (P5)
    relist: "RelistInfo | None" = None

    @property
    def score(self) -> int:
        return self.soft.score if self.soft else 0


def hunt(
    listings: list[Listing],
    req: Requirement,
    extract_fn: Callable[[Listing], dict] | None = None,
) -> list[Match]:
    """Return the matching listings, ranked. Soft prefs need ``extract_fn``.

    Steps per listing: pass the hard gate; if the requirement has soft prefs and an
    extractor is given, extract features and drop listings whose *critical* prefs are
    contradicted; score the rest. Results are sorted best-first (scam-risky ones sink).
    """
    matches: list[Match] = []
    for lst in listings:
        if not passes_hard(lst, req):
            continue
        features: dict | None = None
        soft: SoftResult | None = None
        if req.soft and extract_fn is not None:
            features = extract_fn(lst)
            soft = soft_evaluate(features or {}, req)
            if soft.disqualified:
                continue
        matches.append(Match(lst, features, soft))

    def _rank(m: Match) -> tuple:
        risk = {"low": 0, "medium": 1, "high": 2}.get(m.soft.scam_risk if m.soft else "low", 0)
        return (-m.score, risk)      # higher score first, riskier last

    matches.sort(key=_rank)
    return matches


def enrich_matches(
    matches: list[Match],
    corpus: list[Listing],
    corpus_vecs: list[tuple[Listing, list[float]]] | None = None,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> list[Match]:
    """Attach price-vs-comparables (always) and relist detection (when embeddings are
    available) to each match. Pure over its inputs, so it's easy to test."""
    from .ai.dedup import find_relist
    from .ai.pricing import price_verdict

    for m in matches:
        m.price = price_verdict(m.listing, corpus)
        if embed_fn is not None and corpus_vecs:
            vec = embed_fn(m.listing.free_text() or m.listing.title or "")
            m.relist = find_relist(m.listing, vec, corpus_vecs) if vec else None
    return matches
