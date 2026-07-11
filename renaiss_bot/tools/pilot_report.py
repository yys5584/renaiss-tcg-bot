"""Read-only KPI report for the Renaiss collector-market pilot."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.runtime import load_runtime_environment


async def load_report(days: int) -> dict:
    pool = await get_db()
    async with pool.acquire() as conn:
        events = await conn.fetch(
            """
            SELECT event_name, COUNT(*)::int AS events,
                   COUNT(DISTINCT user_id)::int AS users
            FROM renaiss_events
            WHERE created_at >= now() - ($1::int * interval '1 day')
            GROUP BY event_name
            ORDER BY event_name
            """,
            days,
        )
        clicks = await conn.fetch(
            """
            SELECT source, COUNT(*)::int AS clicks,
                   COUNT(DISTINCT user_id)::int AS unique_users
            FROM renaiss_referral_clicks
            WHERE clicked_at >= now() - ($1::int * interval '1 day')
            GROUP BY source
            ORDER BY clicks DESC, source
            """,
            days,
        )
        retention = await conn.fetchrow(
            """
            WITH first_seen AS (
                SELECT user_id,
                       MIN((created_at AT TIME ZONE 'Asia/Seoul')::date) AS cohort_date
                FROM renaiss_events
                WHERE user_id IS NOT NULL
                GROUP BY user_id
            ), activity AS (
                SELECT DISTINCT user_id,
                       (created_at AT TIME ZONE 'Asia/Seoul')::date AS active_date
                FROM renaiss_events
                WHERE user_id IS NOT NULL
            )
            SELECT
                COUNT(*) FILTER (WHERE cohort_date >=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - $1::int
                    AND cohort_date <=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - 1)::int AS d1_matured,
                COUNT(*) FILTER (WHERE cohort_date >=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - $1::int
                    AND cohort_date <=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - 1
                    AND EXISTS (
                        SELECT 1 FROM activity a
                        WHERE a.user_id = f.user_id
                          AND a.active_date = f.cohort_date + 1
                    ))::int AS d1_returned,
                COUNT(*) FILTER (WHERE cohort_date >=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - $1::int
                    AND cohort_date <=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - 7)::int AS d7_matured,
                COUNT(*) FILTER (WHERE cohort_date >=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - $1::int
                    AND cohort_date <=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - 7
                    AND EXISTS (
                        SELECT 1 FROM activity a
                        WHERE a.user_id = f.user_id
                          AND a.active_date = f.cohort_date + 7
                    ))::int AS d7_returned
            FROM first_seen f
            """,
            days,
        )
        retention_cohorts = await conn.fetch(
            """
            WITH first_c AS (
                SELECT DISTINCT ON (entered.user_id)
                       entered.user_id,
                       entered.created_at AS cohort_at,
                       (entered.created_at AT TIME ZONE 'Asia/Seoul')::date AS cohort_date,
                       posted.metadata->>'assignment_id' AS assignment_id,
                       posted.metadata->>'variant' AS variant
                FROM renaiss_events entered
                JOIN renaiss_events posted
                  ON posted.session_id = entered.session_id
                 AND posted.event_name = 'spawn_posted'
                WHERE entered.event_name = 'first_c_entered'
                  AND entered.user_id IS NOT NULL
                  AND entered.created_at >= now() - ($1::int * interval '1 day')
                  AND posted.metadata->>'assignment_id' IS NOT NULL
                  AND posted.metadata->>'variant' IN ('catch-only', 'insight-layer')
                  AND posted.metadata->>'guess_capable' = 'true'
                ORDER BY entered.user_id, entered.created_at, entered.id
            ), activity AS (
                SELECT DISTINCT user_id,
                       (created_at AT TIME ZONE 'Asia/Seoul')::date AS active_date
                FROM renaiss_events
                WHERE user_id IS NOT NULL
            )
            SELECT
                cohort.variant AS cohort,
                COUNT(*)::int AS users,
                COUNT(*) FILTER (WHERE EXISTS (
                    SELECT 1
                    FROM renaiss_events insight
                    WHERE insight.user_id = cohort.user_id
                      AND insight.event_name IN ('price_guess_locked', 'daily_pick_locked')
                      AND (insight.created_at AT TIME ZONE 'Asia/Seoul')::date =
                          cohort.cohort_date
                ))::int AS insight_acted,
                COUNT(*) FILTER (WHERE cohort.cohort_date <=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - 1)::int AS d1_matured,
                COUNT(*) FILTER (WHERE cohort.cohort_date <=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - 1
                    AND EXISTS (
                        SELECT 1 FROM activity a
                        WHERE a.user_id = cohort.user_id
                          AND a.active_date = cohort.cohort_date + 1
                    ))::int AS d1_returned,
                COUNT(*) FILTER (WHERE cohort.cohort_date <=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - 7)::int AS d7_matured,
                COUNT(*) FILTER (WHERE cohort.cohort_date <=
                    (now() AT TIME ZONE 'Asia/Seoul')::date - 7
                    AND EXISTS (
                        SELECT 1 FROM activity a
                        WHERE a.user_id = cohort.user_id
                          AND a.active_date = cohort.cohort_date + 7
                    ))::int AS d7_returned
            FROM first_c cohort
            GROUP BY cohort.variant
            ORDER BY cohort.variant
            """,
            days,
        )
        spawn_funnel = await conn.fetchrow(
            """
            WITH exposed AS (
                SELECT DISTINCT session_id
                FROM renaiss_events
                WHERE event_name = 'spawn_posted'
                  AND session_id IS NOT NULL
                  AND created_at >= now() - ($1::int * interval '1 day')
            )
            SELECT
                COUNT(*)::int AS exposed_rounds,
                COUNT(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM renaiss_events e
                    WHERE e.session_id = exposed.session_id
                      AND e.event_name = 'catch_entered'
                ))::int AS rounds_with_catch,
                COUNT(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM renaiss_events e
                    WHERE e.session_id = exposed.session_id
                      AND e.event_name = 'price_guess_locked'
                ))::int AS rounds_with_guess,
                COUNT(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM renaiss_events e
                    WHERE e.session_id = exposed.session_id
                      AND e.event_name = 'spawn_revealed'
                ))::int AS revealed_rounds
            FROM exposed
            """,
            days,
        )
        first_success = await conn.fetchrow(
            """
            WITH attempts AS (
                SELECT user_id, MIN(created_at) AS attempted_at
                FROM renaiss_events
                WHERE event_name = 'first_c_attempted' AND user_id IS NOT NULL
                GROUP BY user_id
            ), successes AS (
                SELECT user_id, MIN(created_at) AS succeeded_at
                FROM renaiss_events
                WHERE event_name = 'first_c_entered' AND user_id IS NOT NULL
                GROUP BY user_id
            ), durations AS (
                SELECT EXTRACT(EPOCH FROM (s.succeeded_at - a.attempted_at)) AS seconds
                FROM attempts a
                JOIN successes s USING (user_id)
                WHERE s.succeeded_at >= a.attempted_at
                  AND a.attempted_at >= now() - ($1::int * interval '1 day')
            )
            SELECT
                COUNT(*)::int AS succeeded_users,
                ROUND(AVG(seconds))::int AS average_seconds,
                ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds))::int
                    AS median_seconds,
                COUNT(*) FILTER (WHERE seconds <= 180)::int AS within_3_minutes
            FROM durations
            """,
            days,
        )
        overdue = await conn.fetchrow(
            """
            SELECT
                COUNT(*)::int AS count,
                MIN(settles_at) AS oldest_due,
                COUNT(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM renaiss_market_price_snapshots s
                    WHERE s.board_card_id = p.board_card_id
                      AND s.pick_eligible = TRUE
                      AND s.captured_at >= p.settles_at
                      AND s.price_updated_at >= p.settles_at
                ))::int AS ready_but_unsettled,
                COUNT(*) FILTER (WHERE NOT EXISTS (
                    SELECT 1 FROM renaiss_market_price_snapshots s
                    WHERE s.board_card_id = p.board_card_id
                      AND s.pick_eligible = TRUE
                      AND s.captured_at >= p.settles_at
                      AND s.price_updated_at >= p.settles_at
                ))::int AS needs_refresh
            FROM renaiss_market_picks p
            WHERE settles_at <= now()
              AND settlement_snapshot_id IS NULL
            """
        )
        result_bells = await conn.fetch(
            """
            SELECT state, COUNT(*)::int AS count,
                   COUNT(*) FILTER (WHERE suppression_reason IS NOT NULL)::int
                       AS privacy_limited,
                   MAX(updated_at) AS latest_at
            FROM renaiss_result_bell_outbox
            WHERE bell_date >= (now() AT TIME ZONE 'Asia/Seoul')::date - $1::int
            GROUP BY state
            ORDER BY state
            """,
            days,
        )
        result_bell_unknown = await conn.fetch(
            """
            SELECT
                id, chat_id, bell_date, cohort_pick_date, state, expires_at,
                attempt_count, attempted_at, last_error_code, last_error, updated_at
            FROM renaiss_result_bell_outbox
            WHERE state = 'delivery_unknown'
            ORDER BY updated_at DESC, id DESC
            LIMIT 20
            """
        )
        refresh_lease = await conn.fetchrow(
            """
            SELECT lease_owner, lease_expires_at, next_attempt_at, last_status, updated_at
            FROM renaiss_job_leases
            WHERE job_name = 'daily_pick_price_refresh'
            """
        )
        api_cooldown = await conn.fetchrow(
            """
            SELECT blocked_until, reason, updated_at
            FROM renaiss_api_cooldowns
            WHERE api_name = 'renaiss_partner_api'
            """
        )
    return {
        # The current attempted->entered metric starts after treatment exposure and does
        # not represent group-join-to-first-success. Never graduate on that proxy.
        "measurement_contract_valid": False,
        "events": [dict(row) for row in events],
        "clicks": [dict(row) for row in clicks],
        "retention": dict(retention or {}),
        "retention_cohorts": [dict(row) for row in retention_cohorts],
        "spawn_funnel": dict(spawn_funnel or {}),
        "first_success": dict(first_success or {}),
        "overdue": dict(overdue or {}),
        "result_bells": [dict(row) for row in result_bells],
        "result_bell_unknown": [dict(row) for row in result_bell_unknown],
        "refresh_lease": dict(refresh_lease or {}),
        "api_cooldown": dict(api_cooldown or {}),
    }


def _rate(returned: int, matured: int) -> str:
    return f"{returned / matured * 100:.1f}%" if matured else "n/a"


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _cohort_rows(report: Mapping) -> dict[str, Mapping]:
    return {
        str(row.get("cohort")): row
        for row in report.get("retention_cohorts", [])
        if isinstance(row, Mapping)
    }


def evaluate_readiness(
    report: Mapping,
    *,
    min_cohort_size: int = 10,
    min_d7_uplift_pp: float = 5.0,
    min_insight_action_rate: float = 0.5,
    max_first_success_median_seconds: int = 180,
) -> tuple[bool, list[str]]:
    """Evaluate the smallest measurable pilot gates; observational uplift is not causal."""
    reasons: list[str] = []
    if report.get("measurement_contract_valid") is not True:
        reasons.append(
            "Graduation measurement contract is not valid: establish a pre-exposure FTUE "
            "baseline and intent-to-treat cohort before using retention uplift."
        )
    cohorts = _cohort_rows(report)
    catch_only = cohorts.get("catch-only", {})
    insight = cohorts.get("insight-layer", {})

    catch_d7_n = int(catch_only.get("d7_matured") or 0)
    insight_d7_n = int(insight.get("d7_matured") or 0)
    if catch_d7_n < min_cohort_size or insight_d7_n < min_cohort_size:
        reasons.append(
            "D7 cohorts are not mature enough "
            f"(catch-only {catch_d7_n}, insight-layer {insight_d7_n}; "
            f"need {min_cohort_size} each)."
        )
    else:
        catch_d7 = _ratio(int(catch_only.get("d7_returned") or 0), catch_d7_n) or 0.0
        insight_d7 = _ratio(int(insight.get("d7_returned") or 0), insight_d7_n) or 0.0
        uplift_pp = (insight_d7 - catch_d7) * 100
        if uplift_pp < min_d7_uplift_pp:
            reasons.append(
                f"D7 insight-layer uplift is {uplift_pp:+.1f}pp; "
                f"need at least +{min_d7_uplift_pp:.1f}pp."
            )

    insight_users = int(insight.get("users") or 0)
    insight_acted = int(insight.get("insight_acted") or 0)
    action_rate = _ratio(insight_acted, insight_users)
    if insight_users < min_cohort_size:
        reasons.append(
            f"Insight-layer exposure has {insight_users} users; need {min_cohort_size}."
        )
    elif action_rate is None or action_rate < min_insight_action_rate:
        shown = f"{(action_rate or 0) * 100:.1f}%"
        reasons.append(
            f"Same-day insight action rate is {shown}; "
            f"need at least {min_insight_action_rate * 100:.1f}%."
        )

    first_success = report.get("first_success") or {}
    succeeded = int(first_success.get("succeeded_users") or 0)
    median = first_success.get("median_seconds")
    if succeeded < min_cohort_size:
        reasons.append(f"First-c success has {succeeded} users; need {min_cohort_size}.")
    elif median is None or int(median) > max_first_success_median_seconds:
        shown = "unavailable" if median is None else f"{int(median)}s"
        reasons.append(
            f"Median first-c success is {shown}; "
            f"need <= {max_first_success_median_seconds}s."
        )
    return not reasons, reasons


def _one_line(value, *, limit: int = 240) -> str:
    return " ".join(str(value or "-").split())[:limit]


def print_report(report: dict, days: int) -> None:
    print(f"Renaiss pilot report | last {days} days")
    print("\nEvent funnel")
    for row in report["events"]:
        print(f"- {row['event_name']}: {row['events']} events / {row['users']} users")
    funnel = report.get("spawn_funnel") or {}
    exposed = int(funnel.get("exposed_rounds") or 0)
    caught = int(funnel.get("rounds_with_catch") or 0)
    guessed = int(funnel.get("rounds_with_guess") or 0)
    revealed = int(funnel.get("revealed_rounds") or 0)
    print(
        f"- round conversion: exposed {exposed} / catch {caught} "
        f"({_rate(caught, exposed)}) / guess {guessed} ({_rate(guessed, exposed)}) / "
        f"revealed {revealed} ({_rate(revealed, exposed)})"
    )
    first_success = report.get("first_success") or {}
    succeeded = int(first_success.get("succeeded_users") or 0)
    within_three = int(first_success.get("within_3_minutes") or 0)
    print(
        f"- first-c success: {succeeded} users / <=3m {within_three} "
        f"({_rate(within_three, succeeded)}) / median "
        f"{first_success.get('median_seconds') or 'n/a'}s"
    )
    print("\nOutbound clicks")
    if not report["clicks"]:
        print("- no tracked clicks")
    for row in report["clicks"]:
        print(f"- {row['source']}: {row['clicks']} clicks / {row['unique_users']} known users")
    retention = report["retention"]
    d1_matured = int(retention.get("d1_matured") or 0)
    d1_returned = int(retention.get("d1_returned") or 0)
    d7_matured = int(retention.get("d7_matured") or 0)
    d7_returned = int(retention.get("d7_returned") or 0)
    print("\nRetention (KST exact-day)")
    print(f"- D1: {d1_returned}/{d1_matured} ({_rate(d1_returned, d1_matured)})")
    print(f"- D7: {d7_returned}/{d7_matured} ({_rate(d7_returned, d7_matured)})")
    print("\nRetention by first-c feature exposure (KST exact-day, observational)")
    cohort_rows = _cohort_rows(report)
    for cohort_name in ("catch-only", "insight-layer"):
        row = cohort_rows.get(cohort_name, {})
        users = int(row.get("users") or 0)
        acted = int(row.get("insight_acted") or 0)
        d1_matured = int(row.get("d1_matured") or 0)
        d1_returned = int(row.get("d1_returned") or 0)
        d7_matured = int(row.get("d7_matured") or 0)
        d7_returned = int(row.get("d7_returned") or 0)
        print(
            f"- {cohort_name}: users {users} / same-day insight action "
            f"{acted}/{users} ({_rate(acted, users)}) / "
            f"D1 {d1_returned}/{d1_matured} ({_rate(d1_returned, d1_matured)}) / "
            f"D7 {d7_returned}/{d7_matured} ({_rate(d7_returned, d7_matured)})"
        )
    overdue = report["overdue"]
    print("\nDaily Pick operations")
    print(f"- overdue: {int(overdue.get('count') or 0)} / oldest: {overdue.get('oldest_due') or '-'}")
    print(f"- ready but unsettled: {int(overdue.get('ready_but_unsettled') or 0)}")
    print(f"- needs price refresh: {int(overdue.get('needs_refresh') or 0)}")
    print("\nPublic Result Bell")
    bells = report.get("result_bells") or []
    if not bells:
        print("- no result-bell rows")
    for row in bells:
        privacy_limited = int(row.get("privacy_limited") or 0)
        privacy_note = f" / privacy-limited: {privacy_limited}" if privacy_limited else ""
        print(
            f"- {row['state']}: {int(row.get('count') or 0)}{privacy_note} / "
            f"latest: {row.get('latest_at') or '-'}"
        )
    unknown_rows = report.get("result_bell_unknown") or []
    if unknown_rows:
        print("- delivery_unknown rows requiring operator reconciliation:")
    for row in unknown_rows:
        print(
            f"  id={row.get('id')} / chat={row.get('chat_id')} / "
            f"cohort={row.get('cohort_pick_date')} / expires={row.get('expires_at')} / "
            f"error={row.get('last_error_code') or '-'}: {_one_line(row.get('last_error'))}"
        )
    lease = report.get("refresh_lease") or {}
    print("\nPrice refresh lease")
    if not lease:
        print("- no lease row")
    else:
        print(
            f"- status: {lease.get('last_status') or '-'} / "
            f"next: {lease.get('next_attempt_at') or '-'} / "
            f"expires: {lease.get('lease_expires_at') or '-'}"
        )
    cooldown = report.get("api_cooldown") or {}
    print("\nPartner API cooldown")
    if not cooldown:
        print("- no shared cooldown row")
    else:
        print(
            f"- blocked until: {cooldown.get('blocked_until') or '-'} / "
            f"reason: {cooldown.get('reason') or '-'}"
        )


async def execute(
    days: int,
    *,
    require_ready: bool = False,
    min_cohort_size: int = 10,
    min_d7_uplift_pp: float = 5.0,
) -> int:
    exit_code = 1
    try:
        report = await load_report(days)
        print_report(report, days)
        if require_ready:
            ready, reasons = evaluate_readiness(
                report,
                min_cohort_size=min_cohort_size,
                min_d7_uplift_pp=min_d7_uplift_pp,
            )
            print("\nPilot readiness")
            print("- READY" if ready else "- NOT READY")
            for reason in reasons:
                print(f"  - {reason}")
            exit_code = 0 if ready else 2
        else:
            exit_code = 0
    except Exception as exc:
        print(f"Pilot report failed (error={type(exc).__name__}).")
    finally:
        try:
            await close_db()
        except Exception as exc:
            print(f"Pilot report DB cleanup failed (error={type(exc).__name__}).")
            exit_code = 1
    return exit_code


def main() -> None:
    load_runtime_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="exit 2 unless the minimum measurable pilot gates pass",
    )
    parser.add_argument("--min-cohort-size", type=int, default=10)
    parser.add_argument("--min-d7-uplift-pp", type=float, default=5.0)
    args = parser.parse_args()
    days = max(1, min(90, args.days))
    raise SystemExit(
        asyncio.run(
            execute(
                days,
                require_ready=args.require_ready,
                min_cohort_size=max(1, args.min_cohort_size),
                min_d7_uplift_pp=max(0.0, args.min_d7_uplift_pp),
            )
        )
    )


if __name__ == "__main__":
    main()
