"""NL → requirement schema (LLM mocked)."""

import json

from flat_hunter.ai import requirements as R


def test_build_requirement_coerces_known_fields(monkeypatch):
    payload = {
        "hard": {"price_max": 600, "currency": "USD", "rooms": [1, 2],
                 "floor_not_top": True, "bogus_field": "ignored"},
        "soft": {"pets_allowed": {"want": True, "weight": 9, "critical": True},
                 "unknown_feature": {"want": True, "weight": 2}},
        "notes": "quiet",
    }
    monkeypatch.setattr(R.llm, "complete", lambda *a, **k: json.dumps(payload))
    req = R.build_requirement("2 rooms under 600, not top floor, must allow pets, quiet")
    assert req["hard"]["price_max"] == 600
    assert "bogus_field" not in req["hard"]                 # unknown hard key dropped
    assert req["soft"]["pets_allowed"]["weight"] == 5       # clamped 1..5
    assert req["soft"]["pets_allowed"]["critical"] is True
    assert "unknown_feature" not in req["soft"]             # not in the vocabulary
    assert req["notes"] == "quiet"


def test_build_requirement_bad_json_is_empty(monkeypatch):
    monkeypatch.setattr(R.llm, "complete", lambda *a, **k: "no idea")
    req = R.build_requirement("something")
    assert req == {"hard": {}, "soft": {}, "notes": ""}


def test_empty_description_skips_llm(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(R.llm, "complete", lambda *a, **k: called.__setitem__("n", 1) or "{}")
    assert R.build_requirement("   ")["hard"] == {}
    assert called["n"] == 0


def test_summarize_is_readable():
    req = {"hard": {"rooms": [2], "price_max": 600, "currency": "USD", "floor_not_top": True},
           "soft": {"pets_allowed": {"want": True, "weight": 5, "critical": True}},
           "notes": "quiet"}
    s = R.summarize_requirement(req)
    assert "2-room" in s and "600" in s and "not top floor" in s
    assert "pets_allowed!" in s        # critical marked with !
    assert "quiet" in s
