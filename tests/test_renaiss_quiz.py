"""Renaiss 데일리 퀴즈 + 그레이딩 프리미엄 단위 테스트 (DB/API 불필요)."""

from __future__ import annotations

from pathlib import Path
import random

import pytest

from renaiss_bot.handlers.quiz import _quiz_text
from renaiss_bot.services.features import daily_quiz_enabled
from renaiss_bot.services.grading import (
    compute_grading_premium,
    format_grading_premium,
    is_raw_offer,
)
from renaiss_bot.services.pack_rules import (
    DAILY_FREE_PACKS,
    EXTRA_FREE_PACK_RP,
    PREMIUM_PACK_RP,
    plan_pack_open,
)
from renaiss_bot.services.models import CardIdentity, GradeOffer, RenaissPrice, card_identity_key
from datetime import date, datetime, timezone

from renaiss_bot.services.quiz import (
    build_price_options,
    compute_streak,
    format_distribution,
    format_price_option,
    pick_quiz_subject,
    round_price,
)


def test_quiz_prompt_has_no_pack_or_currency_reward_by_default(monkeypatch):
    monkeypatch.delenv("RENAISS_PACK_ECONOMY_ENABLED", raising=False)
    text = _quiz_text("Charizard", 120, 1)
    assert "free pack" not in text
    assert "Premium Pack" not in text
    assert "RP" not in text


def test_separate_daily_quiz_cannot_be_reenabled(monkeypatch):
    monkeypatch.delenv("RENAISS_DAILY_QUIZ_ENABLED", raising=False)
    assert not daily_quiz_enabled()
    monkeypatch.setenv("RENAISS_DAILY_QUIZ_ENABLED", "1")
    assert not daily_quiz_enabled()


def test_retired_quiz_has_no_runtime_handler_or_scheduler():
    root = Path(__file__).resolve().parents[1] / "renaiss_bot"
    register_source = (root / "handlers" / "register.py").read_text(encoding="utf-8")
    jobs_source = (root / "jobs.py").read_text(encoding="utf-8")
    main_source = (root / "main.py").read_text(encoding="utf-8")

    assert "renaiss:quiz:" not in register_source
    assert "post_daily_quiz" not in jobs_source
    assert "recover_open_quiz_rounds" not in jobs_source
    assert "recover_open_quiz_rounds" not in main_source


async def test_quiz_skips_catalog_fallback_price(monkeypatch):
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_code="BS",
        collector_number="4/102",
        market_price_usd=95,
    )

    async def pool(user_id, category):
        return [card], "catalog"

    async def catalog_price(candidate):
        return RenaissPrice(
            status="candidate",
            source="catalog-fallback",
            fmv_usd=95,
        )

    monkeypatch.setattr("renaiss_bot.services.quiz.load_card_pool", pool)
    monkeypatch.setattr("renaiss_bot.services.quiz.fetch_price", catalog_price)
    assert await pick_quiz_subject() is None


async def test_quiz_accepts_only_verified_multi_source_price(monkeypatch):
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_code="BS",
        collector_number="4/102",
        market_price_usd=95,
    )

    async def pool(user_id, category):
        return [card], "catalog"

    async def exact_price(candidate):
        return RenaissPrice(
            status="exact",
            source="renaiss-index-api",
            fmv_usd=95,
            confidence="high",
            confidence_score=0.9,
            source_count=3,
            valuation_method="median",
            asset_url="https://index.renaissos.com/cards/charizard",
                price_updated_at=datetime.now(timezone.utc),
                source_identity_key=card_identity_key(candidate),
        )

    monkeypatch.setattr("renaiss_bot.services.quiz.load_card_pool", pool)
    monkeypatch.setattr("renaiss_bot.services.quiz.fetch_price", exact_price)
    subject = await pick_quiz_subject()
    assert subject is not None and subject.correct_price_usd == 95


# ── 퀴즈 보기 생성 ─────────────────────────────────────────────


@pytest.mark.parametrize("correct", [5.0, 12.5, 45.0, 128.0, 390.85, 4200.0])
def test_build_price_options_contains_correct_answer(correct):
    options, correct_index = build_price_options(correct, rng=random.Random(42))
    assert len(options) == 4
    assert options[correct_index] == round_price(correct)


@pytest.mark.parametrize("correct", [5.0, 45.0, 390.85, 4200.0])
def test_build_price_options_are_distinct(correct):
    for seed in range(20):
        options, _ = build_price_options(correct, rng=random.Random(seed))
        assert len(set(options)) == 4, f"seed={seed} produced duplicates: {options}"


def test_build_price_options_all_positive():
    for seed in range(20):
        options, _ = build_price_options(7.3, rng=random.Random(seed))
        assert all(value > 0 for value in options)


def test_build_price_options_shuffled_by_seed():
    positions = {
        build_price_options(100.0, rng=random.Random(seed))[1] for seed in range(30)
    }
    # 정답 위치가 항상 같으면 유저가 패턴을 외운다.
    assert len(positions) > 1


def test_round_price_bands():
    assert round_price(7.34) == 7.3
    assert round_price(45.6) == 46.0
    assert round_price(391.0) == 390.0
    assert round_price(4211.0) == 4200.0
    assert round_price(0) == 0.0


def test_format_price_option():
    assert format_price_option(7.3) == "$7.3"
    assert format_price_option(390.0) == "$390"
    assert format_price_option(4200.0) == "$4,200"


# ── 팩 게이트 (일일 무료 + RP) ──────────────────────────────────


def test_plan_free_within_quota():
    plan = plan_pack_open("free", 3, free_used_today=0, rp_balance=0)
    assert plan.allowed_count == 3
    assert plan.rp_cost == 0
    assert plan.error is None


