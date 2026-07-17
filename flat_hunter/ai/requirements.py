"""
NL → requirement schema (AI touchpoint for registration/editing).

The user describes their ideal flat in plain language ("2 rooms under $600, high floor
but not top, must allow pets, ideally renovated with a dishwasher, quiet"); the LLM turns
that into the structured :class:`~flat_hunter.models.Requirement` dict — hard filters plus
weighted/critical soft prefs drawn from the known feature vocabulary. This is what makes
registration feel smart instead of a 20-field form.
"""

from __future__ import annotations

import json

from ..adapter import llm
from .extract import FEATURE_KEYS

_SYSTEM = (
    "You convert a person's description of their ideal rental flat into a JSON search "
    "requirement. Output ONLY this JSON shape:\n"
    '{"hard": {"price_max": number|null, "currency": "USD"|"EUR"|"BYN", '
    '"rooms": [int], "floor_min": int|null, "floor_not_top": bool, '
    '"districts": [string], "area_min": number|null}, '
    '"soft": {FEATURE: {"want": bool, "weight": 1-5, "critical": bool}}, '
    '"notes": string}\n'
    "FEATURE must be one of: " + ", ".join(FEATURE_KEYS) + ". "
    "Use `critical: true` only for must-haves the user states firmly (e.g. 'must allow "
    "pets'); higher weight = more important. Put anything you can't map to a field or a "
    "known feature into `notes`. Omit fields the user didn't mention. JSON only."
)

_ALLOWED_HARD = {"price_max", "currency", "rooms", "floor_min", "floor_not_top",
                 "districts", "area_min"}


def _coerce(obj: dict) -> dict:
    """Keep only known fields/features and sane types, so a stray key can't break matching."""
    hard = {k: v for k, v in (obj.get("hard") or {}).items() if k in _ALLOWED_HARD}
    soft = {}
    for name, spec in (obj.get("soft") or {}).items():
        if name in FEATURE_KEYS and isinstance(spec, dict):
            soft[name] = {
                "want": bool(spec.get("want", True)),
                "weight": max(1, min(5, int(spec.get("weight", 1) or 1))),
                "critical": bool(spec.get("critical", False)),
            }
    return {"hard": hard, "soft": soft, "notes": str(obj.get("notes", "")).strip()}


def build_requirement(description: str, *, provider: str = "ollama") -> dict:
    """Turn a natural-language description into a requirement dict (hard/soft/notes)."""
    if not (description or "").strip():
        return {"hard": {}, "soft": {}, "notes": ""}
    raw = llm.complete(_SYSTEM, description, provider=provider, temperature=0.1)
    try:
        start, end = raw.find("{"), raw.rfind("}")
        obj = json.loads(raw[start:end + 1]) if start != -1 else {}
    except (json.JSONDecodeError, ValueError):
        obj = {}
    return _coerce(obj)


_EDIT_SYSTEM = (
    "You update a saved rental search. Given the CURRENT search as JSON and a change "
    "instruction, output the FULL updated JSON in the same shape (hard/soft/notes) — keep "
    "everything the instruction doesn't touch, and only add/remove/adjust what it asks. "
    "Use the same field and feature vocabulary. JSON only.\n"
    "Feature vocabulary: " + ", ".join(FEATURE_KEYS)
)


def edit_requirement(current: dict, instruction: str, *, provider: str = "ollama") -> dict:
    """Apply a natural-language change to an existing requirement, returning the new one."""
    if not (instruction or "").strip():
        return _coerce(current)
    user = f"CURRENT:\n{json.dumps(current, ensure_ascii=False)}\n\nCHANGE: {instruction}"
    raw = llm.complete(_EDIT_SYSTEM, user, provider=provider, temperature=0.1)
    try:
        start, end = raw.find("{"), raw.rfind("}")
        obj = json.loads(raw[start:end + 1]) if start != -1 else {}
    except (json.JSONDecodeError, ValueError):
        obj = {}
    return _coerce(obj) if obj else _coerce(current)


def diff_requirements(old: dict, new: dict) -> str:
    """A short human-readable list of what changed between two requirement dicts."""
    lines: list[str] = []
    oh, nh = old.get("hard", {}), new.get("hard", {})
    for key in sorted(set(oh) | set(nh)):
        if oh.get(key) != nh.get(key):
            lines.append(f"• {key}: {oh.get(key, '—')} → {nh.get(key, '—')}")
    os_, ns = old.get("soft", {}), new.get("soft", {})
    for name in sorted(set(os_) | set(ns)):
        if name not in ns:
            lines.append(f"• drop want: {name}")
        elif name not in os_:
            lines.append(f"• add want: {name}")
        elif os_[name] != ns[name]:
            lines.append(f"• want {name}: {os_[name]} → {ns[name]}")
    if old.get("notes", "") != new.get("notes", ""):
        lines.append(f"• notes: {new.get('notes', '') or '—'}")
    return "\n".join(lines) if lines else "(no changes)"


def summarize_requirement(req: dict) -> str:
    """A short human-readable recap of a requirement dict (for the confirm step)."""
    hard, soft = req.get("hard", {}), req.get("soft", {})
    bits = []
    if hard.get("rooms"):
        bits.append(f"{'/'.join(map(str, hard['rooms']))}-room")
    if hard.get("price_max"):
        bits.append(f"≤ {hard['price_max']:.0f} {hard.get('currency', 'USD')}")
    if hard.get("floor_min"):
        bits.append(f"floor ≥ {hard['floor_min']}")
    if hard.get("floor_not_top"):
        bits.append("not top floor")
    if hard.get("districts"):
        bits.append("in " + ", ".join(hard["districts"]))
    line = "Hard: " + (", ".join(bits) if bits else "any")
    if soft:
        want = [f"{'no ' if not s['want'] else ''}{n}" + ("!" if s.get("critical") else "")
                for n, s in soft.items()]
        line += "\nWants: " + ", ".join(want)
    if req.get("notes"):
        line += f"\nNotes: {req['notes']}"
    return line
