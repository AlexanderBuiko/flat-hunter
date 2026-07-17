"""
Thin embeddings adapter over jarvis-cli's indexing core (used for dedup / relist
detection and price-vs-comparables RAG in P5). Isolated here for the same reason as
``adapter/llm.py``: a future core extraction touches only this file.
"""

from __future__ import annotations


def embed(texts: list[str], *, provider: str = "ollama",
          model: str = "nomic-embed-text") -> list[list[float]]:
    """Embed texts with jarvis's embedder abstraction. Raises clearly if core is missing."""
    try:
        from jarvis.indexing import make_embedder
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError("flat-hunter's dedup/RAG needs the jarvis core (see adapter/llm.py).") from exc
    return make_embedder(provider, model).embed(texts)
