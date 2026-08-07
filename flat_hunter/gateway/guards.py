"""
Input and output guards — the pure, network-free core of the gateway.

Everything here is a plain function over strings so the whole security surface is
unit-testable without a model or a socket. The server layer wires these around
the provider call; it adds no detection logic of its own.

Two directions, mirror images of each other:

* ``scan_input``  — before a prompt reaches the model. Detects secrets the *user*
  leaked (API keys, cards, phones, emails), plus two obfuscations (base64, a
  secret split across string fragments). Default action is **mask** — replace the
  secret with a typed placeholder and let the (now-safe) prompt through — with an
  opt-in **block** mode that refuses instead.
* ``scan_output`` — before a reply reaches the user. Detects secrets the *model*
  emitted (it sometimes hallucinates real-looking keys), an echo of the system
  prompt, and suspicious URLs or shell commands.

A guard never puts a raw secret in its result's log preview: `Finding.preview` is
always masked, so an audit line can be written safely.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from collections import Counter
from typing import Callable, NamedTuple

# ── data carriers ────────────────────────────────────────────────────────────

class Finding(NamedTuple):
    """One detected item. ``preview`` is masked — safe to write to a log."""
    kind: str          # "openai_key", "card", "prompt_leak", "shell_command", …
    placeholder: str   # what a mask substitutes for it
    start: int         # span in the *scanned* text (−1 when not a literal span)
    end: int
    preview: str       # short, non-recoverable hint for the audit log


class InputVerdict(NamedTuple):
    action: str               # "allow" | "mask" | "block"
    text: str                 # text to forward (masked when action == "mask")
    findings: list[Finding]
    reason: str               # human-readable, used in a block warning


class OutputVerdict(NamedTuple):
    action: str               # "allow" | "redact" | "block"
    text: str                 # redacted reply when action == "redact"
    findings: list[Finding]
    reason: str


# ── secret detectors ─────────────────────────────────────────────────────────
# Order matters: structured, high-confidence patterns first, so a card's digits
# are claimed before the looser phone pattern can grab them.

_REDACT_KEY = "[REDACTED_API_KEY]"
_REDACT_CARD = "[REDACTED_CARD]"
_REDACT_EMAIL = "[REDACTED_EMAIL]"
_REDACT_PHONE = "[REDACTED_PHONE]"
_REDACT_ENCODED = "[REDACTED_ENCODED_SECRET]"


def _luhn_ok(digits_text: str) -> bool:
    """Luhn checksum — rejects the many 13–19 digit runs that are not cards."""
    digits = [int(c) for c in digits_text if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# (kind, placeholder, pattern, optional validator on the matched text)
_DETECTORS: list[tuple[str, str, re.Pattern[str], Callable[[str], bool] | None]] = [
    ("openai_key",   _REDACT_KEY,   re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{4,}\b"), None),
    ("github_token", _REDACT_KEY,   re.compile(r"\bgh[posru]_[A-Za-z0-9]{20,}\b"), None),
    ("aws_key",      _REDACT_KEY,   re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("card",         _REDACT_CARD,  re.compile(r"\b(?:\d[ -]?){13,19}\b"), _luhn_ok),
    ("email",        _REDACT_EMAIL, re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), None),
    ("phone",        _REDACT_PHONE, re.compile(r"(?<![\w+])\+?\d(?:[\d\s().-]{6,13})\d(?!\w)"), None),
]

_BASE64_RE = re.compile(r"\b[A-Za-z0-9+/]{16,}={0,2}\b")
# "sk-" + "proj-abc"  /  'sk-' 'proj'  — string fragments glued by + or adjacency.
_CONCAT_RE = re.compile(r"""["']\s*(?:\+\s*)?["']""")


def _safe_preview(kind: str, matched: str) -> str:
    """A masked hint that identifies the *kind* without leaking the value.

    Key schemes keep their public prefix (``sk-``, ``ghp_``, ``AKIA`` are not
    themselves secret); everything else is reduced to a length, never digits.
    """
    if kind in ("openai_key", "github_token", "aws_key"):
        return f"{matched[:4]}… ({len(matched)} chars)"
    if kind == "email":
        return f"…@… ({len(matched)} chars)"
    return f"{kind} ({sum(c.isdigit() for c in matched)} digits)"


def _literal_findings(text: str) -> list[Finding]:
    """Non-overlapping detector matches, priority order, left to right."""
    claimed: list[tuple[int, int]] = []
    out: list[Finding] = []
    for kind, placeholder, pattern, validator in _DETECTORS:
        for m in pattern.finditer(text):
            if validator and not validator(m.group()):
                continue
            s, e = m.start(), m.end()
            if any(s < ce and cs < e for cs, ce in claimed):  # overlaps a claim
                continue
            claimed.append((s, e))
            out.append(Finding(kind, placeholder, s, e, _safe_preview(kind, m.group())))
    out.sort(key=lambda f: f.start)
    return out


# A long, unbroken alphanumeric run — the shape of a token with no known prefix.
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9]{24,}\b")


