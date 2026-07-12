# Renaiss TCG Bot

A Telegram collector game built around public blind card spawns, hidden-price
guesses, and verified Renaiss market references. Exact, fresh, source-backed
cards can show graded reference values from the **Renaiss Index API**; uncertain
matches stay clearly marked as candidates or collection-only cards.

Play the game, learn the market: catch in public blind spawns, build a collection
with experimental reference values, and test your insight by guessing hidden FMV.

## Features

- **Blind market spawns** — `c` to enter a random draw, guess the hidden FMV, then compare the room's picks
- **Collection tracker** — owned-card counts, grades, top cards, and recent catches
- **First-c onboarding** — one lifetime, collection-only Welcome Card with no FMV or scored use
- **Grading guide** — `/price` shows RAW vs graded premium (e.g. `RAW $45 → PSA 10 $390, 8.7x`)
- **Card images** — cached fixed-frame Pillow compositor with Telegram `file_id` reuse
- **Optional Daily Price Quiz** — opt-in comparison layer; off in the default pilot
- **Gated Daily Market Pick** — disabled until the Partner API and PostgreSQL live preflight pass
- **Gated private pack experiment** — disabled by default and separate from the public `c` core loop

## Web companion ownership

This repository also owns the complete `tgpoke.com/renaiss` collaboration companion:
UI, static assets, read-only collection API, and Telegram OIDC login. The TGPoke
repository keeps only navigation links; Cloudflare Tunnel routes the `/renaiss`
path to this standalone service without stripping the prefix. See
[`renaiss_bot/web/README.md`](renaiss_bot/web/README.md) for the minimal web-only
environment, database role, health gate, and ingress handoff.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
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
| `RENAISS_WEB_SESSION_SECRET` | Independent 32+ character secret for `/renaiss` web sessions |
| `RENAISS_TELEGRAM_OIDC_CLIENT_ID` | BotFather Web Login OIDC client id for `tgpoke.com/renaiss` |
| `RENAISS_TELEGRAM_OIDC_CLIENT_SECRET` | BotFather Web Login OIDC client secret |
| `RENAISS_TELEGRAM_OIDC_REDIRECT_URI` | Exact registered HTTPS callback under `/renaiss/api/auth/telegram/callback` |
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
