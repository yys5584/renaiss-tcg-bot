# Renaiss TCG Bot

A Telegram collector-game bot where users open free packs of real trading cards
(Pokémon TCG, One Piece, …) and every card carries **real graded-market prices**
(PSA/BGS/CGC values) from the **Renaiss Index API**.

Play the game, learn the market: open packs, build a portfolio with a live USD
value, and compete in a **daily price-guessing quiz**.

## Features

- **Packs** — daily free packs (gated) + RP shop for extras and premium packs
- **Portfolio** — collection value with 7-day change, wallet-style
- **Grading guide** — `/price` shows RAW vs graded premium (e.g. `RAW $45 → PSA 10 $390, 8.7x`)
- **Daily Price Quiz** — guess a card's market value, streaks, Wordle-style answer grid, weekly leaderboard
- **Group drops** — `d` to call a drop, `f` to join, TGPoke-style
- **Card images** — self-contained Playwright renderer with grader slab labels

## Setup

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
cp renaiss_bot/.env.example .env   # then fill in the values
python -m renaiss_bot.main
```

## Environment

See [`renaiss_bot/.env.example`](renaiss_bot/.env.example). Key values:

| Var | Purpose |
|-----|---------|
| `RENAISS_BOT_TOKEN` | Telegram bot token |
| `DATABASE_URL` | PostgreSQL (asyncpg) |
| `RENAISS_QUIZ_CHAT_ID` | Group chat id for the daily quiz (21:00 KST) |
| `RENAISS_API_BASE_URL` | Renaiss Index API base |
| `RENAISS_REFERRAL_URL` | Referral link for card CTAs |

`RENAISS_SKIP_DB=1` runs without a database (mock mode).

## Tests

```bash
python -m pytest tests/ -q
```

## Data disclaimer

Renaiss Index API values are presented as **experimental reference data** (beta),
not verified market facts or investment advice.
