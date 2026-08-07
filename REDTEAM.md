# flat-hunter — Red-Team Brief (Day 15)

The pipeline under attack. This is the short "how to run / what to hit / what goes
in and out" note for the pair red-team. For what the app *does*, see
[README.md](README.md).

## What you attack

A **Telegram bot**. It turns a free-text apartment description into a structured
search using an LLM, so the user's message reaches a model — that is the injectable
surface. You talk to it in plain language; there is no web form and no public HTTP
endpoint to poke.

- **Input:** Telegram messages — either a command or free text.
  - `/start` → then describe your ideal flat in one message (this is the AI moment)
  - `/edit <change in words>` → conversational edit of the saved search
  - `/prefs` show search · `/search` check now · `/stop` unsubscribe
  - a `yes` / `no` confirms or rejects a proposed search
- **Output:** the bot's reply text — the understood search, proposed changes, or
  matches. Never a raw secret: the gateway masks anything secret-shaped on the way
  in *and* on the way out.

## Getting access

The bot ignores strangers silently (allow-list). **You attack the live bot over
Telegram — you do not run anything.** All you do:

1. Message `@userinfobot` on Telegram to get your numeric **user id**.
2. Send that id to the defender.

The defender then adds you to the allow-list (their side only — the list is an env
var on their Cloud Run service) and shares the bot handle privately:

```bash
# run by the DEFENDER, not the attacker
gcloud run services update flat-hunter-redteam --region=asia-northeast1 \
  --project=jarvis-mcp-500414 \
  --update-env-vars=FLAT_HUNTER_ALLOWED_USER_IDS=<defender_id>,<attacker_id>
```

## The pipeline (three tiers, one container)

```
[1] Telegram bot        long-poll getUpdates      allow-list + per-user flood cap
      │  (flat_hunter/bot.py)
      ▼
[2] Guarded gateway     POST /v1/complete         inbound guards → prompt → outbound guard → audit
      │  (flat_hunter/gateway/, ingress=internal — not reachable from outside)
      ▼
[3] jarvis llm-core     127.0.0.1:8901            provider call + real cost accounting
      │  (jarvis-cli: jarvis/serve/)
      ▼
   OpenRouter · meta-llama/llama-3.3-70b-instruct
```

Tier [2] is bound to `$PORT` only so the platform health check has something to hit;
it is `ingress=internal`, so you cannot call `/v1/complete` directly — you go through
the bot.

## Where each defense sits (defense in depth)

| Layer | Where | What it stops |
|---|---|---|
| Allow-list | `bot.py` | strangers — silent drop |
| Per-user flood cap | `bot.py` (`FLAT_HUNTER_USER_RATE`/`_WINDOW_S`) | one user draining the budget |
| Per-IP rate limit | `gateway/limiter.py` | request floods |
| Cross-message split | `bot.py` (`_carries_split_secret`) | a secret dribbled across several messages — the bot rejoins a per-chat window and rescans |
| **Inbound guard** | `gateway/guards.py` | secrets (`sk-`/`ghp_`/`AKIA`/card-Luhn/email/phone), **high-entropy tokens**, **base64 decode-and-rescan**, in-message split-secret block — masked before the model |
| Prompt hardening | `ai/requirements.py`, `ai/extract.py` | injection via `<<<USER_DESCRIPTION>>>` / `<<<LISTING_TEXT>>>` delimiters + deterministic `_sanitize_notes` |
| Indirect injection | `ai/extract.py` | payloads in scraped listing text — sanitize + boundary markers + treat a detected injection as a scam signal |
| **Outbound guard** | `gateway/guards.py` | model-echoed secrets, **system-prompt extraction** (prompt-echo), suspicious URLs, shell commands, PII (card/email/phone) |
| Audit | `gateway/audit.py` | every request logged — finding *kind* + masked preview, **never the raw secret** |

## Run it locally (optional — for code inspection, not for attacking)

You do **not** need this to attack: the target is the live bot, and a Telegram bot
allows only one poller per token, so you can't run this exact bot yourself anyway.
This is here only to reproduce behaviour while reading the code.

```bash
# from the flat-hunter checkout (jarvis-cli must be a sibling dir)
pip install -e .[ai]
cp .env.example .env          # bot token + your Telegram id in FLAT_HUNTER_ALLOWED_USER_IDS
python -m flat_hunter bot
```

The gateway and llm-core start automatically inside the container
([docker-entrypoint.sh](docker-entrypoint.sh)); locally the bot talks to jarvis
in-process unless you set `JARVIS_LLM_CORE_URL`.

## Reading the evidence (audit log)

Every attack lands one line in Cloud Logging. This is the defender's proof of what
the guards caught:

```bash
# only the audit lines, last hour, saved to a file
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=flat-hunter-redteam AND textPayload=~"audit outcome="' \
  --project=jarvis-mcp-500414 --freshness=1h \
  --format="value(timestamp,textPayload)" > redteam-evidence.txt
```

Example — a planted email + API key, both caught and masked before the model saw them:

```
audit outcome=completed model=meta-llama/llama-3.3-70b-instruct \
      input=['email', 'openai_key'] output=[] cost_usd=0.00006 tokens=415/58
```

`input=[...]` is what the inbound guard masked; `output=[...]` is what the outbound
guard caught; the raw values never appear in the log.
