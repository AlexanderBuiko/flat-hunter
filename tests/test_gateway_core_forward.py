"""3-tier split: the gateway forwarding to the standalone jarvis llm-core over HTTP.

``JARVIS_LLM_CORE_URL`` turns the in-process jarvis call into an HTTP call to the
llm-core server. httpx is faked, so no socket and no jarvis import is exercised.
"""

import pytest

from flat_hunter.gateway import server as srv


class _Resp:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_core_complete_maps_json_to_a_meter(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _Resp(200, {"text": " hi ", "model": "llama", "prompt_tokens": 7,
                           "completion_tokens": 3, "cost_usd": 0.0001, "latency_ms": 20.0})

    monkeypatch.setattr("httpx.post", fake_post)
    fn = srv._core_complete("http://core:8901/")
    res = fn("sys", "user text", provider="openrouter", model="llama")
    assert captured["url"] == "http://core:8901/v1/complete"
    assert captured["json"] == {"system": "sys", "user": "user text",
                                "provider": "openrouter", "model": "llama"}
    assert res.text == "hi"            # stripped
    assert res.model == "llama"
    assert res.cost_usd == 0.0001


def test_core_complete_raises_on_non_200(monkeypatch):
    monkeypatch.setattr("httpx.post",
                        lambda url, json, timeout: _Resp(502, {"error": "ollama down"}))
    fn = srv._core_complete("http://core:8901")
    with pytest.raises(RuntimeError, match="llm-core error 502"):
        fn("s", "u", provider="ollama", model=None)


def test_gateway_from_env_wires_core_when_url_set(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_CORE_URL", "http://core:8901")
    gw = srv.gateway_from_env()
    assert gw._complete is not srv._default_complete   # forwards to the core, not in-process


def test_gateway_from_env_uses_in_process_when_url_unset(monkeypatch):
    monkeypatch.delenv("JARVIS_LLM_CORE_URL", raising=False)
    gw = srv.gateway_from_env()
    assert gw._complete is srv._default_complete
