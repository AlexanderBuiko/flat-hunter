"""
Run the guarded LLM gateway: ``python -m flat_hunter.gateway``.

Reads ``FLAT_HUNTER_GATEWAY_HOST`` / ``FLAT_HUNTER_GATEWAY_PORT`` (defaults
127.0.0.1:8900) and the shared ``FLAT_HUNTER_GATEWAY_*`` knobs. Binds to
localhost by default — exposing it is a deployment decision, not a default.
"""

from __future__ import annotations

import logging
import os

from .server import build_server, gateway_from_env


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    host = os.environ.get("FLAT_HUNTER_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("FLAT_HUNTER_GATEWAY_PORT", "8900"))
    app = gateway_from_env()
    server = build_server(host, port, app)
    logging.getLogger("flat_hunter.gateway").info(
        "LLM gateway on http://%s:%d  (provider=%s, input_mode=%s, rate=%d/min, log=%s)",
        host, port, app.default_provider, app.input_mode,
        app.limiter.max_per_window, app.audit.path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
