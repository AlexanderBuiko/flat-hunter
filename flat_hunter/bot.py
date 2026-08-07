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

import logging
import threading
import time
from collections import deque
from typing import Callable

from .ai.requirements import (
    build_requirement,
    diff_requirements,
    edit_requirement,
    summarize_requirement,
)
from .gateway.guards import scan_secrets
from .gateway.limiter import RateLimiter
from .notify import format_match
from .pipeline import Match
from .scheduler import run_for_user
from .store import Store

logger = logging.getLogger("flat_hunter.bot")

# How many recent messages per chat to keep for split-secret detection. A secret
# smuggled in as fragments across separate messages is invisible to the stateless
# gateway; the bot is the one tier that knows *who* the user is, so it holds this.
_SPLIT_WINDOW = 6

_INTRO = (
    "👋 I watch realt.by and ping you about new flats that fit you.\n\n"
    "Describe your ideal flat in one message — rooms, budget, floor, district, and any "
    "must-haves (e.g. \"2 rooms under $600, high floor but not top, must allow pets, "
    "ideally renovated with a dishwasher, quiet\")."
)
_HELP = ("/start — set up (or redo) your search\n/prefs — show your current search\n"
         "/edit <change> — tweak your search in words\n/search — check for matches now\n"
         "/stop — stop notifications")


def _call_telegram(token: str, method: str, params: dict, timeout: float = 35) -> dict:
    """POST to the Bot API. Isolated so tests can monkeypatch it."""
    import httpx

    r = httpx.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=timeout)
    return r.json()


