# LLM Gateway — a guarded proxy in front of every model call

Week-10 (Security), Day 13. A small HTTP service that sits between a caller and
the LLM provider and runs every cross-cutting security concern in one place:
input guard (secret detection + masking), output guard (leaked secrets / prompt
echo / suspicious URLs & commands), per-IP rate limiting, cost tracking and an
append-only audit log. This is the lecture's "LLM Gateway as a single chokepoint"
made concrete for flat-hunter.

Code: [`flat_hunter/gateway/`](../../flat_hunter/gateway/). Run it with
`python -m flat_hunter.gateway`. Tests:
[`tests/test_gateway_guards.py`](../../tests/test_gateway_guards.py),
[`tests/test_gateway_server.py`](../../tests/test_gateway_server.py).

## The app

**flat-hunter** is the realt.by rental bot. Everything above the model talks to
one seam, `adapter.llm` — never `jarvis.*` directly. The gateway slots in at that
seam: it is an HTTP front door that guards a request, forwards it through
`adapter.llm.complete_metered`, guards the reply, and logs the cost. It forwards
to **OpenRouter** (cloud) or **Ollama** (local) — not OpenAI, which is blocked
for this operator.

## The risks it addresses

| Risk | Where it bites flat-hunter | Guard |
|---|---|---|
| A user pastes a secret (API key, card, phone, email) into a prompt | anything a user types reaches the model and the provider's logs | **input guard** — mask or block before forwarding |
| The model emits a secret / echoes its system prompt / returns a payment URL or shell command | replies are shown to the user; a hallucinated key or a `curl … \| sh` is dangerous | **output guard** — redact or block |
| One caller drives unbounded cost (the Day-5 partner hits the exposed bot) | the bot will be network-reachable for the pair red-team | **rate limiter** — N requests/min per IP |
| No record of what was intercepted or spent | you cannot audit an attack you did not log | **audit log + cost tracking** |

## Request flow

```
POST /v1/complete
   → rate limit (per IP, fixed window)
   → input guard  (scan_input: mask | block)
   → forward       (adapter.llm.complete_metered → OpenRouter/Ollama)
   → output guard (scan_output: redact | block)
   → audit + cost (one JSONL line, running totals)
```

The whole pipeline lives in `Gateway.handle_complete(client, body) -> (status, json)`,
which touches no socket — so it is unit-tested without binding a port. The HTTP
layer (`_Handler`, `build_server`) is a thin translation shell.

## Input Guard

Detects, in priority order, then **masks** (default) or **blocks**:

| Kind | Pattern / method | Placeholder |
|---|---|---|
| OpenAI key | `sk-` / `sk-proj-` prefix | `[REDACTED_API_KEY]` |
| GitHub token | `ghp_` / `gho_` / `ghu_` / `ghs_` / `ghr_` | `[REDACTED_API_KEY]` |
| AWS access key | `AKIA…` / `ASIA…` + 16 | `[REDACTED_API_KEY]` |
| Credit card | 13–19 digits **passing a Luhn check** (cuts false positives) | `[REDACTED_CARD]` |
| Email | standard address form | `[REDACTED_EMAIL]` |
| Phone | international / grouped digit runs | `[REDACTED_PHONE]` |
| Base64-encoded secret | decode the blob, re-scan, flag if a secret appears | `[REDACTED_ENCODED_SECRET]` |
| Split secret (`"sk-" + "proj-…"`) | glue quoted fragments, re-scan | **block** (cannot mask a span that does not exist yet) |

Masking is the default because it keeps the app usable — the (now-safe) prompt
still goes through. `mode="block"` refuses outright; `mode="off"` disables the
guard. A finding's log preview is always masked, so an audit line never carries
the raw secret.

## Output Guard

Runs on the model's reply before the user sees it:

- **Model-emitted secrets** — same detectors as the input guard (models
  occasionally hallucinate real-looking keys) → **redact**.
- **System-prompt echo** — a reply that repeats a ≥40-char sentence of the system
  prompt verbatim → **block**.
- **Suspicious URLs** — any `http(s)://…` in a reply → **redact**.
- **Shell commands** — `rm -rf`, `curl`, `wget`, `| sh`, `bash -c`, `sudo`,
  `X-Backdoor`, `powershell`, `nc -e` → **block**.

Secrets and URLs are redacted (the rest of the reply is still useful); a prompt
leak or an embedded command is higher severity and blocks the whole reply.

## Test results — 17 guard cases (brief asks for ≥10)

All offline; the model and socket are faked. **Caught** = guard fired as intended;
**miss** = documented limitation.

