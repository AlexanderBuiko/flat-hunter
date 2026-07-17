"""
Thin LLM adapter over jarvis-cli's reusable core.

Everything in flat-hunter calls ``adapter.llm.complete`` — never ``jarvis.*`` directly.
So if jarvis's core is later extracted into its own package (``jarvis-core`` / ``llmkit``),
only this file changes. Defaults to a LOCAL Ollama engine (cheap, offline, good for the
per-listing extraction volume); pass ``provider="openrouter"`` for a cloud upgrade.
"""

from __future__ import annotations

from typing import Any


def complete(system: str, user: str, *, provider: str = "ollama",
             model: str | None = None, temperature: float = 0.1,
             max_tokens: int = 700) -> str:
    """Run a single-shot chat completion and return the text.

    Raises a clear error if the jarvis core isn't importable, so the caller can fall back
    or tell the user to install it.
    """
    try:
        from jarvis.llm.gateway import LLMGateway
        from jarvis.llm.router import make_engine
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "flat-hunter's AI layer needs the jarvis core. Install it: "
            "pip install 'jarvis-cli @ git+https://github.com/AlexanderBuiko/jarvis-cli'"
        ) from exc

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    params: dict[str, Any] = {"temperature": temperature, "max_tokens": max_tokens}
    if model:
        params["model"] = model
    return LLMGateway(make_engine(provider)).complete(messages, params, label="flat-hunter").text.strip()
