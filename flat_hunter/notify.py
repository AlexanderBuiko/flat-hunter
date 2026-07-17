"""Format a ranked Match into a Telegram notification message."""

from __future__ import annotations

from .pipeline import Match

_RISK = {"low": "", "medium": "⚠ possible scam", "high": "🚫 high scam risk"}


def format_match(m: Match) -> str:
    """A compact, human message: headline stats, why it fits, any warning, and the link."""
    lst = m.listing
    price = f"{lst.price:.0f} {lst.currency}" if lst.price else "?"
    lines = [f"🏠 {lst.rooms}-room · floor {lst.storey}/{lst.storeys} · {price}"]
    if lst.address:
        lines.append(lst.address)
    if m.price is not None and m.price.verdict != "n/a":
        lines.append(f"💰 {m.price.line()}")
    if m.relist is not None:
        lines.append(f"♻️ {m.relist.line()}")
    if m.soft is not None:
        lines.append(f"Match: {m.score}%")
        if m.soft.fits:
            lines.append("✓ " + ", ".join(m.soft.fits))
        if m.soft.concerns:
            lines.append("✗ " + ", ".join(m.soft.concerns))
        warn = _RISK.get(m.soft.scam_risk, "")
        if warn:
            flags = ", ".join(m.soft.red_flags)
            lines.append(f"{warn}{': ' + flags if flags else ''}")
        summary = (m.features or {}).get("summary")
        if summary:
            lines.append(f"— {summary}")
    lines.append(lst.url)
    return "\n".join(lines)
