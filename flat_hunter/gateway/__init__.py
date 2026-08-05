"""
LLM Gateway — a guarded HTTP proxy in front of every model call.

Week-10 (Security), Day 13. The lecture's "LLM Gateway as a single chokepoint"
made real: a small HTTP service that sits between a caller and the provider and
runs cross-cutting security concerns in one place — input guard (secret
detection + masking), output guard (leaked secrets / prompt echo / suspicious
URLs), per-IP rate limiting, cost tracking and an append-only audit log.

The guards (`guards.py`) are pure functions with no network, so they carry the
test suite. The server (`server.py`) wires them around the provider call, which
it makes through flat-hunter's usual `adapter.llm` seam — never `jarvis.*`
directly.
"""

from __future__ import annotations

from .guards import scan_input, scan_output, InputVerdict, OutputVerdict

__all__ = ["scan_input", "scan_output", "InputVerdict", "OutputVerdict"]
