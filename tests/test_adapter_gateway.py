"""Adapter → gateway routing: when FLAT_HUNTER_GATEWAY_URL is set, complete() proxies.

httpx is faked, so no server or model is needed — we only check the routing decision
and how a guard block degrades.
"""

from types import SimpleNamespace

from flat_hunter.adapter import llm


def _fake_httpx(monkeypatch, status, body):
    sent = {}
    def _post(url, json=None, timeout=None):
        sent["url"] = url
        sent["json"] = json
        return SimpleNamespace(status_code=status, json=lambda: body)
    monkeypatch.setattr(llm, "httpx", SimpleNamespace(post=_post), raising=False)
    # llm imports httpx *inside* the function, so patch the module it imports from.
    import sys
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=_post))
    return sent


def test_no_gateway_url_falls_through_to_in_process(monkeypatch):
    monkeypatch.delenv("FLAT_HUNTER_GATEWAY_URL", raising=False)
    assert llm._gateway_complete("sys", "user", provider="ollama", model=None) is None


def test_gateway_url_routes_and_returns_text(monkeypatch):
    monkeypatch.setenv("FLAT_HUNTER_GATEWAY_URL", "http://127.0.0.1:8900")
    sent = _fake_httpx(monkeypatch, 200, {"text": "Found 3 flats.", "input_action": "allow"})
    out = llm.complete("you are a bot", "find me a flat", provider="ollama")
    assert out == "Found 3 flats."
    assert sent["url"].endswith("/v1/complete")
    assert sent["json"]["user"] == "find me a flat"


def test_input_guard_block_degrades_to_empty(monkeypatch):
    monkeypatch.setenv("FLAT_HUNTER_GATEWAY_URL", "http://127.0.0.1:8900")
    _fake_httpx(monkeypatch, 400, {"error": "blocked by input guard", "findings": ["split_secret"]})
    assert llm.complete("sys", 'k="sk-"+"proj-x"', provider="ollama") == ""


def test_output_guard_block_degrades_to_empty(monkeypatch):
    monkeypatch.setenv("FLAT_HUNTER_GATEWAY_URL", "http://127.0.0.1:8900")
    _fake_httpx(monkeypatch, 200, {"blocked": True, "text": "", "reason": "unsafe output"})
    assert llm.complete("sys", "how do I fix it", provider="ollama") == ""


def test_transport_error_raises(monkeypatch):
    monkeypatch.setenv("FLAT_HUNTER_GATEWAY_URL", "http://127.0.0.1:8900")
    _fake_httpx(monkeypatch, 502, {"error": "provider call failed"})
    try:
        llm.complete("sys", "hi", provider="ollama")
    except RuntimeError as exc:
        assert "gateway error 502" in str(exc)
    else:
        raise AssertionError("expected RuntimeError on a 502")
