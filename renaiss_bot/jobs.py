"""Scheduled jobs for the standalone Renaiss bot (KST based)."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape
from zoneinfo import ZoneInfo

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, ContextTypes

from renaiss_bot.database.event_queries import list_unfinished_spawns, log_event
from renaiss_bot.database.queries import get_catch_ranking
from renaiss_bot.database.catalog_queries import (
    acquire_catalog_refresh_lease,
    finish_catalog_refresh_lease,
    list_catalog_cards_for_refresh,
    mark_catalog_refresh_attempted,
    update_catalog_cached_price,
)
from renaiss_bot.database.market_queries import (
    acquire_market_refresh_job_lease,
    claim_due_pick_cards,
    begin_daily_pick_result_bell_delivery,
    claim_daily_pick_result_bell,
    enqueue_daily_pick_result_bell,
    finish_market_refresh_job_lease,
    get_latest_complete_result_cohort,
    mark_daily_pick_result_bell_failed,
    mark_daily_pick_result_bell_sent,
    record_market_price_snapshot,
    release_market_refresh_leases,
    renew_market_refresh_job_lease,
    settle_due_daily_picks,
)
from renaiss_bot.handlers.spawn import (
    burst_interval_seconds,
    first_spawn_delay,
    next_spawn_delay,
    official_chat_id,
    spawn_burst_active,
    spawn_interval_bounds,
    spawn_tick,
)
from renaiss_bot.services.client import RenaissAPICooldown, fetch_official_price
from renaiss_bot.services.emoji import icon
from renaiss_bot.services.market import (
    daily_pick_configuration_issues,
    daily_pick_enabled,
    daily_pick_requested,
    exact_price_lookup_configured,
)
from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.result_bell import build_daily_pick_result_bell
from renaiss_bot.services.tracking import cleanup_expired_tracking_links

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
TRACKING_CLEANUP_TIME_KST = time(hour=4, minute=10, tzinfo=KST)
RESULT_BELL_POLL_SECONDS = 300
MAX_REFRESH_INTERVAL_SECONDS = 86_400
CATALOG_REFRESH_TIMES_KST = (
    time(hour=3, minute=0, tzinfo=KST),
    time(hour=15, minute=0, tzinfo=KST),
)
RANKING_ANNOUNCE_TIME_KST = time(hour=22, minute=0, tzinfo=KST)
_RANK_MEDALS = ("🥇", "🥈", "🥉")


def _catalog_refresh_limit() -> int:
    try:
        return max(1, min(5000, int(os.getenv("RENAISS_CATALOG_REFRESH_LIMIT", "1000"))))
    except ValueError:
        return 1000


def _catalog_refresh_enabled() -> bool:
    raw = os.getenv("RENAISS_CATALOG_REFRESH_ENABLED", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"} and exact_price_lookup_configured()


def _catalog_card(row: dict) -> CardIdentity:
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            import json

            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    source_payload = metadata.get("source_payload")
    source_payload = source_payload if isinstance(source_payload, dict) else {}
    market_grade = str(
        metadata.get("market_grade")
        or os.getenv("RENAISS_API_DEFAULT_MARKET_GRADE", "PSA 10 Gem Mint")
        or "PSA 10 Gem Mint"
    )
    return CardIdentity(
        category=str(row.get("category") or "pokemon_tcg"),
        card_name=str(row.get("card_name") or ""),
        # Keep the runtime/catalog grade in the identity key. The Partner
        # request uses metadata.market_grade for the PSA 10 market asset.
        grade=str(row.get("grade") or "R"),
        set_code=str(row.get("set_code") or ""),
        set_name=str(row.get("set_name") or ""),
        collector_number=str(row.get("collector_number") or ""),
        language=str(row.get("language") or ""),
        local_card_id=str(row.get("local_card_id") or ""),
        image_url=row.get("image_url"),
        metadata={
            "variation": str(
                metadata.get("variation")
                or metadata.get("variant")
                or source_payload.get("variation")
                or source_payload.get("variant")
                or ""
            ),
            "market_grade": market_grade,
        },
    )


async def refresh_catalog_prices_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh the active season catalog in the background; public spawns stay DB-only."""
    if not _catalog_refresh_enabled():
        logger.info("Catalog price refresh skipped: exact Partner lookup is not configured.")
        return
    owner = f"renaiss-catalog:{os.getpid()}:{secrets.token_hex(12)}"
    if not await acquire_catalog_refresh_lease(owner=owner, lease_seconds=7200):
        logger.info("Catalog price refresh skipped: another instance owns the lease.")
        return
    refreshed = 0
    missing = 0
    failed = 0
    status = "completed"
    next_attempt_seconds = 39_600  # 11h: blocks duplicate instances, permits the 12h slot.
    try:
        rows = await list_catalog_cards_for_refresh(limit=_catalog_refresh_limit())
        for index, row in enumerate(rows):
            card = _catalog_card(row)
            try:
                price = await fetch_official_price(card, timeout_seconds=5.0)
                if price is None:
                    missing += 1
                    await mark_catalog_refresh_attempted(local_card_id=card.local_card_id)
                    continue
                refreshed += int(
                    await update_catalog_cached_price(
                        local_card_id=card.local_card_id,
                        price=price,
                    )
                )
            except RenaissAPICooldown as exc:
                failed += len(rows) - index
                status = "rate_limited"
                next_attempt_seconds = max(next_attempt_seconds, exc.retry_after_seconds)
                break
            except aiohttp.ClientResponseError as exc:
                failed += 1
                if exc.status == 429:
                    status = "rate_limited"
                    failed += len(rows) - index - 1
                    break
                logger.warning(
                    "Catalog refresh response failed card=%s status=%s",
                    card.local_card_id,
                    exc.status,
                )
            except Exception as exc:
                failed += 1
                logger.warning("Catalog refresh failed card=%s: %s", card.local_card_id, exc)
        if failed and status == "completed":
            status = "completed_with_failures"
        logger.info(
            "Catalog cache refresh done: due=%s refreshed=%s missing=%s failed=%s.",
            len(rows),
            refreshed,
            missing,
            failed,
        )
    finally:
        await finish_catalog_refresh_lease(
            owner=owner,
            status=status,
            retry_after_seconds=next_attempt_seconds,
        )


