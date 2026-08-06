"""
Audit log and cost tracking — one append-only JSONL line per request.

Every request through the gateway leaves a record: which guards fired, what was
masked or blocked, the model used, token counts and the dollar cost. The log is
the deliverable's "logs of intercepted secrets" — so it stores each finding's
*kind* and masked preview, never the raw secret. Cost totals accumulate in
memory for a live view and are also written per line for offline aggregation.

JSONL (one JSON object per line) is chosen over a single JSON array so the file
is append-only and survives a crash mid-write without corrupting earlier lines.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .guards import Finding

logger = logging.getLogger("flat_hunter.gateway.audit")


def _default_path() -> Path:
    root = os.environ.get("FLAT_HUNTER_GATEWAY_LOG")
    if root:
        return Path(root).expanduser()
    return Path.home() / ".flat-hunter" / "gateway-audit.jsonl"


def _findings_json(findings: list[Finding]) -> list[dict]:
    """Serialise findings for the log — kind + masked preview only, no raw value."""
    return [{"kind": f.kind, "preview": f.preview} for f in findings]


class AuditLog:
    """Append-only JSONL sink plus running cost/blocked counters."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.total_cost_usd = 0.0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.requests = 0
        self.blocked = 0

    def record(
        self,
        *,
        client: str,
        provider: str,
        model: str | None,
        outcome: str,                       # "completed" | "blocked_input" | "blocked_output" | "rate_limited" | "error"
        input_findings: list[Finding],
        output_findings: list[Finding],
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cost_usd: float | None,
        latency_ms: float | None,
    ) -> dict:
        """Write one audit line and fold its numbers into the running totals."""
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "client": client,
            "provider": provider,
            "model": model,
            "outcome": outcome,
            "input_findings": _findings_json(input_findings),
            "output_findings": _findings_json(output_findings),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost_usd,
            "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self.requests += 1
            if outcome.startswith("blocked") or outcome == "rate_limited":
                self.blocked += 1
            self.total_prompt_tokens += prompt_tokens or 0
            self.total_completion_tokens += completion_tokens or 0
            self.total_cost_usd += cost_usd or 0.0
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError as exc:  # never let logging break a request
                logger.warning("audit write failed: %s", exc)
        # Also emit one line to the logger. On a container (Cloud Run) the JSONL file is
        # not reachable from outside and is wiped on restart, so stdout logging is what
        # makes an attack observable afterwards. Finding *kinds* only — the raw secret is
        # never logged, same guarantee as the file (which stores kind + masked preview).
        logger.info("audit outcome=%s client=%s model=%s input=%s output=%s cost_usd=%s tokens=%s/%s latency_ms=%s",
                    outcome, client, model,
                    [f["kind"] for f in entry["input_findings"]],
                    [f["kind"] for f in entry["output_findings"]],
                    cost_usd, prompt_tokens, completion_tokens, entry["latency_ms"])
        return entry

    def totals(self) -> dict:
        """A snapshot of the running counters, e.g. for a ``/stats`` view."""
        with self._lock:
            return {
                "requests": self.requests,
                "blocked": self.blocked,
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "cost_usd": round(self.total_cost_usd, 6),
            }
