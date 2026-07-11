"""Privacy-safe public copy for Daily Pick community result bells."""

from __future__ import annotations

from html import escape

from renaiss_bot.services.market import _price_source_url_allowed


MIN_PUBLIC_PARTICIPANTS = 6
MIN_PUBLIC_CARD_SUPPORT = 3


def _privacy_limited_bell(cohort: dict, reason: str) -> tuple[str, dict, str]:
    """Keep the public daily rhythm without exposing a small card bucket."""
    pick_date = escape(str(cohort["pick_date"]))
    text = "\n".join(
        [
            "<b>📣 DAILY PICK RESULT BELL</b>",
            f"<code>{pick_date}</code>",
            "",
            "<b>Results settled</b>",
            "Card-level crowd stats stayed private because the public threshold was not met.",
            "Open Daily Market Pick in DM to see your own verified result and choose today.",
            "",
            "No money or positions · experimental reference data · not investment advice.",
        ]
    )
    return text, {}, reason


def build_daily_pick_result_bell(
    cohort: dict,
) -> tuple[str | None, dict, str | None]:
    """Build a Crowd Pick-only pilot bell without exposing user identities."""
    participant_count = int(cohort.get("participant_count") or 0)
    cards = [dict(item) for item in cohort.get("cards") or []]
    metrics = {
        "cohort_pick_date": cohort.get("pick_date"),
        "participant_count": participant_count,
        "cards": [
            {
                "board_card_id": item.get("board_card_id"),
                "card_name": item.get("card_name"),
                "support_count": int(item.get("support_count") or 0),
                "median_move_pct": float(item.get("median_move_pct") or 0),
            }
            for item in cards
        ],
    }
    if participant_count < MIN_PUBLIC_PARTICIPANTS:
        text, _, reason = _privacy_limited_bell(cohort, "fewer_than_6_participants")
        return text, metrics, reason

    public_cards = [
        item
        for item in cards
        if int(item.get("support_count") or 0) >= MIN_PUBLIC_CARD_SUPPORT
    ]
    if not public_cards:
        text, _, reason = _privacy_limited_bell(cohort, "no_card_with_3_supporters")
        return text, metrics, reason

    max_support = max(int(item["support_count"]) for item in public_cards)
    crowd_candidates = [
        item for item in public_cards if int(item["support_count"]) == max_support
    ]
    if len(crowd_candidates) != 1:
        text, _, reason = _privacy_limited_bell(cohort, "crowd_pick_tie")
        return text, metrics, reason

    crowd = crowd_candidates[0]
    support = int(crowd["support_count"])
    share = support / participant_count * 100
    move = float(crowd["median_move_pct"])
    metrics["crowd_pick"] = {
        "board_card_id": crowd.get("board_card_id"),
        "card_name": crowd.get("card_name"),
        "support_count": support,
        "support_share_pct": round(share, 4),
        "median_move_pct": round(move, 4),
    }

    pick_date = escape(str(cohort["pick_date"]))
    card_name = escape(str(crowd.get("card_name") or "Unknown card"))
    grade = escape(str(crowd.get("grade") or ""))
    grade_text = f" · {grade}" if grade else ""
    source = escape(str(crowd.get("price_source") or "Renaiss Index API"))
    confidence_score = crowd.get("min_confidence_score")
    source_count = crowd.get("min_source_count")
    evidence = [f"Source: <code>{source}</code>"]
    if confidence_score is not None:
        evidence.append(f"confidence ≥ {float(confidence_score):.2f}")
    else:
        evidence.append("confidence gate passed")
    if source_count is not None:
        evidence.append(f"evidence ≥ {int(source_count)} sources")
    else:
        evidence.append("source gate passed")
    latest_mark = crowd.get("latest_price_updated_at")
    if latest_mark is not None:
        mark_text = escape(latest_mark.isoformat())
    else:
        mark_text = "verified post-24h mark"
    asset_url = str(crowd.get("asset_url") or "")
    source_link = ""
    if _price_source_url_allowed(asset_url):
        source_link = (
            f' · <a href="{escape(asset_url, quote=True)}">Renaiss card record</a>'
        )
    text = "\n".join(
        [
            "<b>📣 DAILY PICK RESULT BELL</b>",
            f"<code>{pick_date}</code> · {participant_count} settled picks",
            "",
            "<b>👥 Crowd Pick</b>",
            f"{card_name}{grade_text}",
            f"{support} picks ({share:.0f}%) · median 24h reference change "
            f"<b>{move:+.2f}%</b>",
            "",
            " · ".join(evidence),
            f"Latest included mark: <code>{mark_text}</code>{source_link}",
            "Only exact, fresh Renaiss marks that passed confidence and source-count gates are included.",
            "No money or positions · experimental reference data · not investment advice.",
            "Today's pick window is 21:00-24:00 KST. Choose privately with /market.",
        ]
    )
    return text, metrics, None