def _daily_pick_refresh_limit() -> int:
    try:
        return max(1, min(50, int(os.getenv("RENAISS_DAILY_PICK_REFRESH_LIMIT", "8"))))
    except ValueError:
        return 8


def _daily_pick_refresh_seconds() -> int:
    try:
        return max(
            300,
            min(
                MAX_REFRESH_INTERVAL_SECONDS,
                int(os.getenv("RENAISS_DAILY_PICK_REFRESH_SECONDS", "3600")),
            ),
        )
    except (TypeError, ValueError, OverflowError):
        return 3600


def _daily_pick_refresh_lease_seconds() -> int:
    try:
        return max(
            30,
            min(
                MAX_REFRESH_INTERVAL_SECONDS,
                int(os.getenv("RENAISS_DAILY_PICK_REFRESH_LEASE_SECONDS", "180")),
            ),
        )
    except (TypeError, ValueError, OverflowError):
        return 180


def _result_bell_window_open(now: datetime | None = None) -> bool:
    local = (now or datetime.now(timezone.utc)).astimezone(KST)
    return (local.hour, local.minute) >= (21, 5)


async def cleanup_tracking_links_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        count = await cleanup_expired_tracking_links()
        logger.info("Expired referral-link cleanup done: %s rows.", count)
    except Exception as exc:
        logger.warning("Expired referral-link cleanup skipped: %s", exc)


