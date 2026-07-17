"""Telegram FSM tests — Telegram API and the LLM are faked; no network, no model."""

import pytest

from flat_hunter import bot as botmod
from flat_hunter.models import Listing
from flat_hunter.store import Store


@pytest.fixture
def bot(tmp_path, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(botmod, "_call_telegram",
                        lambda token, method, params, timeout=35:
                        (sent.append((params.get("chat_id"), params.get("text", "")))
                         if method == "sendMessage" else None) or {})
    store = Store(str(tmp_path / "t.db"))
    # fake NL→schema and fake search I/O
    build_req = lambda text: {"hard": {"rooms": [2], "price_max": 800, "currency": "USD"},
                              "soft": {"dishwasher": {"want": True, "weight": 2}}, "notes": ""}
    fetch_fn = lambda: [Listing(code=10, rooms=2, price=500, currency="USD",
                                address="Минск", title="Nice flat")]
    extract_fn = lambda l: {"dishwasher": True, "scam_risk": "low", "red_flags": []}
    # fake edit: raise the budget to 700 and drop the dishwasher want
    edit_req = lambda cur, text: {"hard": {**cur.get("hard", {}), "price_max": 700},
                                  "soft": {}, "notes": cur.get("notes", "")}
    b = botmod.FlatBot("tkn", {"42"}, store, fetch_fn=fetch_fn, extract_fn=extract_fn,
                       build_req=build_req, edit_req=edit_req)
    return b, sent, store


def _msg(text, user="42", chat="99"):
    return {"message": {"from": {"id": user}, "chat": {"id": chat}, "text": text}}


def test_rejects_strangers(bot):
    b, sent, _ = bot
    b.handle_update(_msg("/start", user="999"))
    assert sent == []


def test_no_session_prompts_start(bot):
    b, sent, _ = bot
    b.handle_update(_msg("hello"))
    assert "start" in sent[-1][1].lower()


def test_registration_flow_saves_requirement(bot):
    b, sent, store = bot
    b.handle_update(_msg("/start"))
    assert b._sessions["99"]["state"] == "describe"
    b.handle_update(_msg("2 rooms under 800 with a dishwasher"))
    assert b._sessions["99"]["state"] == "confirm"
    assert "understood" in sent[-1][1].lower()
    b.handle_update(_msg("yes"))
    assert "99" not in b._sessions                    # session closed
    assert store.get_requirement("99")["hard"]["rooms"] == [2]
    assert "saved" in sent[-1][1].lower()


def test_confirm_no_restarts_description(bot):
    b, sent, _ = bot
    b.handle_update(_msg("/start"))
    b.handle_update(_msg("2 rooms"))
    b.handle_update(_msg("no"))
    assert b._sessions["99"]["state"] == "describe"


def test_prefs_shows_saved(bot):
    b, sent, store = bot
    store.save_requirement("99", {"hard": {"rooms": [2]}, "soft": {}, "notes": ""})
    b.handle_update(_msg("/prefs"))
    assert "2-room" in sent[-1][1]


def test_search_sends_matches(bot):
    b, sent, store = bot
    store.save_requirement("99", {"hard": {"rooms": [2], "price_max": 800, "currency": "USD"},
                                  "soft": {"dishwasher": {"want": True, "weight": 2}}, "notes": ""})
    b.handle_update(_msg("/search"))
    joined = "\n".join(t for _, t in sent)
    assert "new match" in joined.lower()
    assert "realt.by/rent/flat-for-long/object/10" in joined     # the listing URL
    # a second search finds nothing new (already notified)
    sent.clear()
    b.handle_update(_msg("/search"))
    assert "no new matches" in sent[-1][1].lower()


def test_edit_inline_shows_diff_and_applies(bot):
    b, sent, store = bot
    store.save_requirement("99", {"hard": {"rooms": [2], "price_max": 600, "currency": "USD"},
                                  "soft": {"dishwasher": {"want": True, "weight": 2}}, "notes": ""})
    b.handle_update(_msg("/edit raise budget to 700, drop dishwasher"))
    assert b._sessions["99"]["state"] == "confirm"
    assert "proposed changes" in sent[-1][1].lower()
    assert "price_max: 600 → 700" in sent[-1][1]
    b.handle_update(_msg("yes"))
    assert "99" not in b._sessions
    assert store.get_requirement("99")["hard"]["price_max"] == 700
    assert "updated" in sent[-1][1].lower()


def test_edit_two_step_then_cancel_keeps_original(bot):
    b, sent, store = bot
    orig = {"hard": {"rooms": [2], "price_max": 600, "currency": "USD"}, "soft": {}, "notes": ""}
    store.save_requirement("99", orig)
    b.handle_update(_msg("/edit"))
    assert b._sessions["99"]["state"] == "editing"
    b.handle_update(_msg("raise budget to 700"))
    assert b._sessions["99"]["state"] == "confirm"
    b.handle_update(_msg("no"))
    assert store.get_requirement("99")["hard"]["price_max"] == 600   # unchanged
    assert "unchanged" in sent[-1][1].lower()


def test_edit_without_requirement_prompts_start(bot):
    b, sent, _ = bot
    b.handle_update(_msg("/edit cheaper"))
    assert "start" in sent[-1][1].lower()


def test_stop_deletes_requirement(bot):
    b, sent, store = bot
    store.save_requirement("99", {"hard": {"rooms": [2]}, "soft": {}, "notes": ""})
    b.handle_update(_msg("/stop"))
    assert store.get_requirement("99") is None
    assert "stopped" in sent[-1][1].lower()
