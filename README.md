# flat-hunter

An **AI apartment watcher** for [realt.by](https://realt.by/rent/flat-for-long/). It
polls the long-term rental listings on an interval, keeps the ones matching your
requirements, and notifies you via a Telegram bot about new ads — with the *judgment*
parts (understanding free-text descriptions, ranking against fuzzy preferences, spotting
scams, de-duplicating relists) done by an LLM.

> Day-35 assignment ("a real task you want to automate"). **Task solved:** stop manually
> refreshing a rental site — collect matching flats automatically and get told, in plain
> language, *why* each new one fits me.

## Where the AI actually participates

The scraper + hard filters (price, rooms, floor, district) are plain automation. The LLM
does the parts a person would otherwise do by hand:

| Step | AI role |
|---|---|
| **Extract** (`ai/extract.py`) | free-text `headline`/`title` → structured features (`dishwasher`, `pets_allowed`, `quiet`, …) + scam signals. realt's own fields are opaque enum codes, so the prose is where the nuance lives. |
| **Rank** (P3) | score survivors against weighted soft prefs, with a one-line rationale |
| **Notify** (P3) | a tailored "why this fits you" + red-flag/price warnings |
| **Dedup / pricing** (P5) | embeddings detect the same flat relisted; RAG compares price to recent comparables |
| **Register / edit** (P4/P6) | natural-language "describe your ideal flat" → requirement schema; conversational edits |

Reuses [jarvis-cli](https://github.com/AlexanderBuiko/jarvis-cli)'s core (`LLMGateway`,
`indexing`) through a thin adapter (`flat_hunter/adapter/`), so a future `jarvis-core`
extraction touches only that layer. Extraction defaults to **local Ollama** (cheap at
"every 3h × many ads"); each listing is LLM'd once and cached.

## Data source & politeness

realt.by is a Next.js app; each listing page embeds all ads as JSON in `__NEXT_DATA__`.
We parse that from the **allowed** HTML pages (and `?page=2..10`, which `robots.txt`
explicitly allows) — we do **not** touch `/_next/data/...`, which robots disallows under
`/_*`. Personal use, one polite request per few hours. Not for redistribution.

## Status — phased build

- **P1 (done): automation spine** — scrape/parse `__NEXT_DATA__` → SQLite → hard filter.
- **P2 (done): AI extraction** — free text → structured features + scam signals
  (`ai/extract.py`); soft ranking + critical gating (`matching`, `pipeline`).
- **P4 (done): Telegram bot** — registration by *describing* your flat (NL → schema,
  `ai/requirements.py`), `/prefs` `/search` `/stop`, and interval notifications
  (`bot.py`, `scheduler.py`).
- **P5–P6 (next)**: embeddings dedup/relist + price-vs-comparables RAG; conversational
  requirement edits.

## Run it

```bash
pip install -e .[ai]                  # core + jarvis (LLM/RAG); or `-e .` for the P1 spine
cp .env.example .env                  # add your bot token + Telegram user id

# offline demo (no network/model):
python -m flat_hunter scrape --fixture tests/fixtures/listings.json
# live hunt with AI ranking (one polite page):
python -m flat_hunter scrape --pages 1 --req my_requirement.json --provider openrouter
# the actual product — Telegram bot + 3h scheduler:
python -m flat_hunter bot
pytest -q
```

In Telegram: `/start` → describe your ideal flat in one message → the LLM builds your
search → confirm → `/search` to check now, and you'll be pinged about new matches.

`my_requirement.json` (hard = code-filtered, soft = LLM-judged in P3):

```json
{
  "hard": { "price_max": 700, "currency": "USD", "rooms": [1,2],
            "floor_min": 2, "floor_not_top": true, "districts": ["Центральный"] },
  "soft": { "pets_allowed": {"want": true, "weight": 5, "critical": true},
            "renovated":    {"want": true, "weight": 3},
            "dishwasher":   {"want": true, "weight": 2} },
  "notes": "quiet, good for remote work, no basement"
}
```