async def spawn_loop_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run one spawn tick and always schedule the next quiet variable interval."""
    application = getattr(context, "application", None)
    burst = spawn_burst_active(application)
    try:
        await spawn_tick(context, burst=burst)
    finally:
        # Re-read the flag: /spawnoff during a long tick must win immediately.
        if spawn_burst_active(application):
            delay = burst_interval_seconds()
        else:
            delay = next_spawn_delay()
        # Replace-by-name keeps exactly one loop even if an operator command
        # rescheduled while this tick was still running.
        jobs_by_name = getattr(context.job_queue, "get_jobs_by_name", None)
        for job in jobs_by_name("renaiss_official_spawn") if callable(jobs_by_name) else ():
            job.schedule_removal()
        context.job_queue.run_once(
            spawn_loop_job,
            when=delay,
            name="renaiss_official_spawn",
            job_kwargs={"misfire_grace_time": None},
        )


def _http_retry_after_seconds(value: str | None) -> int:
    if not value:
        return 0
    try:
        return min(MAX_REFRESH_INTERVAL_SECONDS, max(0, int(float(value))))
    except (TypeError, ValueError, OverflowError):
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return min(
            MAX_REFRESH_INTERVAL_SECONDS,
            max(0, int((retry_at - datetime.now(timezone.utc)).total_seconds())),
        )


async def refresh_daily_pick_prices_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch new exact marks while one fenced worker owns the API batch."""
    recovered = await settle_due_daily_picks(limit=500)
    if not exact_price_lookup_configured():
        if recovered:
            logger.info("Settled %s Daily Pick obligations from persisted marks.", len(recovered))
        logger.warning(
            "Daily Pick obligation refresh cannot fetch new marks: exact Partner API is not configured."
        )
        return
    lease_owner = f"renaiss-refresh:{os.getpid()}:{secrets.token_hex(12)}"
    lease_seconds = max(
        _daily_pick_refresh_lease_seconds(),
        _daily_pick_refresh_limit() * 10 + 60,
    )
    if not await acquire_market_refresh_job_lease(
        lease_owner=lease_owner,
        lease_seconds=lease_seconds,
    ):
        logger.info("Daily Pick refresh skipped: another worker owns the API lease.")
        return
    final_status = "crashed"
    retry_after_seconds = 0
    try:
        final_status, retry_after_seconds = await _refresh_daily_pick_prices_batch(
            context,
            recovered=recovered,
            job_lease_owner=lease_owner,
            job_lease_seconds=lease_seconds,
        )
    finally:
        try:
            released = await finish_market_refresh_job_lease(
                lease_owner=lease_owner,
                cadence_seconds=_daily_pick_refresh_seconds(),
                retry_after_seconds=retry_after_seconds,
                status=final_status,
            )
            if not released:
                logger.warning("Daily Pick refresh lease completion was fenced out.")
        except Exception as exc:
            logger.warning("Daily Pick refresh lease completion failed: %s", exc)


async def _refresh_daily_pick_prices_batch(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    recovered: list[dict],
    job_lease_owner: str,
    job_lease_seconds: int,
) -> tuple[str, int]:
    worker_token = secrets.token_hex(16)
    due_cards = await claim_due_pick_cards(
        worker_token=worker_token,
        limit=_daily_pick_refresh_limit(),
        lease_seconds=job_lease_seconds,
    )
    refreshed = 0
    newer = 0
    failed = 0
    newly_settled = 0
    batch_status = "completed"
    retry_after_seconds = 0
    for index, row in enumerate(due_cards):
        card = CardIdentity(
            category=str(row["category"]),
            card_name=str(row["card_name"]),
            set_code=str(row.get("set_code") or ""),
            set_name=str(row.get("set_name") or ""),
            collector_number=str(row.get("collector_number") or ""),
            language=str(row.get("language") or ""),
            grade=str(row.get("grade") or "RAW"),
            local_card_id=str(row.get("local_card_id") or ""),
            image_url=row.get("image_url"),
            metadata={"variation": str(row.get("variation") or "")},
        )
        try:
            if not await renew_market_refresh_job_lease(
                lease_owner=job_lease_owner,
                lease_seconds=job_lease_seconds,
            ):
                batch_status = "lease_lost"
                failed += len(due_cards) - index
                break
            price = await fetch_official_price(card, timeout_seconds=5.0)
            if price is None:
                failed += 1
                continue
            inserted = await record_market_price_snapshot(
                board_card_id=int(row["board_card_id"]),
                card=card,
                price=price,
            )
            refreshed += int(inserted)
            newly_settled += len(
                await settle_due_daily_picks(board_card_id=int(row["board_card_id"]), limit=500)
            )
            updated_at = price.price_updated_at
            is_newer = bool(
                updated_at is not None
                and updated_at >= row["earliest_unsettled_at"]
            )
            newer += int(is_newer)
            if inserted:
                timestamp_key = updated_at.isoformat() if updated_at else "unknown"
                await log_event(
                    "daily_pick_price_refreshed",
                    event_key=(
                        f"daily-pick:refresh:{row['board_card_id']}:"
                        f"{timestamp_key}"
                    ),
                    metadata={
                        "board_card_id": row["board_card_id"],
                        "pending_pick_count": row["pending_pick_count"],
                        "price_status": price.status,
                        "price_source": price.source,
                        "newer_than_settlement": is_newer,
                    },
                )
        except RenaissAPICooldown as exc:
            failed += len(due_cards) - index
            batch_status = "rate_limited"
            retry_after_seconds = exc.retry_after_seconds
            logger.warning(
                "Daily Pick refresh stopped by shared Partner API cooldown: %ss.",
                retry_after_seconds,
            )
            break
        except aiohttp.ClientResponseError as exc:
            failed += 1
            if exc.status == 429:
                batch_status = "rate_limited"
                failed += len(due_cards) - index - 1
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                retry_after_seconds = (
                    _http_retry_after_seconds(retry_after)
                    or _daily_pick_refresh_seconds()
                )
                logger.error(
                    "Daily Pick refresh rate-limited; stopping batch. retry_after=%s",
                    retry_after or "unknown",
                )
                break
            logger.warning(
                "Daily Pick API response failed board_card_id=%s status=%s: %s",
                row.get("board_card_id"),
                exc.status,
                exc,
            )
        except Exception as exc:
            failed += 1
            logger.warning(
                "Daily Pick price refresh failed board_card_id=%s: %s",
                row.get("board_card_id"),
                exc,
            )
    await release_market_refresh_leases(
        worker_token=worker_token,
        status=batch_status if failed == 0 else f"{batch_status}_with_failures",
    )
    newly_settled += len(await settle_due_daily_picks(limit=500))
    logger.info(
        "Daily Pick refresh done: due=%s inserted=%s newer=%s failed=%s "
        "recovered_settled=%s newly_settled=%s.",
        len(due_cards),
        refreshed,
        newer,
        failed,
        len(recovered),
        newly_settled,
    )
    final_status = batch_status if failed == 0 else f"{batch_status}_with_failures"
    return final_status, retry_after_seconds