def _shannon_bits(s: str) -> float:
    """Shannon entropy in bits per character — how unpredictable the string is.

    A machine-generated secret spreads its characters near-uniformly and scores
    high; a long English word repeats a few letters and scores low. This is the
    signal the prefix/Luhn detectors lack.
    """
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values()) if n else 0.0


def _entropy_findings(text: str, claimed: list[tuple[int, int]]) -> list[Finding]:
    """High-entropy tokens with no known prefix — a secret the pattern detectors miss.

    Deliberately narrow to avoid false positives: 24+ chars, *both* letters and
    digits (the fingerprint of a generated key, not a word), entropy over 3.5
    bits/char, and not already claimed by a stronger detector.
    """
    out: list[Finding] = []
    for m in _TOKEN_RE.finditer(text):
        s, e = m.start(), m.end()
        if any(s < ce and cs < e for cs, ce in claimed):
            continue
        tok = m.group()
        if not (any(c.isalpha() for c in tok) and any(c.isdigit() for c in tok)):
            continue
        if _shannon_bits(tok) < 3.5:
            continue
        out.append(Finding("high_entropy_secret", _REDACT_KEY, s, e, f"entropy ({len(tok)} chars)"))
    return out


def _encoded_findings(text: str) -> list[Finding]:
    """base64 blobs that decode to something a detector recognises."""
    out: list[Finding] = []
    for m in _BASE64_RE.finditer(text):
        blob = m.group()
        try:
            decoded = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True).decode("utf-8", "ignore")
        except (binascii.Error, ValueError):
            continue
        if _literal_findings(decoded):
            out.append(Finding("base64_secret", _REDACT_ENCODED, m.start(), m.end(),
                               f"base64 → {len(blob)} chars"))
    return out


def _split_secret(text: str) -> Finding | None:
    """A secret hidden by splitting it across quoted fragments.

    We only detect it — we cannot cleanly mask a span that does not exist until
    the fragments are glued — so the server blocks on it in mask mode too.
    """
    if not _CONCAT_RE.search(text):
        return None
    dejoined = _CONCAT_RE.sub("", text)
    revealed = _literal_findings(dejoined)
    already = {(f.kind, f.preview) for f in _literal_findings(text)}
    for f in revealed:
        if (f.kind, f.preview) not in already:
            return Finding("split_secret", f.placeholder, -1, -1, f"split {f.kind}")
    return None


# Known jailbreak / role-play markers. Case-insensitive; kept narrow because the
# app's real input is an apartment description — these phrases are clearly out of
# that domain, so a match is a strong signal with little false-positive risk here.
_JAILBREAK_RE = re.compile(
    r"ignore (?:all |any )?(?:the )?(?:previous|prior|earlier|above) (?:instructions?|prompts?|rules?)"
    r"|disregard (?:the |your )?(?:previous |system )?(?:instructions?|prompt|rules?)"
    r"|forget (?:all |everything )?(?:your |the )?(?:previous |above )?(?:instructions?|rules?)"
    r"|you are (?:now )?dan\b|\bdan mode\b|do anything now"
    r"|developer mode|\bjailbreak\b"
    r"|no (?:restrictions|limitations|filters?)|without (?:any )?(?:restrictions|limitations|filters?)"
    r"|you are (?:now )?(?:an? )?(?:unrestricted|unfiltered|uncensored)"
    r"|pretend (?:to be|you are|that you)"
    r"|bypass your (?:rules|guidelines|restrictions|safety)",
    re.I)


def _jailbreak_findings(text: str) -> list[Finding]:
    """Known jailbreak markers — flagged for the audit log, never masked or blocked.

    A probabilistic model cannot be *prevented* from adopting a persona, and the other
    layers (schema coercion, output guard, no tools) already make a successful jailbreak
    harmless. This exists to make the *attempt* observable: the finding rides into the
    audit record so a DAN-style try shows up as ``input=['jailbreak_attempt']`` in logs.
    """
    m = _JAILBREAK_RE.search(text)
    if not m:
        return []
    return [Finding("jailbreak_attempt", "[FLAGGED]", -1, -1, f"marker: {m.group()[:24]}")]


def _mask(text: str, findings: list[Finding]) -> str:
    """Replace each literal-span finding with its placeholder, right to left."""
    for f in sorted((f for f in findings if f.start >= 0), key=lambda f: f.start, reverse=True):
        text = text[:f.start] + f.placeholder + text[f.end:]
    return text


