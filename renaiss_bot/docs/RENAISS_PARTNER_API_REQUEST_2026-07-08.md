# Partner API Access Request — for Renaiss

**From:** moonyu (Renaiss Index API builder — Telegram TCG collector game)
**Date:** 2026-07-08
**Send to:** Renaiss Index API / partnerships contact

---

## Short version

I'm building a Telegram game on top of the **Renaiss Index API**, and I've hit the public rate limit (**10 requests/day per IP**). To build the card catalog and the live pricing the game needs, I'd like **partner API access** — `X-Api-Key` + `X-Api-Secret` (10,000/day tier).

---

## What I'm building

A Telegram collector market-literacy game where users:
- Collect digital TCG cards (Pokémon, etc.) with clearly labeled Renaiss reference data when an exact match is available
- Make one verified **Daily Market Pick** and compare it with the next valid Index mark (no trading or cash balance)
- Join public blind catches, guess a hidden reference band, and discuss the reveal

Every card, price, and slab label in the game is powered by the Renaiss Index. The bot is effectively a live consumer showcase of your data — referral-linked back to Renaiss on every card.

## What I need

1. **Partner credentials** — `X-Api-Key` + `X-Api-Secret` (server-side only, I understand the secret is shown once). Public tier's 10/day per IP can't support building a catalog of thousands of cards.
2. **Confirmation I can use `GET /v1/index/item-by-no`** (lookup by set + item number + variation + language). I want to match cards precisely by structure instead of fuzzy name search — this avoids same-name-different-set mismatches.
3. **The partner-tier data fields** — numeric confidence scores + source counts (the docs note these are partner-only). Reliable confidence is essential for deciding which marks are safe to use in scored predictions.

## Why this is a fair ask

- The bot drives **organic exposure + referral traffic** to Renaiss on every price reveal.
- It's a **live builder case study** for the Index API — daily engagement generated from your data.
- Ties into the daily-quiz **sponsorship** we already discussed (see `RENAISS_QUIZ_SPONSORSHIP_PROPOSAL`).

## Technical notes (for whoever provisions this)

- Requests would be **server-side** from a single VM (Oracle Cloud, fixed IP) — happy to share the IP for allowlisting if that's simpler than key+secret.
- Usage pattern: an initial **bulk catalog build** (a few thousand `item-by-no` lookups, rate-limited politely), then **daily incremental price refreshes**.
- I'll respect rate limits and cache aggressively.

---

*Happy to hop on a quick call or share the bot live. The remaining dependency for the verified prediction pilot is exact, source-backed API access; the game has no buying, selling, cash balance, or user-to-user trading.*
