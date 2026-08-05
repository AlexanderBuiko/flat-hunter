"""
Thin LLM adapter over jarvis-cli's reusable core.

Everything in flat-hunter calls ``adapter.llm.complete`` — never ``jarvis.*`` directly.
So if jarvis's core is later extracted into its own package (``jarvis-core`` / ``llmkit``),
only this file changes. Defaults to a LOCAL Ollama engine (cheap, offline, good for the
per-listing extraction volume); pass ``provider="openrouter"`` for a cloud upgrade.
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple


def _gateway_complete(system: str, user: str, *, provider: str, model: str | None) -> str | None:
    """Route through the guarded gateway proxy when ``FLAT_HUNTER_GATEWAY_URL`` is set.

    Returns the reply text, or ``None`` when no gateway is configured (so the caller
    falls back to the in-process path). A guard block — input refused or output
    stripped — degrades to ``""`` so the bot gives a safe "no criteria" answer
    instead of crashing; only a genuine transport/provider failure raises.
    """
    url = os.environ.get("FLAT_HUNTER_GATEWAY_URL", "").strip()
    if not url:
        return None

    import httpx

    payload: dict[str, Any] = {"system": system, "user": user, "provider": provider}
    if model:
        payload["model"] = model
    resp = httpx.post(f"{url.rstrip('/')}/v1/complete", json=payload, timeout=120)
    data = resp.json()
    if resp.status_code == 400 and "input guard" in str(data.get("error", "")):
        return ""                       # blocked by the input guard → safe empty result
    if resp.status_code != 200:
        raise RuntimeError(f"gateway error {resp.status_code}: {data.get('error') or data}")
    if data.get("blocked"):
        return ""                       # output guard blocked the reply
    return (data.get("text") or "").strip()


def _import_core():
    """Return ``(LLMGateway, make_engine)`` or raise a clear install hint."""
    try:
        from jarvis.llm.gateway import LLMGateway
        from jarvis.llm.router import make_engine
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "flat-hunter's AI layer needs the jarvis core. Install it: "
            "pip install 'jarvis-cli @ git+https://github.com/AlexanderBuiko/jarvis-cli'"
        ) from exc
    return LLMGateway, make_engine


def complete(system: str, user: str, *, provider: str = "ollama",
             model: str | None = None, temperature: float = 0.1,
             max_tokens: int = 700) -> str:
    """Run a single-shot chat completion and return the text.

    Raises a clear error if the jarvis core isn't importable, so the caller can fall back
    or tell the user to install it.

    When ``FLAT_HUNTER_GATEWAY_URL`` is set, the call is routed through the guarded
    gateway proxy (input/output guard + audit + rate limit) instead of hitting the
    provider directly — the whole bot goes through one chokepoint with no code change.
    """
    routed = _gateway_complete(system, user, provider=provider, model=model)
    if routed is not None:
        return routed

    LLMGateway, make_engine = _import_core()
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    params: dict[str, Any] = {"temperature": temperature, "max_tokens": max_tokens}
    if model:
        params["model"] = model
    return LLMGateway(make_engine(provider)).complete(messages, params, label="flat-hunter").text.strip()


class LLMResult(NamedTuple):
    """A completion plus the accounting the gateway proxy needs to log cost."""
    text: str
    model: str | None            # the model the provider actually reported
    prompt_tokens: int | None    # None when the provider omits usage
    completion_tokens: int | None
    cost_usd: float | None       # None when pricing is unavailable
    latency_ms: float


def complete_metered(system: str, user: str, *, provider: str = "ollama",
                     model: str | None = None, temperature: float = 0.1,
                     max_tokens: int = 700) -> LLMResult:
    """Like ``complete`` but also returns token counts and dollar cost.

    The LLM gateway proxy needs real usage to track cost, and jarvis already
    computes it: ``LLMGateway.record`` builds a self-contained call record (usage
    + cost from the engine's cached pricing, $0 for local Ollama). We reuse that
    instead of estimating tokens, so the audit log is never guesswork.
    """
    LLMGateway, make_engine = _import_core()
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    params: dict[str, Any] = {"temperature": temperature, "max_tokens": max_tokens}
    if model:
        params["model"] = model

    gateway = LLMGateway(make_engine(provider))
    completion = gateway.complete(messages, params, label="flat-hunter")
    record = gateway.record(1, "flat-hunter", completion)
    return LLMResult(
        text=completion.text.strip(),
        model=record["response"].get("model"),
        prompt_tokens=(record["response"].get("usage") or {}).get("prompt_tokens"),
        completion_tokens=(record["response"].get("usage") or {}).get("completion_tokens"),
        cost_usd=record["cost"].get("total_usd"),
        latency_ms=completion.latency_ms,
    )
