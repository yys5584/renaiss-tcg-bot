# Renaiss TCG Bot

A Telegram collector game built around public blind card spawns, hidden-price
guesses, and verified Renaiss market references. Exact, fresh, source-backed
cards can show graded reference values from the **Renaiss Index API**; uncertain
matches stay clearly marked as candidates or collection-only cards.

Play the game, learn the market: catch in public blind spawns, build a collection
with experimental reference values, and test your insight by guessing hidden FMV.

## Features

- **Packs** — daily free collector packs; no RP purchases in the default pilot
- **Collection tracker** — owned-card counts, grades, top cards, and recent catches
- **Grading guide** — `/price` shows RAW vs graded premium (e.g. `RAW $45 → PSA 10 $390, 8.7x`)
- **Optional Daily Price Quiz** — opt-in comparison layer; off in the default pilot
- **Blind market spawns** — `c` to enter a random draw, guess the hidden FMV, then compare the room's picks
- **First-c onboarding** — one lifetime, collection-only Welcome Card with no FMV or scored use
- **Daily Market Pick** — choose one verified card in DM, persist its first valid 24h result, and share a privacy-safe public bell
- **Card images** — self-contained Playwright renderer with grader slab labels

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item renaiss_bot\.env.example .env   # then fill in the values
.\.venv\Scripts\python.exe -m renaiss_bot.main
```

## Environment

See [`renaiss_bot/.env.example`](renaiss_bot/.env.example). Key values:

| Var | Purpose |
|-----|---------|
| `RENAISS_BOT_TOKEN` | Telegram bot token |
| `RENAISS_EXPECTED_BOT_ID` | Non-secret numeric `getMe` id for the dedicated Renaiss bot |
| `RENAISS_OFFICIAL_CHAT_ID` | Public Telegram supergroup for `c` spawns and Result Bell |
| `DATABASE_URL` | PostgreSQL (asyncpg) |
| `RENAISS_TELEGRAM_LOCK_DATABASE_URL` | Same PostgreSQL cluster/database as `DATABASE_URL`, through a direct/session-mode endpoint for the single-poller lock; never transaction pooling |
| `RENAISS_QUIZ_CHAT_ID` | Group chat id for the daily quiz (21:00 KST) |
| `RENAISS_API_BASE_URL` | Renaiss Index API base |
| `RENAISS_API_KEY` / `RENAISS_API_SECRET` | Partner API server-side credentials |
| `RENAISS_DAILY_PICK_ENABLED` | Admission flag; off by default and opened only after a live startup probe |
| `RENAISS_TELEGRAM_HEALTH_ENABLED` | Optional loopback-only `/livez` and `/readyz` listener |
| `RENAISS_CLICK_TRACKER_PUBLIC_BASE_URL` | Optional HTTPS click-redirect origin |
| `RENAISS_REFERRAL_URL` | Referral link for card CTAs |

`RENAISS_SKIP_DB=1` is local inspection/mock mode only. A running Telegram or
Discord pilot requires `DATABASE_URL`; production card pools never fall back to
sample cards when PostgreSQL is unavailable.

For the plain `c` group action, disable BotFather privacy mode with `/setprivacy`
or make the bot an administrator, then verify with a real non-admin member's `c`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -q
```

## Product principles

Development follows the Renaiss-specific [game philosophy](renaiss_bot/docs/GAME_PHILOSOPHY.md).
This is an English product: `c` is the canonical one-key catch action, while
Pokémon/TGPoke commands and rules are historical reference only.
Repository-wide AI instructions live in [`AGENTS.md`](AGENTS.md).

## Data disclaimer

Renaiss Index API values are presented as **experimental reference data** (beta),
not verified market facts or investment advice.