def test_plan_free_clamped_to_remaining():
    plan = plan_pack_open("free", 10, free_used_today=DAILY_FREE_PACKS - 2, rp_balance=0)
    assert plan.allowed_count == 2


def test_plan_free_exhausted():
    plan = plan_pack_open("free", 1, free_used_today=DAILY_FREE_PACKS, rp_balance=10_000)
    assert plan.error == "no_free_left"
    assert plan.allowed_count == 0


def test_plan_free_with_rp():
    plan = plan_pack_open(
        "free", 3, free_used_today=DAILY_FREE_PACKS, rp_balance=EXTRA_FREE_PACK_RP * 2, pay_with_rp=True
    )
    assert plan.allowed_count == 2
    assert plan.rp_cost == EXTRA_FREE_PACK_RP * 2
    assert plan.error is None


def test_plan_rp_insufficient():
    plan = plan_pack_open("free", 1, free_used_today=0, rp_balance=EXTRA_FREE_PACK_RP - 1, pay_with_rp=True)
    assert plan.error == "insufficient_rp"


def test_plan_premium_costs_rp():
    plan = plan_pack_open("premium", 2, free_used_today=0, rp_balance=PREMIUM_PACK_RP * 2)
    assert plan.allowed_count == 2
    assert plan.rp_cost == PREMIUM_PACK_RP * 2


def test_plan_premium_insufficient_rp():
    plan = plan_pack_open("premium", 1, free_used_today=0, rp_balance=PREMIUM_PACK_RP - 1)
    assert plan.error == "insufficient_rp"


def test_plan_premium_ignores_free_quota():
    plan = plan_pack_open("premium", 1, free_used_today=DAILY_FREE_PACKS, rp_balance=PREMIUM_PACK_RP)
    assert plan.allowed_count == 1
    assert plan.error is None


# ── 스트릭 ─────────────────────────────────────────────────────


def test_compute_streak_consecutive():
    today = date(2026, 7, 7)
    dates = {date(2026, 7, 7), date(2026, 7, 6), date(2026, 7, 5)}
    assert compute_streak(dates, today) == 3


def test_compute_streak_zero_without_today():
    today = date(2026, 7, 7)
    dates = {date(2026, 7, 6), date(2026, 7, 5)}
    assert compute_streak(dates, today) == 0


def test_compute_streak_gap_resets():
    today = date(2026, 7, 7)
    dates = {date(2026, 7, 7), date(2026, 7, 5), date(2026, 7, 4)}
    assert compute_streak(dates, today) == 1


def test_compute_streak_empty():
    assert compute_streak(set(), date(2026, 7, 7)) == 0


# ── 정답 분포 그리드 ─────────────────────────────────────────────


def test_format_distribution_marks_correct_option():
    lines = format_distribution([45.0, 390.0, 120.0, 800.0], [1, 3, 2, 0], correct_index=1)
    assert len(lines) == 4
    assert "✅" in lines[1]
    assert "🟩" in lines[1]
    assert "✅" not in lines[0]
    assert "⬜" in lines[0]


def test_format_distribution_zero_votes_no_bar():
    lines = format_distribution([45.0, 390.0], [0, 5], correct_index=1)
    assert "⬜" not in lines[0] and "🟩" not in lines[0]
    assert lines[0].endswith("0")


def test_format_distribution_bar_capped():
    lines = format_distribution([45.0, 390.0], [100, 1], correct_index=0, max_bar=8)
    assert lines[0].count("🟩") <= 8


# ── 그레이딩 프리미엄 ───────────────────────────────────────────


def _offer(grade=None, company=None, fmv=0.0):
    return GradeOffer(grade_label=grade, grading_company=company, fmv_usd=fmv)


def test_is_raw_offer():
    assert is_raw_offer(_offer(fmv=45.0))
    assert is_raw_offer(_offer(grade="RAW", fmv=45.0))
    assert is_raw_offer(_offer(grade="Ungraded", fmv=45.0))
    assert not is_raw_offer(_offer(grade="10", company="PSA", fmv=390.0))
    assert not is_raw_offer(_offer(grade="9.5", company="BGS", fmv=210.0))


def test_compute_grading_premium_basic():
    offers = [
        _offer(fmv=45.0),
        _offer(grade="10", company="PSA", fmv=390.0),
        _offer(grade="9", company="PSA", fmv=120.0),
    ]
    premium = compute_grading_premium(offers)
    assert premium is not None
    assert premium.raw_usd == 45.0
    assert premium.graded_usd == 390.0
    assert premium.graded_label == "PSA 10"
    assert premium.multiplier == pytest.approx(390.0 / 45.0)


def test_compute_grading_premium_needs_both_sides():
    assert compute_grading_premium([_offer(fmv=45.0)]) is None
    assert compute_grading_premium([_offer(grade="10", company="PSA", fmv=390.0)]) is None
    assert compute_grading_premium([]) is None


def test_compute_grading_premium_skips_inverted_prices():
    # 그레이드 가격이 RAW보다 낮으면 (베타 데이터 노이즈) 표시하지 않는다.
    offers = [
        _offer(fmv=100.0),
        _offer(grade="8", company="PSA", fmv=60.0),
    ]
    assert compute_grading_premium(offers) is None


def test_format_grading_premium():
    offers = [
        _offer(fmv=45.0),
        _offer(grade="10", company="PSA", fmv=390.0),
    ]
    premium = compute_grading_premium(offers)
    text = format_grading_premium(premium)
    assert "RAW $45.00" in text
    assert "PSA 10 $390.00" in text
    assert "8.7x" in text