def _retry_after_seconds(exc: RetryAfter) -> int:
    value = exc.retry_after
    try:
        seconds = value.total_seconds() if isinstance(value, timedelta) else float(value)
        return min(MAX_REFRESH_INTERVAL_SECONDS, max(1, int(seconds)))
    except (TypeError, ValueError, OverflowError):
        return RESULT_BELL_POLL_SECONDS


async def publish_daily_pick_result_bell_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Publish at most one privacy-safe, fully settled community result bell."""
    now = datetime.now(timezone.utc)
    if not _result_bell_window_open(now):
        return
    chat_id = official_chat_id()
    if chat_id is None:
        return
    bell_date = now.astimezone(KST).date()
    cohort = await get_latest_complete_result_cohort(
        chat_id=chat_id,
        before_date=bell_date,
        oldest_date=bell_date - timedelta(days=2),
        as_of=now,
    )
    if cohort is not None:
        message_text, metrics, suppression_reason = build_daily_pick_result_bell(cohort)
        created = await enqueue_daily_pick_result_bell(
            chat_id=chat_id,
            bell_date=bell_date,
            cohort_pick_date=cohort["pick_date"],
            message_text=message_text,
            metrics=metrics,
            suppression_reason=suppression_reason,
            available_at=now,
        )
        if created and suppression_reason:
            await log_event(
                "daily_pick_result_privacy_limited",
                event_key=f"{created['event_key']}:privacy-limited",
                chat_id=chat_id,
                metadata={
                    "cohort_pick_date": cohort["pick_date"],
                    "reason": suppression_reason,
                    "participant_count": cohort["participant_count"],
                },
            )

    attempt_token = secrets.token_hex(16)
    claimed = await claim_daily_pick_result_bell(
        chat_id=chat_id,
        bell_date=bell_date,
        attempt_token=attempt_token,
        lease_owner=f"renaiss-bot:{os.getpid()}",
    )
    if claimed is None:
        return
    username = getattr(context.bot, "username", None)
    reply_markup = None
    if username and daily_pick_enabled():
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Choose Today's Pick", url=f"https://t.me/{username}?start=market")]]
        )
    inflight = await begin_daily_pick_result_bell_delivery(
        outbox_id=int(claimed["id"]),
        attempt_token=attempt_token,
    )
    if inflight is None:
        return
    delivery_chat_id = int(inflight["chat_id"])
    if delivery_chat_id != chat_id:
        await mark_daily_pick_result_bell_failed(
            outbox_id=int(inflight["id"]),
            attempt_token=attempt_token,
            state="dead",
            error_code="chat_id_mismatch",
            error="claimed result bell does not belong to the configured official chat",
        )
        logger.error(
            "Result Bell blocked because claimed chat_id=%s differs from configured chat_id=%s.",
            delivery_chat_id,
            chat_id,
        )
        return
    try:
        message = await context.bot.send_message(
            chat_id=delivery_chat_id,
            text=str(inflight["message_text"]),
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except RetryAfter as exc:
        retry_seconds = _retry_after_seconds(exc)
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_seconds)
        await mark_daily_pick_result_bell_failed(
            outbox_id=int(inflight["id"]),
            attempt_token=attempt_token,
            state="retryable",
            error_code="telegram_retry_after",
            error=str(exc),
            next_attempt_at=retry_at,
        )
        return
    except (BadRequest, Forbidden) as exc:
        await mark_daily_pick_result_bell_failed(
            outbox_id=int(inflight["id"]),
            attempt_token=attempt_token,
            state="dead",
            error_code=type(exc).__name__,
            error=str(exc),
        )
        return
    except (TimedOut, NetworkError) as exc:
        await mark_daily_pick_result_bell_failed(
            outbox_id=int(inflight["id"]),
            attempt_token=attempt_token,
            state="delivery_unknown",
            error_code=type(exc).__name__,
            error=str(exc),
        )
        return
    except Exception as exc:
        await mark_daily_pick_result_bell_failed(
            outbox_id=int(inflight["id"]),
            attempt_token=attempt_token,
            state="delivery_unknown",
            error_code=type(exc).__name__,
            error=str(exc),
        )
        return

    marked_sent = await mark_daily_pick_result_bell_sent(
        outbox_id=int(inflight["id"]),
        attempt_token=attempt_token,
        telegram_message_id=int(message.message_id),
    )
    if not marked_sent:
        logger.error(
            "Result Bell delivered but DB acknowledgement failed outbox_id=%s; "
            "automatic retry remains disabled by inflight recovery.",
            inflight["id"],
        )
        return
    await log_event(
        "daily_pick_result_published",
        event_key=str(inflight["event_key"]),
        chat_id=chat_id,
        metadata={
            "cohort_pick_date": inflight["cohort_pick_date"],
            "telegram_message_id": message.message_id,
        },
    )


async def _edit_recovered_prompt(application, *, chat_id: int, message_id: int, text: str) -> None:
    """Close a prompt from a previous process; it may be a text or a photo message."""
    try:
        await application.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=None,
        )
    except BadRequest as exc:
        if "no text in the message" not in str(exc).lower():
            raise
        await application.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=None,
        )


async def recover_unfinished_spawns(application: Application, *, page_size: int = 50) -> int:
    """Close every orphaned prompt, failing readiness if any remain ambiguous."""
    after_id = 0
    recovered = 0
    failures: list[str] = []
    while True:
        rows = await list_unfinished_spawns(limit=page_size, after_id=after_id)
        if not rows:
            break
        for row in rows:
            posted_event_id = int(row.get("posted_event_id") or 0)
            after_id = max(after_id, posted_event_id)
            if posted_event_id <= 0:
                failures.append(str(row.get("session_id") or "missing-event-id"))
                continue
            metadata = row.get("metadata") or {}
            message_id = metadata.get("message_id")
            chat_id = row.get("chat_id")
            session_id = row.get("session_id")
            if not message_id or not chat_id or not session_id:
                failures.append(str(session_id or posted_event_id))
                continue
            award_user_id = row.get("award_user_id")
            award_metadata = row.get("award_metadata") or {}
            if award_user_id is not None:
                winner_name = escape(str(award_metadata.get("winner_name") or "the winner"))
                card_name = escape(str(award_metadata.get("card_name") or "The card"))
                try:
                    await _edit_recovered_prompt(
                        application,
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                        text=(
                            "✅ <b>Spawn recovered after a bot restart.</b>\n"
                            f"{card_name} was already awarded to <b>{winner_name}</b> "
                            "and remains in their collection."
                        ),
                    )
                except Exception as exc:
                    logger.info(
                        "Awarded spawn recovery skipped session=%s: %s",
                        session_id,
                        exc,
                    )
                    failures.append(str(session_id))
                    continue
                terminal_logged = await log_event(
                    "spawn_revealed",
                    event_key=f"spawn:{session_id}:recovered",
                    user_id=int(award_user_id),
                    chat_id=int(chat_id),
                    session_id=str(session_id),
                    metadata={
                        "reason": "bot_restart_after_award",
                        "message_id": message_id,
                        "award_recovered": True,
                    },
                )
                if terminal_logged:
                    recovered += 1
                else:
                    failures.append(str(session_id))
                continue
            prompt_closed = False
            try:
                await _edit_recovered_prompt(
                    application,
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=(
                        "ℹ️ <b>Spawn round closed after a bot restart.</b>\n"
                        "No collection award was recorded for this round. "
                        "No Daily Pick penalty was applied."
                    ),
                )
                prompt_closed = True
            except BadRequest as exc:
                # Telegram's definitive rejection means there is no actionable
                # open prompt left for this bot (deleted/already closed/etc.).
                prompt_closed = True
                logger.info("Orphaned spawn prompt is already closed session=%s: %s", session_id, exc)
            except Exception as exc:
                logger.info("Orphaned spawn prompt close skipped session=%s: %s", session_id, exc)
            recovery_event = "spawn_cancelled" if prompt_closed else "spawn_recovery_close_failed"
            recovery_suffix = "cancelled" if prompt_closed else "recovery-close-failed"
            terminal_logged = await log_event(
                recovery_event,
                event_key=f"spawn:{session_id}:{recovery_suffix}",
                chat_id=int(chat_id),
                session_id=str(session_id),
                metadata={
                    "reason": "bot_restart",
                    "message_id": message_id,
                    "prompt_closed": prompt_closed,
                },
            )
            if prompt_closed and terminal_logged:
                recovered += 1
            else:
                failures.append(str(session_id))
        if len(rows) < page_size:
            break
    if failures:
        sample = ", ".join(failures[:5])
        raise RuntimeError(
            f"spawn recovery incomplete: {len(failures)} unresolved prompt(s): {sample}"
        )
    return recovered


def ranking_announce_enabled() -> bool:
    return os.getenv("RENAISS_RANKING_ANNOUNCE_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def build_ranking_message(ranking: dict, *, title: str, footer: str | None = None) -> str:
    lines = [f"{icon('gotcha')} <b>{escape(title)}</b>"]
    for row in ranking.get("rows", [])[:5]:
        rank = int(row.get("rank") or 0)
        marker = _RANK_MEDALS[rank - 1] if 1 <= rank <= 3 else f" {rank}."
        name = escape(str(row.get("winner_name") or "Collector"))
        catches = int(row.get("catches") or 0)
        plural = "es" if catches != 1 else ""
        lines.append(f"{marker} <b>{name}</b> — {catches} catch{plural}")
    best = ranking.get("best_catch")
    if best:
        lines.append(
            f"{icon('crystal')} Top catch: <b>{escape(str(best.get('card_name') or '-'))}</b>"
            f" · {icon('coin')} <b>${float(best.get('fmv_usd') or 0):,.0f}</b>"
            f" · {escape(str(best.get('winner_name') or 'Collector'))}"
        )
    if footer:
        lines.append(f"<i>{escape(footer)}</i>")
    return "\n".join(lines)


async def announce_daily_ranking_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """22:00 KST — post today's catch ranking; Sundays add the weekly final."""
    if not ranking_announce_enabled():
        return
    chat_id = official_chat_id()
    if chat_id is None:
        logger.info("Ranking announce skipped: official chat is not configured.")
        return
    now_kst = datetime.now(KST)
    today = now_kst.date().isoformat()

    try:
        daily = await get_catch_ranking(period="day", limit=5)
    except Exception as exc:
        logger.warning("Daily ranking query failed: %s", exc)
        return
    if daily["rows"]:
        claimed = await log_event(
            "daily_rank_posted",
            event_key=f"daily-rank:{chat_id}:{today}",
            chat_id=chat_id,
            metadata={"rows": len(daily["rows"]), "total": daily["total_catches"]},
        )
        if claimed:
            await context.bot.send_message(
                chat_id,
                build_ranking_message(
                    daily,
                    title=f"Daily Catch Ranking · {now_kst.strftime('%b %d')} (KST)",
                    footer="Posted every night at 22:00 KST.",
                ),
                parse_mode="HTML",
            )
    else:
        logger.info("Daily ranking skipped: no catches today.")

    if now_kst.weekday() == 6:  # Sunday — weekly final before the Monday reset
        try:
            weekly = await get_catch_ranking(period="week", limit=10)
        except Exception as exc:
            logger.warning("Weekly ranking query failed: %s", exc)
            return
        if not weekly["rows"]:
            return
        week_key = now_kst.strftime("%G-W%V")
        claimed = await log_event(
            "weekly_rank_posted",
            event_key=f"weekly-rank:{chat_id}:{week_key}",
            chat_id=chat_id,
            metadata={"rows": len(weekly["rows"]), "total": weekly["total_catches"]},
        )
        if claimed:
            await context.bot.send_message(
                chat_id,
                build_ranking_message(
                    weekly,
                    title=f"Weekly Final · {week_key}",
                    footer="Weekly board restarts Monday 00:00 KST. Congrats, collectors!",
                ),
                parse_mode="HTML",
            )


