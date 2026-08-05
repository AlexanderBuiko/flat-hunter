"""
The gateway proxy — an HTTP server that guards every LLM call.

The request flow is one straight line, and each step is one of the modules in
this package:

    rate limit (limiter) → input guard (guards.scan_input) → forward
    (adapter.llm.complete_metered) → output guard (guards.scan_output)
    → audit + cost (audit.AuditLog)

The transport is stdlib ``http.server`` — flat-hunter's only runtime dependency
stays ``httpx``, and a single-process guarded proxy needs nothing heavier. The
request logic lives in ``Gateway.handle_complete``, which takes a client id and a
parsed body and returns ``(status, dict)`` with no socket in sight, so the whole
proxy is testable without binding a port.
"""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Protocol

from .audit import AuditLog
from .guards import scan_input, scan_output
from .limiter import RateLimiter

logger = logging.getLogger("flat_hunter.gateway")


class _Meter(Protocol):
    """The shape ``complete_fn`` must return — matches ``adapter.llm.LLMResult``."""
    text: str
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None
    latency_ms: float


def _default_complete(system: str, user: str, *, provider: str, model: str | None) -> _Meter:
    """Forward through flat-hunter's usual seam — never ``jarvis.*`` directly."""
    from ..adapter.llm import complete_metered
    return complete_metered(system, user, provider=provider, model=model)


class Gateway:
    """Holds the guards, limiter and audit log, and runs one request through them."""

    def __init__(
        self,
        *,
        complete_fn: Callable[..., _Meter] | None = None,
        limiter: RateLimiter | None = None,
        audit: AuditLog | None = None,
        input_mode: str = "mask",
        default_provider: str = "ollama",
        rate_per_min: int = 20,
    ) -> None:
        self._complete = complete_fn or _default_complete
        self.limiter = limiter or RateLimiter(rate_per_min, window_s=60.0)
        self.audit = audit or AuditLog()
        self.input_mode = input_mode
        self.default_provider = default_provider

    def handle_complete(self, client: str, body: dict[str, Any]) -> tuple[int, dict]:
        """Run one completion request end to end; return ``(http_status, json)``."""
        if not self.limiter.check(client):
            self.audit.record(
                client=client, provider="-", model=None, outcome="rate_limited",
                input_findings=[], output_findings=[], prompt_tokens=None,
                completion_tokens=None, cost_usd=None, latency_ms=None)
            return 429, {"error": "rate limit exceeded",
                         "retry_after": round(self.limiter.retry_after(client), 1)}

        user = body.get("user")
        if not isinstance(user, str) or not user.strip():
            return 400, {"error": "field 'user' (string) is required"}
        system = body.get("system") or ""
        provider = body.get("provider") or self.default_provider
        model = body.get("model")
        mode = body.get("input_mode") or self.input_mode

        # ── input guard ──────────────────────────────────────────────────────
        in_verdict = scan_input(user, mode=mode)
        if in_verdict.action == "block":
            self.audit.record(
                client=client, provider=provider, model=model, outcome="blocked_input",
                input_findings=in_verdict.findings, output_findings=[], prompt_tokens=None,
                completion_tokens=None, cost_usd=None, latency_ms=None)
            return 400, {"error": "blocked by input guard", "reason": in_verdict.reason,
                         "findings": [f.kind for f in in_verdict.findings]}

        # ── forward (masked prompt) ──────────────────────────────────────────
        try:
            result = self._complete(system, in_verdict.text, provider=provider, model=model)
        except Exception as exc:  # noqa: BLE001 — report upstream failure as 502, never crash
            self.audit.record(
                client=client, provider=provider, model=model, outcome="error",
                input_findings=in_verdict.findings, output_findings=[], prompt_tokens=None,
                completion_tokens=None, cost_usd=None, latency_ms=None)
            logger.warning("provider call failed: %s", exc)
            return 502, {"error": f"provider call failed: {exc}"}

        # ── output guard ─────────────────────────────────────────────────────
        out_verdict = scan_output(result.text, system_prompt=system)
        usage = {"prompt_tokens": result.prompt_tokens,
                 "completion_tokens": result.completion_tokens}

        if out_verdict.action == "block":
            self.audit.record(
                client=client, provider=provider, model=result.model, outcome="blocked_output",
                input_findings=in_verdict.findings, output_findings=out_verdict.findings,
                prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                cost_usd=result.cost_usd, latency_ms=result.latency_ms)
            return 200, {"blocked": True, "text": "", "reason": out_verdict.reason,
                         "output_findings": [f.kind for f in out_verdict.findings],
                         "usage": usage, "cost_usd": result.cost_usd, "model": result.model}

        self.audit.record(
            client=client, provider=provider, model=result.model, outcome="completed",
            input_findings=in_verdict.findings, output_findings=out_verdict.findings,
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd, latency_ms=result.latency_ms)
        return 200, {"text": out_verdict.text if out_verdict.action == "redact" else result.text,
                     "input_action": in_verdict.action, "output_action": out_verdict.action,
                     "input_findings": [f.kind for f in in_verdict.findings],
                     "output_findings": [f.kind for f in out_verdict.findings],
                     "usage": usage, "cost_usd": result.cost_usd, "model": result.model}


# ── HTTP translation layer ────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    """Thin adapter: parse the request, call ``Gateway``, write JSON back."""

    protocol_version = "HTTP/1.1"

    @property
    def _app(self) -> Gateway:
        return self.server.app  # type: ignore[attr-defined]

    def _client(self) -> str:
        # Honour X-Forwarded-For's first hop when behind a proxy/LB; else the peer.
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() if fwd else self.client_address[0]

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — stdlib-mandated name
        if self.path == "/healthz":
            self._send(200, {"status": "ok"})
        elif self.path == "/stats":
            self._send(200, self._app.audit.totals())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/complete":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            self._send(400, {"error": f"invalid JSON body: {exc}"})
            return
        status, payload = self._app.handle_complete(self._client(), body)
        self._send(status, payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)


def build_server(host: str, port: int, app: Gateway) -> ThreadingHTTPServer:
    """Create (but do not serve) the HTTP server bound to ``host:port``."""
    server = ThreadingHTTPServer((host, port), _Handler)
    server.app = app  # type: ignore[attr-defined]
    return server


def gateway_from_env() -> Gateway:
    """Build a Gateway from ``FLAT_HUNTER_GATEWAY_*`` / ``JARVIS_LLM_PROVIDER``."""
    return Gateway(
        input_mode=os.environ.get("FLAT_HUNTER_GATEWAY_INPUT_MODE", "mask"),
        default_provider=os.environ.get("JARVIS_LLM_PROVIDER", "ollama"),
        rate_per_min=int(os.environ.get("FLAT_HUNTER_GATEWAY_RATE", "20")),
    )
