"""Daily price quiz handlers: post, answer, reveal."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.database.queries import (
    KST,
    add_drop_points,
    close_quiz_round,
    create_quiz_round,
    get_correct_answer_dates,
    get_quiz_round,
    get_quiz_round_number,
    list_quiz_answers,
    log_pack_event,
    record_quiz_answer,
    register_pack_cards,
    set_quiz_message,
    weekly_quiz_leaderboard,
)
from renaiss_bot.renderers.overlay import render_overlay_card
from renaiss_bot.services.models import CardIdentity, RenaissPrice
from renaiss_bot.services.features import pack_economy_enabled
from renaiss_bot.services.pack import open_pack
from renaiss_bot.services.quiz import (
    build_price_options,
    compute_streak,
    format_distribution,
    format_price_option,
    pick_quiz_subject,
)
from renaiss_bot.services.tracking import build_tracked_url

logger = logging.getLogger(__name__)

QUIZ_CALLBACK_PREFIX = "renaiss:quiz:"
RP_CORRECT_ANSWER = 100


def quiz_chat_id() -> int | None:
    raw = os.getenv("RENAISS_QUIZ_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("RENAISS_QUIZ_CHAT_ID is not an integer: %s", raw)
        return None


def quiz_open_seconds() -> int:
    try:
        return max(30, int(os.getenv("RENAISS_QUIZ_OPEN_SECONDS", "120")))
    except ValueError:
        return 120


def _options_keyboard(round_id: int, options: list[float]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            format_price_option(value),
            callback_data=f"{QUIZ_CALLBACK_PREFIX}{round_id}:{index}",
        )
        for index, value in enumerate(options)
    ]
    return InlineKeyboardMarkup([buttons[:2], buttons[2:]])


def _quiz_text(card_name: str, close_seconds: int, round_number: int) -> str:
    reward_text = ""
    if pack_economy_enabled():
        reward_text = (
            "🎁 Every correct answer wins a free pack.\n"
            "💎 One lucky winner gets a <b>Premium Pack</b>.\n\n"
        )
    return (
        f"🎯 <b>Daily Price Quiz #{round_number}</b>\n"
        "------------\n"
        f"Guess today's market value of <b>{escape(card_name)}</b>!\n\n"
        f"⏳ Answers lock in <b>{close_seconds}s</b>.\n"
        f"{reward_text}"
        "Prices come from the Renaiss Index API (beta reference data)."
    )


async def post_daily_quiz(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = quiz_chat_id()
    if chat_id is None:
        logger.info("Daily quiz skipped: RENAISS_QUIZ_CHAT_ID not set.")
        return

    subject = await pick_quiz_subject()
    if subject is None:
        logger.warning("Daily quiz skipped: no priced card available.")
        return

    options, correct_index = build_price_options(subject.correct_price_usd)
    close_seconds = quiz_open_seconds()
    closes_at = datetime.now(timezone.utc) + timedelta(seconds=close_seconds)

    round_id = await create_quiz_round(
        chat_id=chat_id,
        category=subject.card.category,
        card_name=subject.card.card_name,
        local_card_id=subject.card.local_card_id or None,
        card_image_url=subject.price.image_url or subject.card.image_url,
        correct_price_usd=round(float(options[correct_index]), 2),
        options=options,
        correct_index=correct_index,
        price_source=subject.price.source,
        referral_url=subject.price.referral_url,
        closes_at=closes_at,
    )
    if round_id is None:
        logger.warning("Daily quiz skipped: round insert failed.")
        return

    round_number = await get_quiz_round_number(round_id, chat_id)
    text = _quiz_text(subject.card.card_name, close_seconds, round_number)
    keyboard = _options_keyboard(round_id, options)
    # 문제 이미지는 시세 라벨 없는 원본 카드만 사용한다 (오버레이는 가격이 노출됨).
    image_url = subject.price.image_url or subject.card.image_url
    message = None
    if image_url:
        try:
            message = await context.bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.debug("Quiz photo send failed, falling back to text: %s", exc)
    if message is None:
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    await set_quiz_message(round_id, message.message_id)

    context.job_queue.run_once(
        close_quiz_job,
        when=close_seconds,
        data=round_id,
        name=f"renaiss_quiz_close_{round_id}",
        job_kwargs={"misfire_grace_time": None},
    )
    logger.info("Daily quiz round %s posted to chat %s.", round_id, chat_id)


async def on_quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return

    payload = query.data.removeprefix(QUIZ_CALLBACK_PREFIX)
    try:
        round_part, choice_part = payload.split(":", 1)
        round_id = int(round_part)
        choice_index = int(choice_part)
    except ValueError:
        await query.answer()
        return

    round_data = await get_quiz_round(round_id)
    if round_data is None:
        await query.answer("Quiz not found.", show_alert=False)
        return

    closes_at = round_data.get("closes_at")
    now = datetime.now(timezone.utc)
    if round_data.get("status") != "open" or (closes_at is not None and now >= closes_at):
        await query.answer("This quiz is already closed.", show_alert=False)
        return

    options = round_data.get("options_json") or []
    if not isinstance(options, list) or not (0 <= choice_index < len(options)):
        await query.answer()
        return

    user = update.effective_user
    display_name = user.full_name or user.username or str(user.id)
    is_correct = choice_index == int(round_data.get("correct_index", -1))
    recorded = await record_quiz_answer(
        round_id=round_id,
        user_id=user.id,
        display_name=display_name,
        choice_index=choice_index,
        is_correct=is_correct,
    )
    if recorded:
        await query.answer(
            f"Locked in: {format_price_option(float(options[choice_index]))}",
            show_alert=False,
        )
    else:
        await query.answer("You already answered this quiz.", show_alert=False)


async def close_quiz_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    round_id = context.job.data if context.job else None
    if round_id is None:
        return
    round_data = await get_quiz_round(int(round_id))
    if round_data is None:
        return
    if not await close_quiz_round(int(round_id)):
        return  # 이미 정산됨 (중복 방지)

    chat_id = int(round_data["chat_id"])
    options = round_data.get("options_json") or []
    correct_index = int(round_data.get("correct_index", 0))
    correct_price = float(round_data.get("correct_price_usd") or 0)
    card_name = str(round_data.get("card_name") or "-")

    answers, round_number = await asyncio.gather(
        list_quiz_answers(int(round_id)),
        get_quiz_round_number(int(round_id), chat_id),
    )
    winners = [answer for answer in answers if answer.get("is_correct")]

    jackpot_result = None
    jackpot_winner = None
    if winners and pack_economy_enabled():
        # 정답자별 보상 (프리팩 + RP) 은 서로 독립 → 전원 병렬 지급
        await asyncio.gather(
            *(
                _grant_correct_rewards(int(winner["user_id"]), chat_id)
                for winner in winners
            ),
            return_exceptions=True,
        )
        jackpot_winner = random.choice(winners)
        jackpot_result = await _grant_premium_pack(int(jackpot_winner["user_id"]), chat_id)

    reveal_lines = [
        f"🎯 <b>Daily Price Quiz #{round_number} — Answer</b>",
        "------------",
        f"Card: <b>{escape(card_name)}</b>",
        f"Market Value: <b>{format_price_option(correct_price)}</b>",
        "",
    ]
    if answers and isinstance(options, list) and options:
        counts = [0] * len(options)
        for answer in answers:
            choice = int(answer.get("choice_index", -1))
            if 0 <= choice < len(counts):
                counts[choice] += 1
        reveal_lines.extend(format_distribution([float(v) for v in options], counts, correct_index))
        reveal_lines.append("")

    if not answers:
        reveal_lines.append("No answers today. See you tomorrow at the same time!")
    elif not winners:
        reveal_lines.append(f"{len(answers)} answered, nobody got it. Tough market!")
    else:
        # 연속 정답 스트릭 (오늘 포함, 끊겨도 페널티 없음 — 명예 표기만)
        streak_dates = await get_correct_answer_dates([int(w["user_id"]) for w in winners])
        today_kst = datetime.now(KST).date()
        names_parts = []
        for winner in winners[:12]:
            name = escape(str(winner.get("display_name") or winner["user_id"]))
            streak = compute_streak(streak_dates.get(int(winner["user_id"]), set()), today_kst)
            names_parts.append(f"{name} 🔥{streak}" if streak >= 2 else name)
        names = ", ".join(names_parts)
        if len(winners) > 12:
            names += f" +{len(winners) - 12}"
        reveal_lines.append(f"✅ Correct ({len(winners)}/{len(answers)}): {names}")
        if pack_economy_enabled():
            reveal_lines.append(f"🎁 Each winner received a free pack + RP +{RP_CORRECT_ANSWER}.")
        else:
            reveal_lines.append("🧠 Correct calls are recorded as market insight — no currency reward.")
        if jackpot_winner is not None and pack_economy_enabled():
            jackpot_name = escape(str(jackpot_winner.get("display_name") or jackpot_winner["user_id"]))
            reveal_lines.append(f"💎 Premium Pack jackpot: <b>{jackpot_name}</b>!")

    # 리더보드 조회와 정답 카드 렌더링(Playwright) 은 독립 → 병렬
    leaderboard, reveal_image = await asyncio.gather(
        weekly_quiz_leaderboard(limit=5),
        _render_reveal_card(round_data),
    )
    if leaderboard:
        reveal_lines.extend(["", "<b>This week's leaderboard</b>"])
        for rank, row in enumerate(leaderboard, 1):
            name = escape(str(row.get("display_name") or row.get("user_id")))
            reveal_lines.append(f"{rank}. {name} — {int(row.get('correct_count') or 0)} correct")

    reveal_lines.extend(["", "Renaiss Index API is beta data. Treat Market Value as an experimental reference."])

    keyboard = None
    referral_url = round_data.get("referral_url")
    if referral_url:
        tracked_url = await build_tracked_url(
            referral_url,
            user_id=None,
            chat_id=chat_id,
            local_card_id=round_data.get("local_card_id"),
            source="telegram_quiz_reveal",
        )
        if tracked_url:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("View on Renaiss", url=tracked_url)]]
            )

    try:
        if reveal_image:
            photo = BytesIO(reveal_image)
            photo.name = "renaiss_quiz_answer.png"
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption="\n".join(reveal_lines),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(reveal_lines),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    except Exception as exc:
        logger.warning("Quiz reveal send failed: %s", exc)

    if jackpot_result is not None and jackpot_winner is not None:
        await _announce_jackpot(context, chat_id, jackpot_winner, jackpot_result)

    # options/correct_index는 정산 후 재사용처가 없지만 로깅에는 남긴다.
    logger.info(
        "Quiz round %s revealed: %s answers, %s correct, options=%s correct_index=%s",
        round_id,
        len(answers),
        len(winners),
        options,
        correct_index,
    )


async def _grant_correct_rewards(user_id: int, chat_id: int) -> None:
    await asyncio.gather(
        _grant_free_pack(user_id, chat_id),
        add_drop_points(user_id, RP_CORRECT_ANSWER, source="quiz_correct"),
    )


async def _grant_free_pack(user_id: int, chat_id: int):
    result = await open_pack(user_id=user_id, category_key="pokemon_tcg", pack_type="free", count=1)
    try:
        await register_pack_cards(user_id=user_id, chat_id=chat_id, result=result)
        await log_pack_event(
            user_id=user_id,
            chat_id=chat_id,
            category=result.category,
            best_card=result.best_card,
            price=result.best_price,
            pack_type="quiz_free",
            pack_count=1,
            card_count=len(result.cards),
            pool_source=result.pool_source,
            source="quiz_correct",
        )
    except Exception as exc:
        logger.debug("Quiz free pack grant log skipped user=%s: %s", user_id, exc)
    return result


async def _grant_premium_pack(user_id: int, chat_id: int):
    result = await open_pack(user_id=user_id, category_key="pokemon_tcg", pack_type="premium", count=1)
    try:
        await register_pack_cards(user_id=user_id, chat_id=chat_id, result=result)
        await log_pack_event(
            user_id=user_id,
            chat_id=chat_id,
            category=result.category,
            best_card=result.best_card,
            price=result.best_price,
            pack_type="quiz_premium",
            pack_count=1,
            card_count=len(result.cards),
            pool_source=result.pool_source,
            source="quiz_jackpot",
        )
    except Exception as exc:
        logger.debug("Quiz premium pack grant log skipped user=%s: %s", user_id, exc)
    return result


async def _announce_jackpot(context, chat_id: int, winner: dict, result) -> None:
    winner_name = escape(str(winner.get("display_name") or winner["user_id"]))
    caption = (
        "💎 <b>Premium Pack jackpot opened</b>\n"
        "------------\n"
        f"Winner: <b>{winner_name}</b>\n"
        f"Best pull: <b>{escape(result.best_card.card_name)}</b> {escape(result.best_card.grade)}"
    )
    if result.best_price.fmv_usd is not None:
        caption += f"\nMarket Value: <b>${result.best_price.fmv_usd:,.2f}</b>"

    try:
        image_bytes = await render_overlay_card(result.best_card, result.best_price)
    except Exception:
        image_bytes = None
    try:
        if image_bytes:
            photo = BytesIO(image_bytes)
            photo.name = "renaiss_quiz_jackpot.png"
            await context.bot.send_photo(chat_id=chat_id, photo=photo, caption=caption, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Quiz jackpot announce failed: %s", exc)


async def _render_reveal_card(round_data: dict) -> bytes | None:
    """정답 공개용 슬랩 라벨 이미지 (이제는 가격을 보여줘도 된다)."""
    card = CardIdentity(
        category=str(round_data.get("category") or "pokemon_tcg"),
        card_name=str(round_data.get("card_name") or "-"),
        image_url=round_data.get("card_image_url"),
    )
    price = RenaissPrice(
        status="candidate",
        source=str(round_data.get("price_source") or "renaiss-index-api"),
        fmv_usd=float(round_data.get("correct_price_usd") or 0),
        image_url=round_data.get("card_image_url"),
        referral_url=round_data.get("referral_url"),
    )
    try:
        return await render_overlay_card(card, price)
    except Exception as exc:
        logger.debug("Quiz reveal render skipped: %s", exc)
        return None
