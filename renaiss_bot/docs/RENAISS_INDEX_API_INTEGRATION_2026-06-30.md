# Renaiss Index API Integration

## Source

- API docs: `https://index.renaissos.com/api-docs`
- API base: `https://api.renaissos.com`
- Search endpoint: `GET /v1/search?q={query}&limit={limit}`
- CLI smoke command: `npx renaiss`

## Current Bot Mapping

The Renaiss bot uses `GET /v1/search` for pack result FMV and `/price` lookup.

Mapped fields:

- `priceUsdCents` -> `RenaissPrice.fmv_usd`
- `deltaPct` -> `RenaissPrice.change_7d_pct`
- `href` -> `RenaissPrice.asset_url`
- `imageUrlLg` / `imageUrl` / `imageUrlThumb` -> `RenaissPrice.image_url`
- `gradeLabel` -> `RenaissPrice.grade_label`
- `company` -> `RenaissPrice.grading_company`
- `confidence` -> `RenaissPrice.confidence`
- `lastSaleAt` / `updatedAt` -> `RenaissPrice.price_updated_at`

Top image label:

- left: official Renaiss logo
- right: joined grader/price slab label, e.g. `PSA 10 | $390.85`
- PSA uses an ivory label with deep red frame; BGS/CGC use grader-specific chip colors
- `deltaPct` and `confidence` stay in chat text, not in the image label

Grader identity colors use the same ivory label base. The grader signal comes from frame and text color:

- PSA: deep red text and frame
- BGS Black / Pristine: bronze-gold text and gold frame
- BGS Gold / 9.5: dark bronze text and bronze frame
- CGC Pristine / Perfect: teal text and teal frame
- CGC Gem Mint: teal text and teal frame
- RAW / ungraded: brown-gold text and frame

Image priority:

1. Renaiss Index image URL from the matched API result
2. Local TGPOKE/DB card image URL
3. TGPOKE renderer fallback

Button behavior:

- User-facing buttons use the configured referral URL:
  `https://www.renaiss.xyz/ref/moonyu`

## Beta Data Notice

Renaiss CLI and Renaiss Index API are beta builder tools. FMV, index, trade, listing, and price-history data may be incomplete, delayed, missing, or still updating. The bot presents Index API values as experimental reference data, not final verified market facts.

Final hackathon submission should explicitly mention:

- Data source: Renaiss Index API
- Assumption: FMV is used as an experimental collector-market reference
- Limitation: beta data may be incomplete or delayed