class FlatBot:
    def __init__(self, token: str, allowed: set[str], store: Store, *, provider: str = "ollama",
                 rate_max: int = 20, rate_window_s: float = 3600.0,
                 burst_max: int = 6, burst_window_s: float = 60.0,
                 fetch_fn: Callable[[], list] | None = None,
                 extract_fn: Callable | None = None,
                 build_req: Callable[[str], dict] | None = None,
                 edit_req: Callable[[dict, str], dict] | None = None) -> None:
        self._token = token
        self._allowed = {str(a) for a in allowed}
        self._store = store
        self._provider = provider
        self._fetch_fn = fetch_fn
        self._extract_fn = extract_fn
        self._build_req = build_req or (lambda text: build_requirement(text, provider=provider))
        self._edit_req = edit_req or (lambda cur, text: edit_requirement(cur, text, provider=provider))
        # Per-user flood cap. The gateway's own limiter is per-IP, and every bot call
        # reaches it from the one bot IP, so it cannot throttle a single Telegram user.
        # This is the per-user budget the threat model needs: a deterministic ceiling on
        # how much model work one person can drive, independent of anything the model says.
        # Two windows: a short *burst* cap so one user's spike cannot drain the gateway's
        # shared per-minute bucket and starve everyone else, and a longer *sustained* cap.
        self._burst = RateLimiter(burst_max, burst_window_s)
        self._budget = RateLimiter(rate_max, rate_window_s)
        self._sessions: dict[str, dict] = {}
        self._history: dict[str, deque[str]] = {}   # recent messages per chat, for split-secret
        self._offset = 0
        self._stop = threading.Event()

    # ── messaging ────────────────────────────────────────────────────────────

    def _send(self, chat_id: str, text: str) -> None:
        _call_telegram(self._token, "sendMessage",
                       {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

    def notify(self, user_id: str, match: Match) -> None:
        """Push a new-listing notification (used by the scheduler)."""
        self._send(user_id, format_match(match))

    def _within_budget(self, chat: str) -> bool:
        """Return whether ``chat`` may drive another LLM call now; tell them if not.

        Keyed on ``chat`` because every other per-user thing in the bot is (sessions,
        stored requirement) and a private chat is one user. Checked at each LLM call
        site rather than centrally, so cheap actions (/prefs, /stop, yes/no) do not
        spend budget.
        """
        # Burst first (short window), then the sustained hourly cap. Either one over
        # the limit denies the action and reports how long to wait.
        for limiter in (self._burst, self._budget):
            if not limiter.check(chat):
                wait = int(limiter.retry_after(chat)) + 1
                self._send(chat, f"⏳ Usage limit reached. Try again in ~{wait}s.")
                return False
        return True

    def _carries_split_secret(self, chat: str, text: str) -> bool:
        """True if this message alone is clean but the recent window hides a secret.

        The gateway masks a secret that fits in one request; what it cannot see is a
        secret dribbled out over several messages. If ``text`` on its own has no
        secret but the window joined together does, that is the split attack — block
        it and reset the window. Concatenation ``"".join`` catches directly glued
        fragments (``"sk-"`` then ``"proj-…"``); entropy is off there so benign text
        run together does not trip.
        """
        if scan_secrets(text):
            return False                      # single-message secret — the gateway handles it
        hist = self._history.get(chat)
        if not hist or len(hist) < 2:
            return False
        if scan_secrets("".join(hist), entropy=False) or scan_secrets(" ".join(hist)):
            logger.warning("split-secret blocked for chat=%s (%d msgs)", chat, len(hist))
            self._history.pop(chat, None)
            self._send(chat, "⚠️ That looks like a secret spread across messages — blocked. "
                             "Please don't send API keys, tokens, or card numbers.")
            return True
        return False

    # ── update handling (the FSM) ────────────────────────────────────────────

    def handle_update(self, update: dict) -> None:
        msg = update.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        user = str((msg.get("from") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not chat or user not in self._allowed:
            return  # ignore strangers silently

        if text:
            self._history.setdefault(chat, deque(maxlen=_SPLIT_WINDOW)).append(text)

        if text.startswith("/"):
            cmd, _, rest = text.partition(" ")
            return self._command(chat, cmd.lower(), rest.strip())
        sess = self._sessions.get(chat)
        if not sess:
            # A freshly-allowed user (e.g. a red-team partner) lands here on their first
            # message. Show the command list — it leads with /start — so the bot is
            # self-documenting instead of pointing at one command they can't see yet.
            return self._send(chat, _HELP)
        if sess["state"] == "describe":
            return self._on_describe(chat, text)
        if sess["state"] == "editing":
            return self._start_edit(chat, sess["current"], text)
        if sess["state"] == "confirm":
            return self._on_confirm(chat, text)

    def _command(self, chat: str, cmd: str, rest: str = "") -> None:
        if cmd == "/start":
            self._sessions[chat] = {"state": "describe"}
            self._send(chat, _INTRO)
        elif cmd == "/prefs":
            req = self._store.get_requirement(chat)
            self._send(chat, summarize_requirement(req) if req else "No search yet — /start.")
        elif cmd == "/edit":
            cur = self._store.get_requirement(chat)
            if not cur:
                return self._send(chat, "No search yet — /start first.")
            if rest:
                self._start_edit(chat, cur, rest)
            else:
                self._sessions[chat] = {"state": "editing", "current": cur}
                self._send(chat, "What should I change? (e.g. \"drop the dishwasher, "
                                 "raise the budget to $700, closer to the centre\")")
        elif cmd == "/search":
            self._do_search(chat)
        elif cmd == "/stop":
            self._store.delete_requirement(chat)
            self._sessions.pop(chat, None)
            self._send(chat, "Stopped. No more notifications. /start to set up again.")
        else:
            self._send(chat, _HELP)

    def _on_describe(self, chat: str, text: str) -> None:
        if not self._within_budget(chat):
            return
        if self._carries_split_secret(chat, text):
            return
        req = self._build_req(text)
        if not req.get("hard") and not req.get("soft"):
            return self._send(chat, "I couldn't read any criteria from that — try again with "
                                    "rooms / budget / must-haves.")
        self._sessions[chat] = {"state": "confirm", "pending": req, "origin": "register"}
        self._send(chat, "Here's what I understood:\n\n" + summarize_requirement(req)
                   + "\n\nSave this search? (yes / no)")

    def _start_edit(self, chat: str, current: dict, instruction: str) -> None:
        if not self._within_budget(chat):
            return
        if self._carries_split_secret(chat, instruction):
            return
        new = self._edit_req(current, instruction)
        changes = diff_requirements(current, new)
        if changes == "(no changes)":
            self._sessions.pop(chat, None)
            return self._send(chat, "I couldn't turn that into a change — try /prefs then /edit.")
        self._sessions[chat] = {"state": "confirm", "pending": new, "origin": "edit"}
        self._send(chat, "Proposed changes:\n" + changes + "\n\nApply? (yes / no)")

    def _on_confirm(self, chat: str, text: str) -> None:
        sess = self._sessions[chat]
        if text.lower() in ("yes", "y", "да", "ok", "save", "apply"):
            self._store.save_requirement(chat, sess["pending"])
            self._sessions.pop(chat, None)
            done = "✅ Updated." if sess.get("origin") == "edit" else "✅ Saved."
            self._send(chat, f"{done} /search to check now.")
        elif text.lower() in ("no", "n", "нет", "cancel"):
            self._sessions.pop(chat, None)
            if sess.get("origin") == "edit":
                self._send(chat, "Cancelled — your search is unchanged.")
            else:
                self._sessions[chat] = {"state": "describe"}
                self._send(chat, "OK — describe your ideal flat again.")
        else:
            self._send(chat, "Please reply yes or no.")

    def _do_search(self, chat: str) -> None:
        req = self._store.get_requirement(chat)
        if not req:
            return self._send(chat, "No search yet — /start first.")
        if not self._within_budget(chat):
            return
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
