"""Renaiss 스폰 시세 밴드 단위 테스트 (DB/telegram 불필요)."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import Application

from renaiss_bot.database.queries import (
    FirstCStarterGrant,
    SpawnAwardConflict,
    SpawnAwardResult,
    award_spawn_card,
    grant_first_c_starter,
)
from renaiss_bot.database.event_queries import (
    SpawnDispatchReservation,
    _hour_in_quiet_window,
    list_unfinished_spawns,
)
from renaiss_bot.handlers.register import register_handlers
from renaiss_bot.handlers.spawn import (
    ActiveSpawn,
    _active,
    _assign_cohort_variant,
    _catalog_reference_price,
    _first_c_feedback_users,
    _guess_distribution_lines,
    _guess_keyboard,
    _price_summary_line,
    _resolve,
    _spawning,
    _spawn_event_metadata,
    _spawn_text,
    catch_handler,
    next_spawn_delay,
    spawn_tick,
    spawn_interval_bounds,
    spawn_daily_cap,
    catch_window_seconds,
    spawn_quiet_hours,
    first_spawn_delay,
)
from renaiss_bot.jobs import recover_unfinished_spawns, spawn_loop_job
from renaiss_bot.services.market import market_card_eligible
from renaiss_bot.services.models import CardIdentity, RenaissPrice, card_identity_key
from renaiss_bot.services.spawn import (
    BAND_MIN_USD,
    Spawn,
    cards_in_band,
    pick_spawn_card,
    roll_band,
)


def _card(name, usd):
    return CardIdentity(category="pokemon_tcg", card_name=name, grade="R", market_price_usd=usd)


def _allow_spawn_dispatch(monkeypatch):
    reserve = AsyncMock(
        return_value=SpawnDispatchReservation(True, "acquired", "2026-07-11", 1)
    )
    release = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.reserve_spawn_dispatch", reserve)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.release_spawn_dispatch", release)
    return reserve, release


POOL = [
    _card("cheap-a", 5.0),
    _card("cheap-b", 40.0),
    _card("mid-a", 120.0),
    _card("mid-b", 300.0),
    _card("grail-a", 600.0),
    _card("grail-b", 1200.0),
]


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _AwardConnection:
    def __init__(self, fetchrows):
        self.fetchrow = AsyncMock(side_effect=fetchrows)
        self.execute = AsyncMock(return_value="INSERT 0 1")

    def transaction(self):
        return _AsyncContext(self)


class _AwardPool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


async def test_restart_scan_keeps_uncertain_award_failure_reconcilable(monkeypatch):
    connection = SimpleNamespace(fetch=AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "renaiss_bot.database.event_queries.get_db",
        AsyncMock(return_value=_AwardPool(connection)),
    )

    assert await list_unfinished_spawns() == []

    sql = connection.fetch.await_args.args[0]
    assert "'spawn_award_failed'" not in sql
    assert '"prompt_closed": true' in sql


async def test_first_c_starter_ledger_and_card_write_share_one_transaction(monkeypatch):
    connection = _AwardConnection([{"id": 91}, {"user_id": 55}])
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_AwardPool(connection)),
    )

    grant = await grant_first_c_starter(user_id=55, chat_id=-1001)

    assert grant == FirstCStarterGrant(event_id=91, created=True)
    event_sql = connection.fetchrow.await_args_list[0].args[0]
    card_sql = connection.fetchrow.await_args_list[1].args[0]
    assert "'first_c_starter_granted'" in event_sql
    assert "ON CONFLICT (event_key) DO NOTHING" in event_sql
    assert "market_price_usd" in card_sql
    assert "NULL,NULL,1,TRUE" in card_sql
    assert "is_tutorial" in card_sql
    assert "DO UPDATE" not in card_sql


def test_cards_in_band_common():
    got = {c.card_name for c in cards_in_band(POOL, "common")}
    assert got == {"cheap-a", "cheap-b"}


def test_cards_in_band_rare():
    got = {c.card_name for c in cards_in_band(POOL, "rare")}
    assert got == {"mid-a", "mid-b"}


def test_cards_in_band_grail():
    got = {c.card_name for c in cards_in_band(POOL, "grail")}
    assert got == {"grail-a", "grail-b"}


def test_bands_are_disjoint_and_cover():
    all_banded = []
    for band in ("common", "rare", "grail"):
        all_banded += [c.card_name for c in cards_in_band(POOL, band)]
    assert sorted(all_banded) == sorted(c.card_name for c in POOL)


def test_band_thresholds():
    assert BAND_MIN_USD["common"] == 0.0
    assert BAND_MIN_USD["rare"] == 100.0
    assert BAND_MIN_USD["grail"] == 500.0


def test_pick_spawn_card_in_band():
    rng = random.Random(1)
    card = pick_spawn_card(POOL, "grail", rng=rng)
    assert card is not None and card.market_price_usd >= 500


def test_pick_spawn_card_fallback_when_band_empty():
    # grail 없는 풀 → rare 로 폴백
    pool = [_card("only-mid", 150.0)]
    card = pick_spawn_card(pool, "grail", rng=random.Random(0))
    assert card is not None and card.card_name == "only-mid"


def test_roll_band_rush_shifts_toward_rare():
    # 통계적으로 rush 가 레어+그레일 비중을 높인다
    rng = random.Random(42)
    normal = [roll_band(rush=False, rng=rng) for _ in range(3000)]
    rush = [roll_band(rush=True, rng=rng) for _ in range(3000)]
    normal_rare = sum(1 for b in normal if b != "common")
    rush_rare = sum(1 for b in rush if b != "common")
    assert rush_rare > normal_rare


def test_roll_band_returns_valid():
    rng = random.Random(7)
    for _ in range(100):
        assert roll_band(rng=rng) in {"common", "rare", "grail"}


async def test_spawn_award_commits_event_card_and_pack_in_one_transaction(monkeypatch):
    connection = _AwardConnection([{"id": 77}])
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_AwardPool(connection)),
    )
    card = _card("winner-card", 60.0)

    result = await award_spawn_card(
        award_key="spawn:-1001:42:award",
        spawn_token="token-1",
        user_id=123,
        winner_name="Rookie",
        chat_id=-1001,
        category="pokemon_tcg",
        card=card,
        price=RenaissPrice(status="candidate", source="catalog", fmv_usd=60),
    )

    assert result.created and result.event_id == 77
    assert connection.execute.await_count == 2
    statements = [call.args[0] for call in connection.execute.await_args_list]
    assert "INSERT INTO renaiss_user_cards" in statements[0]
    assert "INSERT INTO renaiss_pack_events" in statements[1]


async def test_spawn_award_stores_the_verified_reveal_snapshot(monkeypatch):
    connection = _AwardConnection([{"id": 78}])
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_AwardPool(connection)),
    )
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        grade="RAW",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        local_card_id="catalog:pokemon_tcg:charizard",
        market_price_usd=900,
    )
    price = RenaissPrice(
        status="exact",
        source="renaiss-index-api:item-by-no",
        fmv_usd=20,
        confidence="high",
        confidence_score=0.95,
        source_count=3,
        valuation_method="median",
        asset_url="https://www.renaiss.xyz/assets/charizard",
        price_updated_at=datetime.now(timezone.utc),
        source_identity_key=card_identity_key(card),
    )

    await award_spawn_card(
        award_key="spawn:-1001:43:award",
        spawn_token="token-2",
        user_id=123,
        winner_name="Rookie",
        chat_id=-1001,
        category="pokemon_tcg",
        card=card,
        price=price,
    )

    card_write = connection.execute.await_args_list[0]
    assert card_write.args[9] == 20
    assert "market_price_usd = COALESCE" in card_write.args[0]


async def test_spawn_award_retry_is_idempotent(monkeypatch):
    existing = {
        "id": 77,
        "event_name": "catch_won",
        "user_id": 123,
        "chat_id": -1001,
        "session_id": "token-1",
        "metadata": {
            "category": "pokemon_tcg",
            "local_card_id": "pokemon_tcg:winner-card:R",
        },
    }
    connection = _AwardConnection([None, existing])
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_AwardPool(connection)),
    )

    result = await award_spawn_card(
        award_key="spawn:-1001:42:award",
        spawn_token="token-1",
        user_id=123,
        winner_name="Rookie",
        chat_id=-1001,
        category="pokemon_tcg",
        card=_card("winner-card", 60.0),
        price=RenaissPrice(status="candidate", source="catalog", fmv_usd=60),
    )

    assert not result.created and result.event_id == 77
    connection.execute.assert_not_awaited()


async def test_spawn_award_rejects_reused_key_for_another_winner(monkeypatch):
    existing = {
        "id": 77,
        "event_name": "catch_won",
        "user_id": 999,
        "chat_id": -1001,
        "session_id": "token-1",
        "metadata": {
            "category": "pokemon_tcg",
            "local_card_id": "pokemon_tcg:winner-card:R",
        },
    }
    connection = _AwardConnection([None, existing])
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_AwardPool(connection)),
    )

    with pytest.raises(SpawnAwardConflict):
        await award_spawn_card(
            award_key="spawn:-1001:42:award",
            spawn_token="token-1",
            user_id=123,
            winner_name="Rookie",
            chat_id=-1001,
            category="pokemon_tcg",
            card=_card("winner-card", 60.0),
            price=RenaissPrice(status="candidate", source="catalog", fmv_usd=60),
        )


async def test_spawn_award_write_failure_propagates(monkeypatch):
    connection = _AwardConnection([{"id": 77}])
    connection.execute.side_effect = ["INSERT 0 1", RuntimeError("pack write failed")]
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_AwardPool(connection)),
    )

    with pytest.raises(RuntimeError, match="pack write failed"):
        await award_spawn_card(
            award_key="spawn:-1001:42:award",
            spawn_token="token-1",
            user_id=123,
            winner_name="Rookie",
            chat_id=-1001,
            category="pokemon_tcg",
            card=_card("winner-card", 60.0),
            price=RenaissPrice(status="candidate", source="catalog", fmv_usd=60),
        )


async def test_spawn_award_guard_failure_propagates_without_card_write(monkeypatch):
    connection = _AwardConnection([RuntimeError("event ledger unavailable")])
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_AwardPool(connection)),
    )

    with pytest.raises(RuntimeError, match="event ledger unavailable"):
        await award_spawn_card(
            award_key="spawn:-1001:42:award",
            spawn_token="token-1",
            user_id=123,
            winner_name="Rookie",
            chat_id=-1001,
            category="pokemon_tcg",
            card=_card("winner-card", 60.0),
            price=RenaissPrice(status="candidate", source="catalog", fmv_usd=60),
        )
    connection.execute.assert_not_awaited()


def test_normal_spawn_cadence_is_randomized_between_five_and_ten_minutes(monkeypatch):
    monkeypatch.delenv("RENAISS_SPAWN_INTERVAL_SECONDS", raising=False)
    monkeypatch.setenv("RENAISS_SPAWN_INTERVAL_MIN_SECONDS", "300")
    monkeypatch.setenv("RENAISS_SPAWN_INTERVAL_MAX_SECONDS", "600")
    assert spawn_interval_bounds() == (300, 600)
    delays = {next_spawn_delay(rng=random.Random(seed)) for seed in range(20)}
    assert min(delays) >= 300 and max(delays) <= 600
    assert len(delays) > 1


def test_first_spawn_uses_short_bounded_warmup(monkeypatch):
    monkeypatch.setenv("RENAISS_FIRST_SPAWN_DELAY_SECONDS", "60")
    assert first_spawn_delay() == 60
    monkeypatch.setenv("RENAISS_FIRST_SPAWN_DELAY_SECONDS", "9999")
    assert first_spawn_delay() == 300


def test_spawn_noise_guards_have_safe_defaults_and_wrap_kst(monkeypatch):
    monkeypatch.delenv("RENAISS_SPAWN_DAILY_CAP", raising=False)
    monkeypatch.delenv("RENAISS_SPAWN_QUIET_START_HOUR_KST", raising=False)
    monkeypatch.delenv("RENAISS_SPAWN_QUIET_END_HOUR_KST", raising=False)
    assert spawn_daily_cap() == 6
    assert spawn_quiet_hours() == (0, 9)
    assert _hour_in_quiet_window(3, 0, 9)
    assert not _hour_in_quiet_window(9, 0, 9)
    assert _hour_in_quiet_window(23, 22, 7)
    assert _hour_in_quiet_window(6, 22, 7)
    assert not _hour_in_quiet_window(12, 22, 7)
    assert not _hour_in_quiet_window(3, 3, 3)


def test_season1_high_exposure_profile_is_supported(monkeypatch):
    monkeypatch.delenv("RENAISS_SPAWN_INTERVAL_SECONDS", raising=False)
    monkeypatch.setenv("RENAISS_SPAWN_CATCH_WINDOW_SECONDS", "20")
    monkeypatch.setenv("RENAISS_SPAWN_INTERVAL_MIN_SECONDS", "30")
    monkeypatch.setenv("RENAISS_SPAWN_INTERVAL_MAX_SECONDS", "30")
    monkeypatch.setenv("RENAISS_SPAWN_DAILY_CAP", "2880")

    assert catch_window_seconds() == 20
    assert spawn_interval_bounds() == (30, 30)
    assert spawn_daily_cap() == 2880


def test_spawn_interval_cannot_overlap_the_catch_window(monkeypatch):
    monkeypatch.delenv("RENAISS_SPAWN_INTERVAL_SECONDS", raising=False)
    monkeypatch.setenv("RENAISS_SPAWN_CATCH_WINDOW_SECONDS", "40")
    monkeypatch.setenv("RENAISS_SPAWN_INTERVAL_MIN_SECONDS", "30")
    monkeypatch.setenv("RENAISS_SPAWN_INTERVAL_MAX_SECONDS", "30")

    assert spawn_interval_bounds() == (45, 45)


async def test_spawn_loop_reschedules_after_failed_tick(monkeypatch):
    async def fail_tick(context, **kwargs):
        raise RuntimeError("temporary failure")

    queue = SimpleNamespace(run_once=lambda *args, **kwargs: calls.append((args, kwargs)))
    calls = []
    monkeypatch.setattr("renaiss_bot.jobs.spawn_tick", fail_tick)
    monkeypatch.setattr("renaiss_bot.jobs.next_spawn_delay", lambda: 420)
    try:
        await spawn_loop_job(SimpleNamespace(job_queue=queue))
    except RuntimeError:
        pass
    assert calls[0][1]["when"] == 420


def test_registration_keeps_catch_and_removes_legacy_drop_flow():
    app = Application.builder().token("123456:TEST_TOKEN").build()
    register_handlers(app)
    callbacks = {
        handler.callback.__name__
        for handlers in app.handlers.values()
        for handler in handlers
    }
    assert "catch_handler" in callbacks
    assert "schedule_group_command_delete" in callbacks
    assert "cmd_market" in callbacks
    assert "on_market" in callbacks
    assert "cmd_rank" not in callbacks
    assert "cmd_sell" not in callbacks
    assert "on_sell" not in callbacks
    assert "call_drop_handler" not in callbacks
    assert "feed_drop_handler" not in callbacks


def _active_blind_spawn() -> ActiveSpawn:
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="grail-a",
        grade="R",
        set_code="BS",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        market_price_usd=600.0,
    )
    return ActiveSpawn(
        chat_id=-1001,
        spawn=Spawn(card=card, band="grail", market_usd=600.0),
        started_at=monotonic(),
        price_options=[180.0, 360.0, 600.0, 960.0],
        correct_price_index=2,
        token="deadbeef",
    )


def test_spawn_prompt_hides_fmv_and_value_band():
    text = _spawn_text(_active_blind_spawn())
    assert "$600" not in text
    assert "GRAIL" not in text
    assert "Type <code>c</code> to catch!" in text
    assert "Base Set #4/102 · English" in text


def test_cohort_experiment_is_off_by_default(monkeypatch):
    monkeypatch.delenv("RENAISS_COHORT_EXPERIMENT_ENABLED", raising=False)
    monkeypatch.delenv("RENAISS_COHORT_EXPERIMENT_SALT", raising=False)
    assert _assign_cohort_variant(spawn_token="round-a", guess_capable=True) == (None, None)


def test_cohort_assignment_is_deterministic_and_only_for_guess_capable_rounds(monkeypatch):
    monkeypatch.setenv("RENAISS_COHORT_EXPERIMENT_ENABLED", "1")
    monkeypatch.setenv("RENAISS_COHORT_EXPERIMENT_SALT", "pilot-secret-at-least-16")

    first = _assign_cohort_variant(spawn_token="round-a", guess_capable=True)
    second = _assign_cohort_variant(spawn_token="round-a", guess_capable=True)

    assert first == second
    assert first[0].startswith("first-c-insight-v1-")
    assert first[1] in {"catch-only", "insight-layer"}
    assert _assign_cohort_variant(spawn_token="round-a", guess_capable=False) == (None, None)


def test_catch_only_assignment_suppresses_guess_ui():
    active = _active_blind_spawn()
    active.guess_capable = True
    active.assignment_id = "first-c-insight-v1-example"
    active.variant = "catch-only"
    active.price_options = []
    active.correct_price_index = None

    assert _guess_keyboard(active) is None
    # Catch-only rounds show no guess CTA and no price options
    assert "Guess the price" not in _spawn_text(active)


def test_spawn_event_metadata_persists_assignment_and_actual_treatment():
    active = _active_blind_spawn()
    active.message_id = 42
    active.guess_capable = True
    active.assignment_id = "first-c-insight-v1-example"
    active.variant = "insight-layer"

    metadata = _spawn_event_metadata(active)

    assert metadata["guess_capable"] is True
    assert metadata["has_price_guess"] is True
    assert metadata["experiment_name"] == "first-c-insight-v1"
    assert metadata["assignment_id"] == "first-c-insight-v1-example"
    assert metadata["variant"] == "insight-layer"


def test_spawn_guess_keyboard_has_four_bound_options():
    keyboard = _guess_keyboard(_active_blind_spawn())
    assert keyboard is not None
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert [button.text for button in buttons] == ["$180", "$360", "$600", "$960"]
    assert buttons[2].callback_data == "renaiss:spawn_guess:deadbeef:2"


def test_spawn_guess_distribution_reveals_correct_choice():
    active = _active_blind_spawn()
    active.guesses = {1: 2, 2: 0, 3: 2}
    lines = _guess_distribution_lines(active)
    assert any("$600" in line and "✅" in line and line.endswith("2") for line in lines)
    assert any("$180" in line and line.endswith("1") for line in lines)


def test_verified_reveal_shows_source_freshness_confidence_and_score_gate(monkeypatch):
    monkeypatch.setenv("RENAISS_DAILY_PICK_MIN_CONFIDENCE_SCORE", "0.5")
    monkeypatch.setenv("RENAISS_DAILY_PICK_MIN_SOURCE_COUNT", "2")
    now = datetime.now(timezone.utc)
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard ex",
        set_code="SV4a",
        collector_number="349/190",
        grade="RAW",
    )
    price = RenaissPrice(
        status="exact",
        source="renaiss-index-api:item-by-no",
        confidence="high",
        confidence_score=0.92,
        source_count=3,
        observation_count=12,
        valuation_method="median",
        fmv_usd=430,
        asset_url="https://www.renaiss.xyz/assets/charizard-ex",
        price_updated_at=now - timedelta(hours=2),
        source_identity_key=card_identity_key(card),
    )

    line = _price_summary_line(card, price, now=now)

    from renaiss_bot.services.emoji import icon as _icon
    assert line == f"{_icon('coin')} <b>$430</b> {_icon('check')} Renaiss FMV · 2h ago"


def test_price_summary_never_renders_untrusted_partner_confidence():
    card = _card("safe", 60.0)
    price = RenaissPrice(
        status="candidate",
        source="renaiss-index-api",
        confidence="<a href='https://evil.example'>high</a>",
        fmv_usd=60,
    )

    line = _price_summary_line(card, price)

    # The compact summary no longer surfaces the free-text confidence field.
    assert "evil.example" not in line
    assert "<a href=" not in line


def test_unverified_reveal_is_labeled_collection_only():
    card = _card("sample", 60.0)
    price = RenaissPrice(status="candidate", source="sample", fmv_usd=60.0)

    line = _price_summary_line(card, price)

    from renaiss_bot.services.emoji import icon as _icon
    assert line == f"{_icon('coin')} <b>$60</b> · 🧪 unverified"


def test_unpriced_spawn_does_not_prompt_for_missing_buttons():
    active = ActiveSpawn(
        chat_id=-1001,
        spawn=Spawn(card=_card("unknown", 0.0), band="common", market_usd=0.0),
        started_at=monotonic(),
    )
    assert "guess the market price below" not in _spawn_text(active)
    assert _guess_keyboard(active) is None


def test_catalog_estimate_without_exact_evidence_is_not_guess_eligible():
    spawn = Spawn(card=_card("unknown", 60.0), band="common", market_usd=60.0)
    price = _catalog_reference_price(spawn)
    assert price.source == "catalog"
    assert not market_card_eligible(spawn.card, price)


async def test_uncaught_spawn_still_reveals_price_and_guess_results():
    class FakeBot:
        def __init__(self):
            self.edits = []
            self.photos = []

        async def edit_message_text(self, **kwargs):
            self.edits.append(kwargs)

        async def send_photo(self, **kwargs):
            self.photos.append(kwargs)
            return SimpleNamespace(photo=[])

    bot = FakeBot()
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="mid-a",
        grade="RAW",
        set_code="BS",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        market_price_usd=60.0,
    )
    verified_price = RenaissPrice(
        status="exact",
        source="renaiss-index-api:item-by-no",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        fmv_usd=60.0,
        asset_url="https://www.renaiss.xyz/assets/mid-a",
        referral_url="https://www.renaiss.xyz/assets/mid-a",
        price_updated_at=datetime.now(timezone.utc),
    )
    active = ActiveSpawn(
        chat_id=-1001,
        message_id=42,
        spawn=Spawn(card=card, band="common", market_usd=60.0),
        started_at=monotonic(),
        verified_price=verified_price,
        price_options=[18.0, 36.0, 60.0, 96.0],
        correct_price_index=2,
        guesses={1: 2, 2: 0},
    )

    await _resolve(SimpleNamespace(bot=bot), active)

    # Every reveal now leads with the graded slab image; text lives in the caption.
    assert len(bot.photos) == 1
    text = bot.photos[0]["caption"]
    assert "<b>$60</b>" in text
    assert "Nobody caught it" in text
    assert "$60 ✅" in text
    # The original blind prompt is cleared after the photo reveal posts.
    assert len(bot.edits) == 1
    assert "result posted below" in bot.edits[0]["text"]


async def test_failed_spawn_award_never_announces_collection_success(monkeypatch):
    class FakeBot:
        def __init__(self):
            self.edits = []

        async def edit_message_text(self, **kwargs):
            self.edits.append(kwargs)

    active = _active_blind_spawn()
    active.message_id = 42
    active.catchers = {123: "Rookie"}
    bot = FakeBot()
    event = AsyncMock(return_value=True)
    award = AsyncMock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr("renaiss_bot.handlers.spawn.REVEAL_SUSPENSE_SECONDS", 0)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.award_spawn_card", award)
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.register_market_reveal",
        AsyncMock(return_value=7),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.build_tracked_url",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.render_overlay_card",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("renaiss_bot.handlers.spawn.log_event", event)

    await _resolve(SimpleNamespace(bot=bot), active)

    reveal_text = bot.edits[-1]["text"]
    assert "Caught by" not in reveal_text
    assert "added to their collection" not in reveal_text
    assert "award could not be verified" in reveal_text
    assert award.await_args.kwargs["award_key"] == "spawn:-1001:42:award"
    assert event.await_args.args[0] == "spawn_award_failed"
    assert event.await_args.kwargs["metadata"]["award_succeeded"] is False


async def test_spawn_award_response_loss_is_confirmed_with_same_idempotency_key(monkeypatch):
    class FakeBot:
        def __init__(self):
            self.edits = []

        async def edit_message_text(self, **kwargs):
            self.edits.append(kwargs)

    active = _active_blind_spawn()
    active.message_id = 42
    active.catchers = {123: "Rookie"}
    award = AsyncMock(
        side_effect=[
            RuntimeError("commit acknowledgement lost"),
            SpawnAwardResult(event_id=7, created=False),
        ]
    )
    monkeypatch.setattr("renaiss_bot.handlers.spawn.REVEAL_SUSPENSE_SECONDS", 0)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.award_spawn_card", award)
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.register_market_reveal",
        AsyncMock(return_value=7),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.build_tracked_url",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.render_overlay_card",
        AsyncMock(return_value=None),
    )
    event = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.log_event", event)

    await _resolve(SimpleNamespace(bot=FakeBot()), active)

    assert award.await_count == 2
    first_key = award.await_args_list[0].kwargs["award_key"]
    second_key = award.await_args_list[1].kwargs["award_key"]
    assert first_key == second_key == "spawn:-1001:42:award"
    assert event.await_args.args[0] == "spawn_revealed"
    assert event.await_args.kwargs["metadata"]["award_succeeded"] is True


async def test_first_c_outside_spawn_gets_one_time_guidance(monkeypatch):
    class FakeMessage:
        def __init__(self):
            self.replies = []

        async def reply_text(self, text, **kwargs):
            self.replies.append(text)

    message = FakeMessage()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001, type="group"),
        effective_user=SimpleNamespace(id=55, full_name="Rookie"),
        effective_message=message,
    )
    _active.clear()
    _first_c_feedback_users.clear()
    event = AsyncMock(return_value=True)
    starter = AsyncMock(
        side_effect=[
            FirstCStarterGrant(event_id=1, created=True),
            FirstCStarterGrant(event_id=1, created=False),
        ]
    )
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.log_event", event)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.grant_first_c_starter", starter)

    await catch_handler(update, SimpleNamespace(bot=SimpleNamespace()))
    await catch_handler(update, SimpleNamespace(bot=SimpleNamespace()))

    assert len(message.replies) == 1
    assert "First-c starter unlocked" in message.replies[0]
    assert "No spawn is active" in message.replies[0]
    assert event.await_count == 1
    assert starter.await_count == 2


async def test_first_c_guidance_survives_event_log_failure(monkeypatch):
    class FakeMessage:
        def __init__(self):
            self.replies = []

        async def reply_text(self, text, **kwargs):
            self.replies.append(text)

    message = FakeMessage()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001, type="group"),
        effective_user=SimpleNamespace(id=56, full_name="Returning Rookie"),
        effective_message=message,
    )
    _active.clear()
    _first_c_feedback_users.clear()
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.log_event",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.event_exists",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.grant_first_c_starter",
        AsyncMock(return_value=FirstCStarterGrant(event_id=1, created=False)),
    )

    await catch_handler(update, SimpleNamespace(bot=SimpleNamespace()))

    assert len(message.replies) == 1
    assert "No spawn is active" in message.replies[0]
    assert "starter unlocked" not in message.replies[0]


async def test_first_c_guidance_is_not_repeated_when_event_already_exists(monkeypatch):
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001, type="group"),
        effective_user=SimpleNamespace(id=58, full_name="Returning Rookie"),
        effective_message=message,
    )
    _active.clear()
    _first_c_feedback_users.clear()
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.log_event",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.event_exists",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.grant_first_c_starter",
        AsyncMock(return_value=FirstCStarterGrant(event_id=1, created=False)),
    )

    await catch_handler(update, SimpleNamespace(bot=SimpleNamespace()))

    message.reply_text.assert_not_awaited()


async def test_catch_after_deadline_is_never_added(monkeypatch):
    active = _active_blind_spawn()
    active.started_at = monotonic() - 41
    active.message_id = 42
    _active.clear()
    _active[-1001] = active
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001, type="group"),
        effective_user=SimpleNamespace(id=57, full_name="Late Trainer"),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
    )
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.log_event",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.grant_first_c_starter",
        AsyncMock(return_value=FirstCStarterGrant(event_id=1, created=False)),
    )

    await catch_handler(update, SimpleNamespace(bot=bot))

    assert active.catchers == {}
    bot.edit_message_text.assert_not_awaited()
    _active.clear()


async def test_active_catch_survives_starter_database_failure(monkeypatch):
    active = _active_blind_spawn()
    active.message_id = 42
    _active.clear()
    _active[-1001] = active
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001, type="group"),
        effective_user=SimpleNamespace(id=58, full_name="New Trainer"),
        effective_message=message,
    )
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.grant_first_c_starter",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr("renaiss_bot.handlers.spawn.log_event", AsyncMock(return_value=False))

    await catch_handler(update, SimpleNamespace(bot=bot))

    assert active.catchers[58] == "New Trainer"
    bot.edit_message_text.assert_awaited_once()
    # A starter-write failure must not block the entry receipt; the only reply
    # is the draw confirmation, never a starter/welcome message.
    message.reply_text.assert_awaited_once()
    receipt_text = message.reply_text.await_args.args[0]
    assert "opened a pack" in receipt_text
    assert "Welcome Card" not in receipt_text
    _active.clear()


async def test_active_catch_does_not_wait_for_stalled_starter_database(monkeypatch):
    active = _active_blind_spawn()
    active.message_id = 42
    _active.clear()
    _active[-1001] = active
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001, type="group"),
        effective_user=SimpleNamespace(id=59, full_name="Fast Trainer"),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
    )
    bot = SimpleNamespace(edit_message_text=AsyncMock())

    async def stalled_starter(**kwargs):
        await asyncio.Event().wait()

    monkeypatch.setenv("RENAISS_STARTER_GRANT_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.grant_first_c_starter", stalled_starter)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.log_event", AsyncMock(return_value=True))

    await asyncio.wait_for(catch_handler(update, SimpleNamespace(bot=bot)), timeout=0.2)

    assert active.catchers[59] == "Fast Trainer"
    bot.edit_message_text.assert_awaited_once()
    _active.clear()


async def test_concurrent_spawn_ticks_post_only_one_prompt(monkeypatch):
    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)
            await asyncio.sleep(0)
            return SimpleNamespace(message_id=42)

    class FakeJobQueue:
        def __init__(self):
            self.jobs = []

        def run_once(self, *args, **kwargs):
            self.jobs.append((args, kwargs))

    _active.clear()
    _spawning.clear()
    reserve, release = _allow_spawn_dispatch(monkeypatch)
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        market_price_usd=60,
    )
    roll = AsyncMock(return_value=Spawn(card=card, band="common", market_usd=60))
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.roll_spawn", roll)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.log_event", AsyncMock(return_value=True))
    bot = FakeBot()
    context = SimpleNamespace(bot=bot, job_queue=FakeJobQueue())

    await asyncio.gather(spawn_tick(context), spawn_tick(context))

    assert roll.await_count == 1
    assert len(bot.sent) == 1
    assert len(context.job_queue.jobs) == 1
    reserve.assert_awaited_once()
    release.assert_not_awaited()
    _active.clear()
    _spawning.clear()


async def test_spawn_without_persistent_recovery_anchor_is_cancelled(monkeypatch):
    class FakeBot:
        def __init__(self):
            self.edit_message_text = AsyncMock()

        async def send_message(self, **kwargs):
            return SimpleNamespace(message_id=42)

    queue = SimpleNamespace(run_once=AsyncMock())
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        market_price_usd=60,
    )
    _active.clear()
    _spawning.clear()
    _, release = _allow_spawn_dispatch(monkeypatch)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.roll_spawn",
        AsyncMock(return_value=Spawn(card=card, band="common", market_usd=60)),
    )
    monkeypatch.setattr("renaiss_bot.handlers.spawn.log_event", AsyncMock(return_value=False))
    bot = FakeBot()

    await spawn_tick(SimpleNamespace(bot=bot, job_queue=queue))

    bot.edit_message_text.assert_awaited_once()
    queue.run_once.assert_not_called()
    assert -1001 not in _active
    release.assert_awaited_once()


async def test_spawn_dispatch_gate_blocks_before_pool_or_telegram(monkeypatch):
    _active.clear()
    _spawning.clear()
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn.reserve_spawn_dispatch",
        AsyncMock(
            return_value=SpawnDispatchReservation(False, "daily_cap", "2026-07-11", 6)
        ),
    )
    roll = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.spawn.roll_spawn", roll)
    bot = SimpleNamespace(send_message=AsyncMock())

    await spawn_tick(SimpleNamespace(bot=bot, job_queue=SimpleNamespace()))

    roll.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_restart_recovery_closes_orphaned_prompt(monkeypatch):
    class FakeBot:
        def __init__(self):
            self.edits = []

        async def edit_message_text(self, **kwargs):
            self.edits.append(kwargs)

    bot = FakeBot()
    event = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "renaiss_bot.jobs.list_unfinished_spawns",
        AsyncMock(
            return_value=[
                {
                    "posted_event_id": 101,
                    "session_id": "deadbeef",
                    "chat_id": -1001,
                    "metadata": {"message_id": 42},
                }
            ]
        ),
    )
    monkeypatch.setattr("renaiss_bot.jobs.log_event", event)

    await recover_unfinished_spawns(SimpleNamespace(bot=bot))

    assert bot.edits[0]["message_id"] == 42
    assert "round closed after a bot restart" in bot.edits[0]["text"]
    assert "No collection award was recorded" in bot.edits[0]["text"]
    assert event.await_args.args[0] == "spawn_cancelled"
    assert event.await_args.kwargs["metadata"]["prompt_closed"] is True


async def test_restart_recovery_preserves_committed_spawn_award(monkeypatch):
    class FakeBot:
        def __init__(self):
            self.edits = []

        async def edit_message_text(self, **kwargs):
            self.edits.append(kwargs)

    bot = FakeBot()
    event = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "renaiss_bot.jobs.list_unfinished_spawns",
        AsyncMock(
            return_value=[
                {
                    "posted_event_id": 102,
                    "session_id": "deadbeef",
                    "chat_id": -1001,
                    "metadata": {"message_id": 42},
                    "award_user_id": 123,
                    "award_metadata": {
                        "winner_name": "Rookie",
                        "card_name": "Charizard",
                    },
                }
            ]
        ),
    )
    monkeypatch.setattr("renaiss_bot.jobs.log_event", event)

    await recover_unfinished_spawns(SimpleNamespace(bot=bot))

    assert "already awarded to <b>Rookie</b>" in bot.edits[0]["text"]
    assert "cancelled" not in bot.edits[0]["text"]
    assert event.await_args.args[0] == "spawn_revealed"
    assert event.await_args.kwargs["user_id"] == 123
    assert event.await_args.kwargs["metadata"]["award_recovered"] is True


async def test_restart_recovery_keeps_failed_prompt_close_nonterminal(monkeypatch):
    class FakeBot:
        async def edit_message_text(self, **kwargs):
            raise RuntimeError("message is gone")

    event = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "renaiss_bot.jobs.list_unfinished_spawns",
        AsyncMock(
            return_value=[
                {
                    "posted_event_id": 103,
                    "session_id": "gone",
                    "chat_id": -1001,
                    "metadata": {"message_id": 43},
                }
            ]
        ),
    )
    monkeypatch.setattr("renaiss_bot.jobs.log_event", event)

    with pytest.raises(RuntimeError, match="spawn recovery incomplete"):
        await recover_unfinished_spawns(SimpleNamespace(bot=FakeBot()))

    assert event.await_args.args[0] == "spawn_recovery_close_failed"
    assert event.await_args.kwargs["event_key"] == "spawn:gone:recovery-close-failed"
    assert event.await_args.kwargs["metadata"]["prompt_closed"] is False


async def test_restart_recovery_pages_until_exhausted(monkeypatch):
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    page_one = [
        {
            "posted_event_id": 201,
            "session_id": "page-one",
            "chat_id": -1001,
            "metadata": {"message_id": 51},
        }
    ]
    page_two = [
        {
            "posted_event_id": 202,
            "session_id": "page-two",
            "chat_id": -1001,
            "metadata": {"message_id": 52},
        }
    ]
    listing = AsyncMock(side_effect=[page_one, page_two, []])
    monkeypatch.setattr("renaiss_bot.jobs.list_unfinished_spawns", listing)
    monkeypatch.setattr("renaiss_bot.jobs.log_event", AsyncMock(return_value=True))

    recovered = await recover_unfinished_spawns(
        SimpleNamespace(bot=bot),
        page_size=1,
    )

    assert recovered == 2
    assert [call.kwargs["after_id"] for call in listing.await_args_list] == [0, 201, 202]
    assert bot.edit_message_text.await_count == 2


async def test_rebuild_active_spawn_restores_round_from_ledger(monkeypatch):
    from renaiss_bot.handlers.spawn import rebuild_active_spawn

    card = _card("resumed", 120.0)
    monkeypatch.setattr(
        "renaiss_bot.services.card_pool.load_catalog_card",
        AsyncMock(return_value=card),
    )
    metadata = {
        "local_card_id": "catalog:pokemon_tcg:renaiss-xyz",
        "message_id": 77,
        "band": "rare",
        "market_usd": 120.0,
        "price_options": [60.0, 120.0, 180.0, 240.0],
        "correct_price_index": 1,
        "guess_capable": True,
        "prompt_is_photo": True,
    }
    active = await rebuild_active_spawn(
        session_id="resume-token",
        chat_id=-1001,
        metadata=metadata,
        catchers={7: "Alice", 8: "Bob"},
        guesses={7: 1},
    )
    assert active is not None
    assert active.token == "resume-token"
    assert active.message_id == 77
    assert active.catchers == {7: "Alice", 8: "Bob"}
    assert active.guesses == {7: 1}
    assert active.price_options == [60.0, 120.0, 180.0, 240.0]
    assert active.correct_price_index == 1
    assert active.spawn.band == "rare"
    assert active.prompt_is_photo is True


async def test_restart_recovery_resumes_open_round_instead_of_closing(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from renaiss_bot import jobs as jobs_module

    closes_at = (datetime.now(timezone.utc) + timedelta(seconds=25)).isoformat()
    row = {
        "posted_event_id": 900,
        "session_id": "resume-token",
        "chat_id": -1001,
        "metadata": {
            "local_card_id": "catalog:pokemon_tcg:renaiss-xyz",
            "message_id": 88,
            "closes_at": closes_at,
            "price_options": [10.0, 20.0, 30.0, 40.0],
            "correct_price_index": 2,
            "band": "common",
            "market_usd": 20.0,
        },
        "created_at": datetime.now(timezone.utc),
        "award_user_id": None,
        "award_metadata": None,
    }
    pages = [[row], []]

    async def fake_list(**kwargs):
        return pages.pop(0)

    monkeypatch.setattr(jobs_module, "list_unfinished_spawns", fake_list)
    monkeypatch.setattr(
        jobs_module,
        "list_spawn_round_entries",
        AsyncMock(return_value={"catchers": {5: "Resumer"}, "guesses": {5: 2}}),
    )
    card = _card("resumed", 20.0)
    monkeypatch.setattr(
        "renaiss_bot.services.card_pool.load_catalog_card",
        AsyncMock(return_value=card),
    )
    close_edit = AsyncMock()
    monkeypatch.setattr(jobs_module, "_edit_recovered_prompt", close_edit)

    scheduled = []

    class FakeQueue:
        def run_once(self, callback, when, **kwargs):
            scheduled.append((callback, when, kwargs))

    _active.clear()
    application = SimpleNamespace(
        bot=SimpleNamespace(),
        job_queue=FakeQueue(),
        bot_data={},
    )
    recovered = await jobs_module.recover_unfinished_spawns(application)

    assert recovered == 1
    assert -1001 in _active
    resumed = _active[-1001]
    assert resumed.catchers == {5: "Resumer"}
    assert resumed.guesses == {5: 2}
    close_edit.assert_not_awaited()  # 닫기 안내가 아니라 이어하기여야 한다
    assert scheduled and 20 <= scheduled[0][1] <= 26  # 남은 시간으로 재예약
    _active.clear()