def register_jobs(application: Application) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        raise RuntimeError(
            "Telegram JobQueue is required for Renaiss scheduled jobs; "
            "startup cannot continue."
        )

    job_queue.run_daily(
        cleanup_tracking_links_job,
        time=TRACKING_CLEANUP_TIME_KST,
        name="renaiss_referral_link_cleanup",
        job_kwargs={"misfire_grace_time": None},
    )

    for refresh_time in CATALOG_REFRESH_TIMES_KST:
        job_queue.run_daily(
            refresh_catalog_prices_job,
            time=refresh_time,
            name=f"renaiss_catalog_price_refresh_{refresh_time.hour:02d}",
            job_kwargs={"misfire_grace_time": None},
        )
    logger.info("Catalog DB cache refresh scheduled at 03:00 and 15:00 KST.")

    job_queue.run_daily(
        announce_daily_ranking_job,
        time=RANKING_ANNOUNCE_TIME_KST,
        name="renaiss_daily_ranking_announce",
        job_kwargs={"misfire_grace_time": None},
    )
    logger.info("Daily catch ranking announce scheduled at 22:00 KST (weekly final on Sundays).")

    # Admission can close instantly, but existing T+24 picks and outbox rows are
    # obligations. Drain workers therefore run whenever the DB-backed bot runs.
    job_queue.run_repeating(
        refresh_daily_pick_prices_job,
        interval=_daily_pick_refresh_seconds(),
        first=120,
        name="renaiss_daily_pick_price_refresh",
        job_kwargs={"misfire_grace_time": None},
    )
    job_queue.run_repeating(
        publish_daily_pick_result_bell_job,
        interval=RESULT_BELL_POLL_SECONDS,
        first=30,
        name="renaiss_daily_pick_result_bell",
        job_kwargs={"misfire_grace_time": None},
    )
    logger.info(
        "Daily Pick obligation drain scheduled; refresh cadence=%ss.",
        _daily_pick_refresh_seconds(),
    )
    if daily_pick_enabled():
        logger.info("Daily Pick admission is open.")
    elif daily_pick_requested():
        issues = daily_pick_configuration_issues()
        detail = "; ".join(issues) if issues else "live Partner/admin preflight did not pass"
        logger.error("Daily Pick admission stays closed: %s.", detail)
    else:
        logger.info("Daily Pick closed: RENAISS_DAILY_PICK_ENABLED is not set.")

    # 공식방 스폰: 무인 기본값은 그룹 노이즈를 줄인 2~4시간 가변 간격.
    if official_chat_id() is not None:
        minimum, maximum = spawn_interval_bounds()
        job_queue.run_once(
            spawn_loop_job,
            when=first_spawn_delay(),
            name="renaiss_official_spawn",
            job_kwargs={"misfire_grace_time": None},
        )
        logger.info(
            "Official-room first spawn in %ss; later cadence %s-%ss.",
            first_spawn_delay(),
            minimum,
            maximum,
        )
    else:
        logger.info("Official spawn not scheduled: RENAISS_OFFICIAL_CHAT_ID/QUIZ_CHAT_ID not set.")
