"""AI extraction tests — the LLM call is mocked, so no model/network is needed."""

import json

from flat_hunter.ai import extract
from flat_hunter.models import Listing


def _listing(text="Уютная квартира, есть посудомоечная машина и кондиционер."):
    return Listing(code=1, title=text)


def test_extract_parses_and_coerces(monkeypatch):
    payload = {"dishwasher": True, "air_conditioning": True, "pets_allowed": False,
               "scam_risk": "low", "red_flags": [], "summary": "Cozy flat with a dishwasher."}
    monkeypatch.setattr(extract.llm, "complete", lambda *a, **k: json.dumps(payload))
    feats = extract.extract_features(_listing())
    assert feats["dishwasher"] is True
    assert feats["air_conditioning"] is True
    assert feats["pets_allowed"] is False
    assert feats["quiet"] is None                 # unmentioned key defaults to null
    assert feats["scam_risk"] == "low"
    assert feats["summary"].startswith("Cozy")


def test_extract_tolerates_fenced_json(monkeypatch):
    monkeypatch.setattr(extract.llm, "complete",
                        lambda *a, **k: '```json\n{"dishwasher": true}\n```')
    feats = extract.extract_features(_listing())
    assert feats["dishwasher"] is True


def test_extract_bad_json_degrades_to_nulls(monkeypatch):
    monkeypatch.setattr(extract.llm, "complete", lambda *a, **k: "sorry, I can't")
    feats = extract.extract_features(_listing())
    assert all(feats[k] is None for k in extract.FEATURE_KEYS)
    assert feats["scam_risk"] == "low"


def test_empty_text_skips_the_llm(monkeypatch):
    called = {"n": 0}
    def _spy(*a, **k):
        called["n"] += 1
        return "{}"
    monkeypatch.setattr(extract.llm, "complete", _spy)
    feats = extract.extract_features(Listing(code=2))   # no text at all
    assert called["n"] == 0                              # never hit the model
    assert feats["dishwasher"] is None