| # | Case | Result |
|---|---|---|
| 1 | OpenAI key `sk-proj-…` | ✅ masked |
| 2 | GitHub token `ghp_…` | ✅ masked |
| 3 | AWS key `AKIA…` | ✅ masked |
| 4 | Credit card (valid Luhn) | ✅ masked |
| 5 | 16 random digits (fails Luhn) | ✅ **not** flagged (no false positive) |
| 6 | Email address | ✅ masked |
| 7 | Phone number | ✅ masked |
| 8 | Base64-encoded key | ✅ decoded & masked |
| 9 | Split secret `"sk-" + "proj-…"` | ✅ detected → **blocked** |
| 10 | Clean prompt | ✅ passes through untouched |
| 11 | `mode="block"` refuses instead of masking | ✅ |
| 12 | Log preview never holds the raw secret | ✅ |
| 13 | **Miss:** bare 40-char hex, no prefix | ⚠️ **not** caught — documented below |
| 14 | Output: model-emitted key | ✅ redacted |
| 15 | Output: system-prompt echo | ✅ blocked |
| 16 | Output: embedded `curl … \| sh` | ✅ blocked |
| 17 | Output: suspicious URL | ✅ redacted |

Plus the server pipeline (`test_gateway_server.py`): masked-before-forward,
blocked-input-never-calls-provider, unsafe-output-blocked, provider-error→502,
rate-limit→429, per-client budgets, and audit-stores-kind-not-secret. **86 tests
pass** (30 new + 56 pre-existing).

### What we miss, honestly

- **A high-entropy secret with no known prefix or format** (e.g. a bare 40-char
  hex token) is not caught — detection is prefix/format-based. An entropy
  heuristic would catch more but also raise false positives on IDs and hashes;
  out of scope for today.
- **A secret split across *separate requests*** (`sk-` in one call, `proj-…` in
  the next) is not caught — this proxy is stateless per request. Only same-prompt
  fragment-splitting is detected.

## Rate limiting

In-memory fixed-window counter, per client IP (honouring `X-Forwarded-For`'s
first hop behind a proxy). Default 20/min, set via `FLAT_HUNTER_GATEWAY_RATE`.
Over the cap → HTTP 429 with `retry_after`. No Redis: a single-process proxy does
not need a distributed limiter, and a real deployment puts one at the load
balancer anyway. Verified live: `[200, 200, 429, 429]` at cap 3.

## Cost tracking

Real numbers, never estimated. Both jarvis clients return the provider's `usage`
block, and `LLMGateway.record` computes dollar cost from cached pricing
(Ollama = $0). Each audit line carries `prompt_tokens`, `completion_tokens`,
`cost_usd`; `GET /stats` returns running totals.

## Logs of intercepted secrets (real runs)

Local `qwen2.5:7b`, a prompt leaking an OpenAI key — masked before the model,
which never saw or echoed it:

```json
{"ts": "2026-08-05T13:37:49Z", "client": "7.7.7.7", "provider": "ollama",
 "model": "qwen2.5:7b", "outcome": "completed",
 "input_findings": [{"kind": "openai_key", "preview": "sk-p… (23 chars)"}],
 "output_findings": [], "prompt_tokens": 59, "completion_tokens": 16,
 "cost_usd": 0.0, "latency_ms": 3713.0}
```

A prompt leaking a key **and** a card, and a rate-limited hit:

```json
{"outcome": "completed",
 "input_findings": [{"kind": "openai_key", "preview": "sk-p… (25 chars)"},
                    {"kind": "card", "preview": "card (16 digits)"}], "...": "..."}
{"outcome": "rate_limited", "provider": "-", "model": null, "cost_usd": null}
```

The preview identifies the *kind* and keeps a key's public prefix; the secret
value never reaches the log.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `FLAT_HUNTER_GATEWAY_HOST` / `_PORT` | `127.0.0.1` / `8900` | bind address (localhost by default) |
| `FLAT_HUNTER_GATEWAY_RATE` | `20` | requests per minute per IP |
| `FLAT_HUNTER_GATEWAY_INPUT_MODE` | `mask` | `mask` \| `block` \| `off` |
| `FLAT_HUNTER_GATEWAY_LOG` | `~/.flat-hunter/gateway-audit.jsonl` | audit log path |
| `JARVIS_LLM_PROVIDER` | `ollama` | forwarding provider (`ollama` / `openrouter`) |

## Deferred (not today)

Wiring flat-hunter's bot adapter to route through the proxy, and deploying it, are
separate steps — kept out of this task on purpose. The pieces they need
(`complete_metered`, env-driven config, `/healthz`) are already in place.
