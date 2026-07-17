"""
AI feature extraction (P2) — the flagship LLM step.

realt.by's structured fields are opaque enum codes; the *nuance* a renter cares about
lives in free text (``headline`` / ``title`` / ``description``), often mixing Russian and
brand names ("вся техника Miele", "тихий двор", "можно с животными"). This turns that
prose into a clean, filterable/rankable feature dict — the thing no regex can do.

Grounding rule: only assert what the text supports; ``null`` means *not mentioned*, never
guessed. Also surfaces scam signals so the notifier can warn the user.
"""

from __future__ import annotations

import json

from ..adapter import llm
from ..models import Listing

# The feature vocabulary. Values are true / false / null(=unmentioned). Extend as the
# picker's discovered attributes grow (P4).
FEATURE_KEYS = [
    "renovated", "dishwasher", "washing_machine", "air_conditioning", "furnished",
    "fridge", "balcony", "parking", "pets_allowed", "kids_allowed", "near_metro",
    "quiet", "good_for_remote_work", "basement", "premium_appliances",
]

_SYSTEM = (
    "You extract apartment features from a rental listing's free text (often Russian). "
    "Return ONLY a JSON object with these boolean-or-null keys: "
    + ", ".join(FEATURE_KEYS) + ". Use true only when the text clearly supports it, false "
    "when it clearly denies it, and null when it is not mentioned — never guess. Also add "
    '"scam_risk": one of "low"|"medium"|"high", "red_flags": [short strings] (e.g. no '
    "photos, prepayment before viewing, contradictory details, agency spam), and a one-"
    'sentence English "summary". Output valid JSON and nothing else.'
)


def _coerce(obj: dict) -> dict:
    """Keep only known keys; default missing feature keys to null; normalise metadata."""
    out = {k: obj.get(k) if obj.get(k) in (True, False) else None for k in FEATURE_KEYS}
    out["scam_risk"] = obj.get("scam_risk") if obj.get("scam_risk") in ("low", "medium", "high") else "low"
    out["red_flags"] = obj.get("red_flags") if isinstance(obj.get("red_flags"), list) else []
    out["summary"] = str(obj.get("summary", "")).strip()
    return out


def extract_features(listing: Listing, *, provider: str = "ollama") -> dict:
    """Extract a feature dict from a listing's free text. Empty text → all-null features."""
    text = listing.free_text()
    if not text:
        return _coerce({})
    raw = llm.complete(_SYSTEM, text, provider=provider, temperature=0.1)
    try:
        # Be tolerant of a stray ```json fence or trailing prose.
        start, end = raw.find("{"), raw.rfind("}")
        obj = json.loads(raw[start:end + 1]) if start != -1 else {}
    except (json.JSONDecodeError, ValueError):
        obj = {}
    return _coerce(obj)
