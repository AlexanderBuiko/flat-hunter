"""
Telegram bot — registration (AI: NL → requirement schema), manual search, and the
channel new-listing notifications are pushed through.

Structure mirrors jarvis's support bot: a long-poll loop over ``getUpdates``, per-chat
session state, an allow-list, and a mockable ``_call_telegram`` so the whole FSM is
unit-testable with no network. Registration is the AI moment — the user *describes* their
ideal flat and the LLM turns it into the structured requirement (see ai/requirements.py).

Commands: /start (register), /prefs (show), /search (check now), /stop (unsubscribe).
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from .ai.requirements import build_requirement, summarize_requirement
from .notify import format_match
from .pipeline import Match
from .scheduler import run_for_user
from .store import Store

_INTRO = (
    "👋 I watch realt.by and ping you about new flats that fit you.\n\n"
    "Describe your ideal flat in one message — rooms, budget, floor, district, and any "
    "must-haves (e.g. \"2 rooms under $600, high floor but not top, must allow pets, "
    "ideally renovated with a dishwasher, quiet\")."
)
_HELP = ("/start — set up (or redo) your search\n/prefs — show your current search\n"
         "/search — check for matches now\n/stop — stop notifications")


def _call_telegram(token: str, method: str, params: dict, timeout: float = 35) -> dict:
    """POST to the Bot API. Isolated so tests can monkeypatch it."""
    import httpx

    r = httpx.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=timeout)
    return r.json()


class FlatBot:
    def __init__(self, token: str, allowed: set[str], store: Store, *, provider: str = "ollama",
                 fetch_fn: Callable[[], list] | None = None,
                 extract_fn: Callable | None = None,
                 build_req: Callable[[str], dict] | None = None) -> None:
        self._token = token
        self._allowed = {str(a) for a in allowed}
        self._store = store
        self._provider = provider
        self._fetch_fn = fetch_fn
        self._extract_fn = extract_fn
        self._build_req = build_req or (lambda text: build_requirement(text, provider=provider))
        self._sessions: dict[str, dict] = {}
        self._offset = 0
        self._stop = threading.Event()

    # ── messaging ────────────────────────────────────────────────────────────

    def _send(self, chat_id: str, text: str) -> None:
        _call_telegram(self._token, "sendMessage",
                       {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    def notify(self, user_id: str, match: Match) -> None:
        """Push a new-listing notification (used by the scheduler)."""
        self._send(user_id, format_match(match))

    # ── update handling (the FSM) ────────────────────────────────────────────

    def handle_update(self, update: dict) -> None:
        msg = update.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        user = str((msg.get("from") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not chat or user not in self._allowed:
            return  # ignore strangers silently

        if text.startswith("/"):
            return self._command(chat, text.split()[0].lower())
        sess = self._sessions.get(chat)
        if not sess:
            return self._send(chat, "Send /start to set up your search.")
        if sess["state"] == "describe":
            return self._on_describe(chat, text)
        if sess["state"] == "confirm":
            return self._on_confirm(chat, text)

    def _command(self, chat: str, cmd: str) -> None:
        if cmd == "/start":
            self._sessions[chat] = {"state": "describe"}
            self._send(chat, _INTRO)
        elif cmd == "/prefs":
            req = self._store.get_requirement(chat)
            self._send(chat, summarize_requirement(req) if req else "No search yet — /start.")
        elif cmd == "/search":
            self._do_search(chat)
        elif cmd == "/stop":
            self._store.delete_requirement(chat)
            self._sessions.pop(chat, None)
            self._send(chat, "Stopped. No more notifications. /start to set up again.")
        else:
            self._send(chat, _HELP)

    def _on_describe(self, chat: str, text: str) -> None:
        req = self._build_req(text)
        if not req.get("hard") and not req.get("soft"):
            return self._send(chat, "I couldn't read any criteria from that — try again with "
                                    "rooms / budget / must-haves.")
        self._sessions[chat] = {"state": "confirm", "pending": req}
        self._send(chat, "Here's what I understood:\n\n" + summarize_requirement(req)
                   + "\n\nSave this search? (yes / no)")

    def _on_confirm(self, chat: str, text: str) -> None:
        if text.lower() in ("yes", "y", "да", "ok", "save"):
            self._store.save_requirement(chat, self._sessions[chat]["pending"])
            self._sessions.pop(chat, None)
            self._send(chat, "✅ Saved. I'll notify you about new matches. /search to check now.")
        elif text.lower() in ("no", "n", "нет"):
            self._sessions[chat] = {"state": "describe"}
            self._send(chat, "OK — describe your ideal flat again.")
        else:
            self._send(chat, "Please reply yes or no.")

    def _do_search(self, chat: str) -> None:
        req = self._store.get_requirement(chat)
        if not req:
            return self._send(chat, "No search yet — /start first.")
        fetch = self._fetch_fn or self._live_fetch
        matches = run_for_user(self._store, chat, req,
                               fetch_fn=fetch, extract_fn=self._extractor())
        if not matches:
            return self._send(chat, "No new matches right now — I'll ping you when one appears.")
        self._send(chat, f"Found {len(matches)} new match(es):")
        for m in matches:
            self._send(chat, format_match(m))

    # ── real I/O (used when not injected for tests) ──────────────────────────

    def _live_fetch(self) -> list:
        from .scraper import fetch_listings
        return fetch_listings(pages=1)

    def _extractor(self):
        if self._extract_fn is not None:
            return self._extract_fn
        from .ai.extract import extract_features

        def cached(listing):
            hit = self._store.get_features(listing.code)
            if hit is not None:
                return hit
            feats = extract_features(listing, provider=self._provider)
            self._store.set_features(listing.code, feats)
            return feats
        return cached

    # ── poll loop ────────────────────────────────────────────────────────────

    def poll_once(self) -> None:
        resp = _call_telegram(self._token, "getUpdates",
                              {"offset": self._offset, "timeout": 25})
        for update in resp.get("result", []):
            self._offset = update["update_id"] + 1
            try:
                self.handle_update(update)
            except Exception as exc:  # noqa: BLE001 — one bad update mustn't kill the poller
                print(f"[bot] update failed: {exc}")

    def run(self) -> None:  # pragma: no cover - network loop
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[bot] poll failed: {exc}")
                time.sleep(3)

    def start(self) -> threading.Thread:  # pragma: no cover
        t = threading.Thread(target=self.run, name="flat-bot", daemon=True)
        t.start()
        return t

    def stop(self) -> None:  # pragma: no cover
        self._stop.set()
