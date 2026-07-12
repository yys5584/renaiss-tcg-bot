"""Read-only readiness audit for imported Renaiss catalog price evidence."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.runtime import load_runtime_environment
from renaiss_bot.services.card_pool import catalog_row_to_card, load_card_pool
from renaiss_bot.services.market import market_card_eligibility_issues
from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.price_evidence import (
    catalog_provenance_issue,
    catalog_reference_price,
)


def _normalized(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _variation(card: CardIdentity) -> str:
    metadata = card.metadata or {}
    direct = metadata.get("variation") or metadata.get("variant")
    if direct not in (None, ""):
        return _normalized(direct)
    source_payload = metadata.get("source_payload")
    if isinstance(source_payload, dict):
        return _normalized(source_payload.get("variation") or source_payload.get("variant"))
    return ""


def _structural_identity(card: CardIdentity) -> tuple[str, ...] | None:
    """Return a strong physical-card key; incomplete rows are not guessed."""
    category = _normalized(card.category)
    name = _normalized(card.card_name)
    # Provider set codes are not canonical (for example BS vs base1). Prefer
    # the human set name and use a code only when no name is available.
    set_identity = _normalized(card.set_name) or _normalized(card.set_code)
    collector_number = _normalized(card.collector_number)
    language = _normalized(card.language)
    if not all((category, name, set_identity, collector_number, language)):
        return None
    return (
        category,
        name,
        set_identity,
        collector_number,
        language,
        _variation(card),
    )


def _catalog_id_namespaced(card: CardIdentity) -> bool:
    identifier = str(card.local_card_id or "")
    category = _normalized(card.category)
    return identifier.startswith(
        (f"catalog:{category}:", f"pokard:{category}:")
    )


def audit_cards(cards: list[CardIdentity], *, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    issue_counts: Counter[str] = Counter()
    eligible_examples: list[str] = []
    rejected_examples: list[str] = []
    priced = 0
    eligible = 0
    structural_keys = [_structural_identity(card) for card in cards]
    structural_counts = Counter(key for key in structural_keys if key is not None)
    duplicate_groups = sum(1 for count in structural_counts.values() if count > 1)
    legacy_id_count = sum(not _catalog_id_namespaced(card) for card in cards)
    for card, structural_key in zip(cards, structural_keys):
        price = catalog_reference_price(card)
        if price.fmv_usd is not None and price.fmv_usd > 0:
            priced += 1
        issues = market_card_eligibility_issues(card, price, now=current)
        provenance_issue = catalog_provenance_issue(card)
        if provenance_issue:
            issues.insert(0, provenance_issue)
        if not _catalog_id_namespaced(card):
            issues.insert(0, "active catalog id is not provider-namespaced")
        if structural_key is not None and structural_counts[structural_key] > 1:
            issues.insert(0, "duplicate active structural catalog identity")
        if not issues:
            eligible += 1
            if len(eligible_examples) < 5:
                eligible_examples.append(card.card_name)
            continue
        issue_counts.update(issues)
        if len(rejected_examples) < 5:
            rejected_examples.append(f"{card.card_name}: {issues[0]}")
    return {
        "total": len(cards),
        "priced": priced,
        "eligible": eligible,
        "collection_only": len(cards) - eligible,
        "identity_clean": legacy_id_count == 0 and duplicate_groups == 0,
        "legacy_id_count": legacy_id_count,
        "duplicate_identity_groups": duplicate_groups,
        "issue_counts": dict(issue_counts.most_common()),
        "eligible_examples": eligible_examples,
        "rejected_examples": rejected_examples,
    }


async def load_catalog_cards(*, category: str | None, limit: int) -> list[CardIdentity]:
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                local_card_id, category, card_name, grade, set_code, set_name,
                collector_number, rarity, language, image_url,
                market_price_usd, metadata
            FROM renaiss_catalog_cards
            WHERE is_active = TRUE
              AND ($1::text IS NULL OR category = $1)
            ORDER BY category, card_name, local_card_id
            LIMIT $2
            """,
            category,
            max(1, min(50_000, limit)),
        )
    return [catalog_row_to_card(dict(row)) for row in rows]