# ── public: secret detection (no masking, no split) ──────────────────────────

def scan_secrets(text: str, *, entropy: bool = True) -> list[Finding]:
    """Every secret finding in ``text`` — literal patterns, base64, high-entropy
    tokens — with spans de-conflicted so they never overlap.

    Public and stateless so a per-user caller (the bot) can reuse the exact same
    detection across message boundaries, closing the split-across-messages gap the
    stateless proxy cannot see. ``entropy=False`` restricts it to structured secrets
    for callers that glue fragments and would otherwise trip on benign concatenations.
    """
    literal = _literal_findings(text)
    encoded = _encoded_findings(text)
    if not entropy:
        return literal + encoded
    claimed = [(f.start, f.end) for f in literal + encoded if f.start >= 0]
    return literal + encoded + _entropy_findings(text, claimed)


# ── public: input guard ──────────────────────────────────────────────────────

def scan_input(text: str, *, mode: str = "mask") -> InputVerdict:
    """Guard a prompt before it reaches the model.

    ``mode`` is ``"mask"`` (default — redact secrets and forward), ``"block"``
    (refuse if any secret is present), or ``"off"`` (pass through untouched). A
    split-across-fragments secret cannot be masked in place, so it forces a block
    even in mask mode.
    """
    if mode == "off":
        return InputVerdict("allow", text, [], "")

    secrets = scan_secrets(text)
    split = _split_secret(text)
    if split:
        secrets.append(split)
    flags = _jailbreak_findings(text)          # observability only — never masks or blocks
    findings = secrets + flags

    if not findings:
        return InputVerdict("allow", text, [], "")

    kinds = ", ".join(sorted({f.kind for f in findings}))
    if not secrets:
        # Only a jailbreak flag: forward the text (the model resists, the other layers
        # contain the damage) but carry the finding so the attempt is logged, not dropped.
        return InputVerdict("allow", text, findings, f"flagged: {kinds}")
    if mode == "block" or split is not None:
        why = ("secret split across fragments cannot be masked; blocked"
               if split is not None and mode != "block" else f"secret(s) detected: {kinds}")
        return InputVerdict("block", text, findings, why)

    return InputVerdict("mask", _mask(text, secrets), findings, f"masked: {kinds}")


# ── output detectors (things the model should not have said) ──────────────────

_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)
_SHELL_RE = re.compile(
    r"(?:\brm\s+-rf\b|\bcurl\s|\bwget\s|\|\s*sh\b|\bbash\s+-c\b|\bsudo\s"
    r"|X-Backdoor|\bpowershell\b|\bnc\s+-e\b)", re.I)


def _normalise(s: str) -> str:
    return " ".join(s.lower().split())


def _prompt_echo(text: str, system_prompt: str | None) -> Finding | None:
    """Flag a reply that parrots a long, verbatim slice of the system prompt."""
    if not system_prompt:
        return None
    hay = _normalise(text)
    # Split on sentence and line boundaries: a leak often echoes one sentence of
    # a multi-sentence line, not the whole line verbatim.
    for segment in re.split(r"[.\n]", system_prompt):
        needle = _normalise(segment)
        if len(needle) >= 40 and needle in hay:
            return Finding("prompt_leak", "[BLOCKED]", -1, -1, f"echoed {len(needle)} chars of system prompt")
    return None


def scan_output(text: str, *, system_prompt: str | None = None) -> OutputVerdict:
    """Guard a model reply before it reaches the user.

    Secrets and URLs are redacted; a system-prompt echo or an embedded shell
    command is treated as higher severity and blocks the whole reply.
    """
    findings = _literal_findings(text)          # model-hallucinated secrets
    for m in _URL_RE.finditer(text):
        findings.append(Finding("suspicious_url", "[REDACTED_URL]", m.start(), m.end(),
                                f"url ({len(m.group())} chars)"))
    for m in _SHELL_RE.finditer(text):
        findings.append(Finding("shell_command", "[BLOCKED]", m.start(), m.end(),
                                f"cmd: {m.group().strip()[:20]}"))
    # Entropy last, skipping anything already claimed above (a URL is high-entropy too).
    claimed = [(f.start, f.end) for f in findings if f.start >= 0]
    findings += _entropy_findings(text, claimed)

    echo = _prompt_echo(text, system_prompt)
    if echo:
        findings.append(echo)

    if not findings:
        return OutputVerdict("allow", text, [], "")

    kinds = sorted({f.kind for f in findings})
    if "prompt_leak" in kinds or "shell_command" in kinds:
        return OutputVerdict("block", "", findings, f"unsafe output: {', '.join(kinds)}")

    findings.sort(key=lambda f: f.start)
    return OutputVerdict("redact", _mask(text, findings), findings, f"redacted: {', '.join(kinds)}")
