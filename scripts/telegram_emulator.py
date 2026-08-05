#!/usr/bin/env python3
"""
Telegram UI emulator for flat-hunter, with the Day-13 gateway input guard.

No bot token and no network: it feeds fake Telegram ``update`` dicts into the
REAL ``FlatBot.handle_update`` and prints whatever the bot would ``sendMessage``,
so the terminal reads like a Telegram chat. Every user message first passes
through the gateway's ``scan_input`` — exactly what the bot will do once it is
wired to the proxy — so you can watch a pasted API key get masked, or a split
secret get refused, from the "Telegram" side. Each message also writes an audit
line, so the same log you inspect for the HTTP proxy fills up here too.

This is a demo/testing aid (the bot→gateway wiring itself is a separate task); it
injects the guard at the message seam rather than changing production code.

Run (from the flat-hunter repo root):

    JARVIS_LLM_PROVIDER=ollama \\
        PYTHONPATH=.:/path/to/jarvis-cli python3 scripts/telegram_emulator.py

Then type like a Telegram user:

    /start
    cozy 2-room under 700 BYN, pets, dishwasher, quiet  (btw key sk-proj-ABC123456789)
    yes
    /prefs
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from flat_hunter import bot as botmod
from flat_hunter.bot import FlatBot
from flat_hunter.gateway import guards
from flat_hunter.gateway.audit import AuditLog
from flat_hunter.store import Store

USER_ID = "424242"          # a single allow-listed "Telegram user"
PROVIDER = os.environ.get("JARVIS_LLM_PROVIDER", "ollama")


def _print_send(token: str, method: str, params: dict, timeout: float = 35) -> dict:
    """Stand in for the Bot API: print what the bot would send, touch no network."""
    if method == "sendMessage":
        for line in params["text"].splitlines():
            print(f"  🤖  {line}")
    return {"ok": True, "result": []}


def main() -> int:
    botmod._call_telegram = _print_send          # every bot reply now prints locally
    audit = AuditLog(Path(tempfile.mkdtemp()) / "telegram-gateway-audit.jsonl")
    store = Store(str(Path(tempfile.mkdtemp()) / "emulator.db"))
    # fetch_fn returns [] so /search never hits realt.by — the demo stays offline.
    bot = FlatBot("emulator-token", {USER_ID}, store, provider=PROVIDER, fetch_fn=lambda: [])

    print(f"flat-hunter Telegram emulator  (provider={PROVIDER}, guard=on)")
    print("type as the user; Ctrl-D or /quit to exit\n")
    print("  🤖  Send /start to set up your search.")

    for raw in sys.stdin:
        text = raw.rstrip("\n")
        if not text.strip():
            continue
        if text.strip() in ("/quit", "/exit"):
            break
        print(f"  🧑  {text}")

        # ── the gateway input guard, at the bot's message seam ────────────────
        verdict = guards.scan_input(text)
        audit.record(
            client=f"telegram:{USER_ID}", provider=PROVIDER, model=None,
            outcome="blocked_input" if verdict.action == "block" else "completed",
            input_findings=verdict.findings, output_findings=[], prompt_tokens=None,
            completion_tokens=None, cost_usd=None, latency_ms=None)

        if verdict.action == "block":
            kinds = ", ".join(f.kind for f in verdict.findings)
            print(f"  🛡  gateway BLOCKED this message ({kinds}) — not sent to the assistant")
            print(f"  🤖  ⚠️ That message looked like it contained a secret ({kinds}); "
                  f"I did not process it. Please resend without the sensitive value.")
            continue
        if verdict.action == "mask":
            kinds = ", ".join(f.kind for f in verdict.findings)
            print(f"  🛡  gateway masked {kinds} before the assistant saw it")
            text = verdict.text

        update = {"message": {"chat": {"id": USER_ID}, "from": {"id": USER_ID}, "text": text}}
        try:
            bot.handle_update(update)
        except Exception as exc:  # noqa: BLE001 — keep the chat alive on a bad turn
            print(f"  ⚠️  turn failed: {exc}")

    print(f"\naudit log: {audit.path}")
    print("totals:", audit.totals())
    return 0


if __name__ == "__main__":
    sys.exit(main())