async def load_catalog_identity_summary(*, category: str | None) -> dict[str, int]:
    """Check every active DB row server-side, independent of the report sample limit."""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH active AS (
                SELECT
                    local_card_id,
                    lower(btrim(category)) AS category,
                    regexp_replace(lower(btrim(card_name)), '\\s+', ' ', 'g') AS card_name,
                    regexp_replace(
                        lower(COALESCE(NULLIF(btrim(set_name), ''), btrim(set_code))),
                        '\\s+', ' ', 'g'
                    ) AS set_identity,
                    regexp_replace(lower(btrim(collector_number)), '\\s+', ' ', 'g')
                        AS collector_number,
                    regexp_replace(lower(btrim(language)), '\\s+', ' ', 'g') AS language,
                    regexp_replace(
                        lower(btrim(COALESCE(
                            metadata->>'variation',
                            metadata->>'variant',
                            metadata->'source_payload'->>'variation',
                            metadata->'source_payload'->>'variant',
                            ''
                        ))),
                        '\\s+', ' ', 'g'
                    ) AS variation
                FROM renaiss_catalog_cards
                WHERE is_active = TRUE
                  AND ($1::text IS NULL OR category = $1)
            ), duplicate_groups AS (
                SELECT 1
                FROM active
                WHERE category <> '' AND card_name <> '' AND set_identity <> ''
                  AND collector_number <> '' AND language <> ''
                GROUP BY category, card_name, set_identity, collector_number,
                         language, variation
                HAVING COUNT(*) > 1
            )
            SELECT
                (SELECT COUNT(*)::int FROM active
                 WHERE left(local_card_id, length('catalog:' || category || ':'))
                           <> 'catalog:' || category || ':'
                   AND left(local_card_id, length('pokard:' || category || ':'))
                           <> 'pokard:' || category || ':') AS legacy_id_count,
                (SELECT COUNT(*)::int FROM duplicate_groups) AS duplicate_identity_groups
            """,
            category,
        )
    return {
        "legacy_id_count": int(row["legacy_id_count"] if row else 0),
        "duplicate_identity_groups": int(
            row["duplicate_identity_groups"] if row else 0
        ),
    }


def print_report(
    report: dict,
    *,
    category: str | None,
    resolved_source: str = "catalog-table",
) -> None:
    print(
        f"Renaiss price-evidence audit | category={category or 'all'} "
        f"| resolved_source={resolved_source}"
    )
    print(
        f"- total: {report['total']} | priced: {report['priced']} | "
        f"verified eligible: {report['eligible']} | collection-only: {report['collection_only']}"
    )
    print(
        f"- identity gate: {'clean' if report['identity_clean'] else 'FAIL'} | "
        f"legacy ids: {report['legacy_id_count']} | "
        f"duplicate structural groups: {report['duplicate_identity_groups']}"
    )
    print("\nRejection reasons")
    if not report["issue_counts"]:
        print("- none")
    for reason, count in report["issue_counts"].items():
        print(f"- {reason}: {count}")
    if report["eligible_examples"]:
        print("\nEligible examples")
        for name in report["eligible_examples"]:
            print(f"- {name}")
    if report["rejected_examples"]:
        print("\nRejected examples")
        for example in report["rejected_examples"]:
            print(f"- {example}")


async def execute(
    *,
    category: str | None,
    limit: int,
    require_eligible: int,
    pool_source: str,
    require_total: int = 0,
    require_resolved_source: str | None = None,
) -> int:
    exit_code = 1
    try:
        if pool_source == "actual":
            if not category:
                raise ValueError("--category is required with --pool-source actual")
            cards, resolved_source = await load_card_pool(None, category)
        else:
            cards = await load_catalog_cards(category=category, limit=limit)
            resolved_source = "renaiss_catalog_cards"
        report = audit_cards(cards)
        if resolved_source in {"renaiss_catalog", "renaiss_catalog_cards"}:
            identity_summary = await load_catalog_identity_summary(category=category)
            report["legacy_id_count"] = identity_summary["legacy_id_count"]
            report["duplicate_identity_groups"] = identity_summary[
                "duplicate_identity_groups"
            ]
            report["identity_clean"] = not any(identity_summary.values())
        print_report(report, category=category, resolved_source=resolved_source)
        if (
            require_resolved_source is not None
            and resolved_source != require_resolved_source
        ):
            print(
                "\nFAIL: runtime card pool source "
                f"{resolved_source!r} != required {require_resolved_source!r}"
            )
        elif report["total"] < max(0, require_total):
            print(
                f"\nFAIL: runtime card pool total {report['total']} "
                f"< required {max(0, require_total)}"
            )
        elif not report["identity_clean"]:
            print(
                "\nFAIL: active catalog identity is unsafe; deactivate legacy/duplicate "
                "rows only after a read-only DB review"
            )
        elif report["eligible"] < max(0, require_eligible):
            print(
                f"\nFAIL: verified eligible cards {report['eligible']} "
                f"< required {max(0, require_eligible)}"
            )
        else:
            exit_code = 0
    except Exception as exc:
        print(f"Catalog audit failed (error={type(exc).__name__}).")
    finally:
        try:
            await close_db()
        except Exception as exc:
            print(f"Catalog audit DB cleanup failed (error={type(exc).__name__}).")
            exit_code = 1
    return exit_code


def main() -> None:
    load_runtime_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", default=None)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--require-eligible", type=int, default=0)
    parser.add_argument("--require-total", type=int, default=0)
    parser.add_argument(
        "--require-resolved-source",
        default=None,
        help="fail unless the runtime pool resolves to this exact source label",
    )
    parser.add_argument(
        "--pool-source",
        choices=("actual", "catalog"),
        default="actual",
        help="actual follows runtime fallback; catalog reads only renaiss_catalog_cards",
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            execute(
                category=args.category,
                limit=args.limit,
                require_eligible=args.require_eligible,
                pool_source=args.pool_source,
                require_total=args.require_total,
                require_resolved_source=args.require_resolved_source,
            )
        )
    )


if __name__ == "__main__":
    main()
