"""Real PostgreSQL coverage for the bot's data-integrity boundaries."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import uuid
from zoneinfo import ZoneInfo

import pytest

from renaiss_bot.database.api_queries import (
    claim_partner_api_request_slot,
    extend_partner_api_cooldown,
    get_partner_api_cooldown_seconds,
)
from renaiss_bot.database.event_queries import (
    release_spawn_dispatch,
    reserve_spawn_dispatch,
)
from renaiss_bot.database.catalog_queries import (
    acquire_catalog_refresh_lease,
    finish_catalog_refresh_lease,
)
from renaiss_bot.database.instance_queries import (
    acquire_instance_lock,
    instance_lock_owner_backend_pid,
    probe_instance_lock_contender,
    probe_instance_lock_session,
    release_instance_lock,
)
from renaiss_bot.database.market_queries import (
    acquire_market_refresh_job_lease,
    begin_daily_pick_result_bell_delivery,
    claim_daily_pick_result_bell,
    enqueue_daily_pick_result_bell,
    finish_market_refresh_job_lease,
    get_latest_complete_result_cohort,
    mark_daily_pick_result_bell_sent,
    record_market_price_snapshot,
    renew_market_refresh_job_lease,
    settle_due_daily_picks,
)
from renaiss_bot.database.queries import (
    SpawnAwardConflict,
    award_spawn_card,
    complete_daily_flex,
    finalize_command_free_pack,
    grant_first_c_starter,
    reserve_daily_flex,
    reserve_command_free_packs,
)
from renaiss_bot.database.schema import create_tables
from renaiss_bot.services.models import (
    CardIdentity,
    PackOpenResult,
    RenaissPrice,
    card_identity_key,
)
from renaiss_bot.services.pack_rules import CARDS_PER_PACK
from renaiss_bot.tools.preflight import _probe_telegram_lock_domain

pytestmark = pytest.mark.postgres


def _card() -> CardIdentity:
    return CardIdentity(
        category="pokemon_tcg",
        card_name="Integration Charizard",
        set_code="INT",
        set_name="Integration Set",
        collector_number="1/1",
        grade="RAW",
        local_card_id="integration-card-1",
        market_price_usd=100,
    )


def _pack_result(pack_count: int) -> PackOpenResult:
    card = _card()
    return PackOpenResult(
        category="pokemon_tcg",
        cards=[card for _ in range(pack_count * CARDS_PER_PACK)],
        best_card=card,
        best_price=RenaissPrice(status="candidate", source="integration", fmv_usd=100),
        pack_type="free",
        pack_count=pack_count,
        pool_source="integration",
    )


async def test_schema_is_idempotent_on_real_postgres(postgres_pool):
    await asyncio.gather(*(create_tables(postgres_pool) for _ in range(4)))


async def test_telegram_session_lock_has_one_live_owner(postgres_pool):
    lock_name = "renaiss:telegram_poller:integration"
    first = await postgres_pool.acquire()
    second = await postgres_pool.acquire()
    try:
        assert await acquire_instance_lock(first, lock_name=lock_name)
        backend_pid = await instance_lock_owner_backend_pid(
            first,
            lock_name=lock_name,
        )
        assert backend_pid is not None
        assert await probe_instance_lock_session(
            first,
            lock_name=lock_name,
            expected_backend_pid=backend_pid,
        )
        assert await probe_instance_lock_contender(second, lock_name=lock_name)
        assert not await acquire_instance_lock(second, lock_name=lock_name)
        assert not await release_instance_lock(second, lock_name=lock_name)
        assert await release_instance_lock(first, lock_name=lock_name)
        assert not await probe_instance_lock_session(first, lock_name=lock_name)
        assert not await probe_instance_lock_contender(second, lock_name=lock_name)
        assert await acquire_instance_lock(second, lock_name=lock_name)
        assert not await release_instance_lock(first, lock_name=lock_name)
        assert await release_instance_lock(second, lock_name=lock_name)
    finally:
        await postgres_pool.release(first)
        await postgres_pool.release(second)


async def test_preflight_lock_domain_blocks_then_reacquires(postgres_pool):
    owner = await postgres_pool.acquire()
    try:
        result = await _probe_telegram_lock_domain(
            postgres_pool,
            owner,
            f"renaiss:preflight:integration:{uuid.uuid4().hex}",
        )
    finally:
        await postgres_pool.release(owner)

    assert result == (True, False, True)


@pytest.mark.parametrize("disconnect", ["close", "terminate"])
async def test_telegram_session_lock_is_released_when_owner_disconnects(
    postgres_pool,
    disconnect,
):
    lock_name = f"renaiss:telegram_poller:disconnect:{disconnect}"
    owner = await postgres_pool.acquire()
    successor = await postgres_pool.acquire()
    successor_acquired = False
    try:
        assert await acquire_instance_lock(owner, lock_name=lock_name)
        assert not await acquire_instance_lock(successor, lock_name=lock_name)

        if disconnect == "close":
            await owner.close()
        else:
            owner.terminate()

        deadline = asyncio.get_running_loop().time() + 2
        while not successor_acquired:
            successor_acquired = await acquire_instance_lock(
                successor,
                lock_name=lock_name,
            )
            if successor_acquired:
                break
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail("PostgreSQL did not release the disconnected session lock")
            await asyncio.sleep(0.01)
    finally:
        try:
            if successor_acquired:
                await release_instance_lock(successor, lock_name=lock_name)
        finally:
            try:
                await postgres_pool.release(owner)
            finally:
                await postgres_pool.release(successor)


async def test_partner_api_cooldown_is_shared_and_never_shortened(postgres_pool):
    await asyncio.gather(
        extend_partner_api_cooldown(retry_after_seconds=60, reason="integration-429"),
        extend_partner_api_cooldown(retry_after_seconds=5, reason="shorter-429"),
    )
    first = await get_partner_api_cooldown_seconds()
    second = await get_partner_api_cooldown_seconds()

    assert 1 <= first <= 60
    assert second >= first - 1
    await create_tables(postgres_pool)


async def test_spawn_dispatch_lease_and_daily_cap_are_cross_instance_safe(postgres_pool):
    chat_id = -100777

    first_wave = await asyncio.gather(
        *(
            reserve_spawn_dispatch(
                chat_id=chat_id,
                lease_token=f"integration-spawn-{index}",
                daily_cap=2,
                quiet_start_hour=0,
                quiet_end_hour=0,
                lease_seconds=60,
                minimum_interval_seconds=0,
            )
            for index in range(8)
        )
    )
    winners = [reservation for reservation in first_wave if reservation.acquired]
    assert len(winners) == 1
    winning_index = next(index for index, item in enumerate(first_wave) if item.acquired)
    assert await release_spawn_dispatch(
        chat_id=chat_id,
        lease_token=f"integration-spawn-{winning_index}",
    )

    second = await reserve_spawn_dispatch(
        chat_id=chat_id,
        lease_token="integration-spawn-second",
        daily_cap=2,
        quiet_start_hour=0,
        quiet_end_hour=0,
        lease_seconds=60,
        minimum_interval_seconds=0,
    )
    assert second.acquired
    assert second.dispatched_count == 2
    assert await release_spawn_dispatch(
        chat_id=chat_id,
        lease_token="integration-spawn-second",
    )

    capped = await reserve_spawn_dispatch(
        chat_id=chat_id,
        lease_token="integration-spawn-capped",
        daily_cap=2,
        quiet_start_hour=0,
        quiet_end_hour=0,
        lease_seconds=60,
        minimum_interval_seconds=0,
    )
    assert not capped.acquired
    assert capped.reason == "daily_cap"


async def test_partner_api_request_start_gate_is_atomic_across_workers(postgres_pool):
    claims = await asyncio.gather(
        *(claim_partner_api_request_slot(min_interval_ms=1000) for _ in range(8))
    )

    assert claims.count(0) == 1
    assert all(wait == 0 or wait > 0 for wait in claims)


async def test_concurrent_pack_quota_and_finalize_are_exact(postgres_pool):
    reservations = await asyncio.gather(
        *(
            reserve_command_free_packs(
                request_id=f"integration-open-{index}",
                user_id=7001,
                chat_id=7001,
                platform="integration",
                category="pokemon_tcg",
                requested_count=5,
                daily_limit=5,
            )
            for index in range(2)
        )
    )
    assert sum(item.allowed_count for item in reservations) == 5
    reserved = next(item for item in reservations if item.created)

    finalized = await asyncio.gather(
        *(
            finalize_command_free_pack(
                request_id=reserved.request_id,
                user_id=7001,
                chat_id=7001,
                result=_pack_result(reserved.allowed_count),
            )
            for _ in range(2)
        )
    )
    assert sorted(finalized) == [False, True]
    async with postgres_pool.acquire() as conn:
        quantity = await conn.fetchval(
            "SELECT quantity FROM renaiss_user_cards WHERE user_id=7001"
        )
        event = await conn.fetchrow(
            """
            SELECT pack_count, card_count
            FROM renaiss_pack_events
            WHERE request_id=$1
            """,
            reserved.request_id,
        )
    assert quantity == reserved.allowed_count * CARDS_PER_PACK == 50
    assert event is not None
    assert event["pack_count"] == 5
    assert event["card_count"] == 50


async def test_pack_finalize_rolls_back_cards_when_event_insert_fails(postgres_pool):
    reservation = await reserve_command_free_packs(
        request_id="integration-pack-rollback",
        user_id=7051,
        chat_id=7051,
        platform="integration",
        category="pokemon_tcg",
        requested_count=1,
        daily_limit=5,
    )
    assert reservation.created
    async with postgres_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO renaiss_pack_events (
                user_id, chat_id, category, pack_type, pack_count, card_count,
                pool_source, source, request_id
            ) VALUES (9999,9999,'pokemon_tcg','free',1,10,'fault','fault',$1)
            """,
            reservation.request_id,
        )

    with pytest.raises(Exception):
        await finalize_command_free_pack(
            request_id=reservation.request_id,
            user_id=7051,
            chat_id=7051,
            result=_pack_result(1),
        )

    async with postgres_pool.acquire() as conn:
        cards = await conn.fetchval(
            "SELECT COUNT(*) FROM renaiss_user_cards WHERE user_id = 7051"
        )
        status = await conn.fetchval(
            "SELECT status FROM renaiss_pack_open_requests WHERE request_id=$1",
            reservation.request_id,
        )
    assert cards == 0
    assert status == "reserved"


async def test_concurrent_spawn_award_is_idempotent(postgres_pool):
    price = RenaissPrice(status="candidate", source="integration", fmv_usd=100)
    results = await asyncio.gather(
        *(
            award_spawn_card(
                award_key="integration-spawn-award",
                spawn_token="integration-spawn-token",
                user_id=8001,
                winner_name="Integration Winner",
                chat_id=-1008001,
                category="pokemon_tcg",
                card=_card(),
                price=price,
            )
            for _ in range(8)
        )
    )
    assert sum(item.created for item in results) == 1
    assert len({item.event_id for item in results}) == 1
    async with postgres_pool.acquire() as conn:
        quantity = await conn.fetchval(
            "SELECT quantity FROM renaiss_user_cards WHERE user_id=8001"
        )
        events = await conn.fetchval(
            "SELECT COUNT(*) FROM renaiss_events WHERE event_key='integration-spawn-award'"
        )
    assert quantity == 1 and events == 1

    with pytest.raises(SpawnAwardConflict):
        await award_spawn_card(
            award_key="integration-spawn-award",
            spawn_token="integration-spawn-token",
            user_id=8002,
            winner_name="Wrong Winner",
            chat_id=-1008001,
            category="pokemon_tcg",
            card=_card(),
            price=price,
        )


async def test_spawn_award_rolls_back_event_when_card_insert_fails(postgres_pool):
    invalid_card = CardIdentity(
        category="pokemon_tcg",
        card_name=None,  # type: ignore[arg-type]
        grade="R",
        local_card_id="integration-invalid-card",
    )
    with pytest.raises(Exception):
        await award_spawn_card(
            award_key="integration-spawn-rollback",
            spawn_token="integration-spawn-rollback-token",
            user_id=8051,
            winner_name="Rollback Winner",
            chat_id=-1008051,
            category="pokemon_tcg",
            card=invalid_card,
            price=RenaissPrice(status="candidate", source="integration", fmv_usd=10),
        )

    async with postgres_pool.acquire() as conn:
        events = await conn.fetchval(
            "SELECT COUNT(*) FROM renaiss_events WHERE event_key='integration-spawn-rollback'"
        )
        cards = await conn.fetchval(
            "SELECT COUNT(*) FROM renaiss_user_cards WHERE user_id=8051"
        )
    assert events == 0 and cards == 0


async def test_concurrent_first_c_starter_is_collection_only_and_once(postgres_pool):
    grants = await asyncio.gather(
        *(
            grant_first_c_starter(user_id=8101, chat_id=-1008101)
            for _ in range(12)
        )
    )
    assert sum(grant.created for grant in grants) == 1
    assert len({grant.event_id for grant in grants}) == 1
    async with postgres_pool.acquire() as conn:
        card = await conn.fetchrow(
            """
            SELECT category, local_card_id, card_name, market_price_usd, quantity,
                   is_tutorial
            FROM renaiss_user_cards
            WHERE user_id = 8101
            """
        )
        event_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM renaiss_events
            WHERE event_key = 'renaiss:first-c-starter:user:8101'
            """
        )
        pack_count = await conn.fetchval(
            "SELECT COUNT(*) FROM renaiss_pack_events WHERE user_id = 8101"
        )
    assert card is not None
    assert card["local_card_id"] == "renaiss:starter:welcome:v1"
    assert card["market_price_usd"] is None
    assert card["quantity"] == 1
    assert card["is_tutorial"] is True
    assert event_count == 1
    assert pack_count == 0


async def test_concurrent_daily_flex_reserves_and_publishes_once(postgres_pool):
    card = {
        "local_card_id": "integration-flex-card",
        "card_name": "Integration Flex Card",
        "grade": "SAR",
        "market_price_usd": 250,
    }
    tokens = [f"integration-flex-{index}" for index in range(8)]
    reservations = await asyncio.gather(
        *(
            reserve_daily_flex(
                user_id=8201,
                chat_id=-1008201,
                reservation_token=token,
                card=card,
            )
            for token in tokens
        )
    )
    assert sum(row["state"] == "reserved" for row in reservations) == 1
    assert sum(row["state"] == "user_already" for row in reservations) == 7
    winner = next(
        token for token, row in zip(tokens, reservations) if row["state"] == "reserved"
    )
    assert await complete_daily_flex(reservation_token=winner, message_id=8201001)
    assert not await complete_daily_flex(reservation_token=winner, message_id=8201002)
    async with postgres_pool.acquire() as conn:
        slot = await conn.fetchrow(
            """
            SELECT state, message_id
            FROM renaiss_flex_daily_slots
            WHERE user_id = 8201
            """
        )
        posts = await conn.fetchval(
            "SELECT COUNT(*) FROM renaiss_flex_posts WHERE user_id = 8201"
        )
    assert slot["state"] == "sent" and slot["message_id"] == 8201001
    assert posts == 1


async def test_concurrent_flex_users_share_one_atomic_room_budget(
    postgres_pool,
    monkeypatch,
):
    monkeypatch.setenv("RENAISS_FLEX_ROOM_DAILY_LIMIT", "1")
    monkeypatch.setenv("RENAISS_FLEX_ROOM_COOLDOWN_SECONDS", "30")
    card = {
        "local_card_id": "integration-room-flex-card",
        "card_name": "Integration Room Flex Card",
        "grade": "SAR",
        "market_price_usd": 250,
    }
    reservations = await asyncio.gather(
        *(
            reserve_daily_flex(
                user_id=8300 + index,
                chat_id=-1008301,
                reservation_token=f"integration-room-flex-{index}",
                card=card,
            )
            for index in range(1, 9)
        )
    )

    assert sum(row["state"] == "reserved" for row in reservations) == 1
    assert sum(row["state"] == "room_limit" for row in reservations) == 7


async def test_concurrent_daily_pick_settlement_uses_first_valid_mark(postgres_pool):
    now = datetime.now(timezone.utc)
    settles_at = now - timedelta(hours=1)
    async with postgres_pool.acquire() as conn:
        board_id = await conn.fetchval(
            """
            INSERT INTO renaiss_market_board (
                week_start, category, local_card_id, card_name, set_code, set_name,
                collector_number, language, grade, initial_fmv_usd, pick_eligible
            ) VALUES ($1,'pokemon_tcg','settlement-card','Settlement Card','INT',
                      'Integration Set','2/2','English','RAW',100,TRUE)
            RETURNING id
            """,
            date.today(),
        )
        stale_id = await conn.fetchval(
            """
            INSERT INTO renaiss_market_price_snapshots (
                board_card_id,fmv_usd,price_source,pick_eligible,price_updated_at,captured_at
            ) VALUES ($1,105,'integration',TRUE,$2,$3) RETURNING id
            """,
            board_id,
            settles_at - timedelta(minutes=1),
            settles_at + timedelta(minutes=1),
        )
        first_valid_id = await conn.fetchval(
            """
            INSERT INTO renaiss_market_price_snapshots (
                board_card_id,fmv_usd,price_source,pick_eligible,price_updated_at,captured_at
            ) VALUES ($1,110,'integration',TRUE,$2,$3) RETURNING id
            """,
            board_id,
            settles_at + timedelta(minutes=2),
            settles_at + timedelta(minutes=2),
        )
        await conn.execute(
            """
            INSERT INTO renaiss_market_price_snapshots (
                board_card_id,fmv_usd,price_source,pick_eligible,price_updated_at,captured_at
            ) VALUES ($1,120,'integration',TRUE,$2,$3)
            """,
            board_id,
            settles_at + timedelta(minutes=3),
            settles_at + timedelta(minutes=3),
        )
        await conn.executemany(
            """
            INSERT INTO renaiss_market_picks (
                user_id,pick_date,board_card_id,entry_fmv_usd,entry_price_updated_at,
                community_chat_id,picked_at,settles_at
            ) VALUES ($1,$2,$3,100,$4,-1009001,$5,$6)
            """,
            [
                (
                    9000 + index,
                    date.today() - timedelta(days=1),
                    board_id,
                    settles_at - timedelta(days=1),
                    settles_at - timedelta(days=1),
                    settles_at,
                )
                for index in range(20)
            ],
        )

    settled_batches = await asyncio.gather(
        *(settle_due_daily_picks(as_of=now, limit=5) for _ in range(8))
    )
    assert sum(len(batch) for batch in settled_batches) == 20
    async with postgres_pool.acquire() as conn:
        snapshot_ids = await conn.fetch(
            "SELECT DISTINCT settlement_snapshot_id FROM renaiss_market_picks"
        )
        event_count = await conn.fetchval(
            "SELECT COUNT(*) FROM renaiss_events WHERE event_name='daily_pick_settled'"
        )
    assert stale_id != first_valid_id
    assert [row["settlement_snapshot_id"] for row in snapshot_ids] == [first_valid_id]
    assert event_count == 20
    assert await settle_due_daily_picks(as_of=now, limit=100) == []


async def test_same_market_mark_can_become_eligible_when_evidence_improves(
    postgres_pool,
    monkeypatch,
):
    monkeypatch.setenv("RENAISS_DAILY_PICK_MIN_SOURCE_COUNT", "2")
    monkeypatch.setenv("RENAISS_DAILY_PICK_MIN_CONFIDENCE_SCORE", "0")
    now = datetime.now(timezone.utc)
    settles_at = now - timedelta(minutes=1)
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Evidence Card",
        set_code="INT",
        set_name="Integration Set",
        collector_number="3/3",
        language="English",
        grade="RAW",
        local_card_id="evidence-card",
    )
    async with postgres_pool.acquire() as conn:
        board_id = await conn.fetchval(
            """
            INSERT INTO renaiss_market_board (
                week_start, category, local_card_id, card_name, set_code, set_name,
                collector_number, language, grade, asset_url, initial_fmv_usd,
                pick_eligible
            ) VALUES ($1,'pokemon_tcg','evidence-card','Evidence Card','INT',
                      'Integration Set','3/3','English','RAW',$2,100,FALSE)
            RETURNING id
            """,
            date.today(),
            "https://index.renaissos.com/cards/evidence-card",
        )
        await conn.execute(
            """
            INSERT INTO renaiss_market_picks (
                user_id,pick_date,board_card_id,entry_fmv_usd,entry_price_updated_at,
                community_chat_id,picked_at,settles_at
            ) VALUES (9101,$1,$2,100,$3,-1009101,$4,$5)
            """,
            date.today() - timedelta(days=1),
            board_id,
            settles_at - timedelta(days=1),
            settles_at - timedelta(days=1),
            settles_at,
        )

    weak = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        fmv_usd=110,
        confidence="high",
        confidence_score=0.9,
        source_count=1,
        observation_count=10,
        valuation_method="median",
        asset_url="https://index.renaissos.com/cards/evidence-card",
        price_updated_at=now,
        source_identity_key=card_identity_key(card),
    )
    strong = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        fmv_usd=110,
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        observation_count=10,
        valuation_method="median",
        asset_url="https://index.renaissos.com/cards/evidence-card",
        price_updated_at=now,
        source_identity_key=card_identity_key(card),
    )

    assert await record_market_price_snapshot(board_card_id=board_id, card=card, price=weak)
    assert await record_market_price_snapshot(board_card_id=board_id, card=card, price=strong)
    assert not await record_market_price_snapshot(board_card_id=board_id, card=card, price=strong)

    settled = await settle_due_daily_picks(
        as_of=datetime.now(timezone.utc) + timedelta(seconds=1),
        limit=10,
    )
    assert len(settled) == 1
    async with postgres_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, pick_eligible, source_count
            FROM renaiss_market_price_snapshots
            WHERE board_card_id = $1
            ORDER BY id
            """,
            board_id,
        )
    assert [(row["pick_eligible"], row["source_count"]) for row in rows] == [
        (False, 1),
        (True, 3),
    ]
    assert settled[0]["settlement_snapshot_id"] == rows[1]["id"]


async def test_result_bell_cohort_aggregate_runs_on_real_postgres(postgres_pool):
    now = datetime.now(timezone.utc)
    bell_date = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    cohort_date = bell_date - timedelta(days=2)
    settles_at = now - timedelta(days=1)
    snapshot_time = settles_at + timedelta(minutes=1)
    async with postgres_pool.acquire() as conn:
        board_ids = []
        snapshot_ids = []
        for index, (name, fmv) in enumerate((("Crowd Card", 110), ("Other Card", 120))):
            board_id = await conn.fetchval(
                """
                INSERT INTO renaiss_market_board (
                    week_start, category, local_card_id, card_name, set_code, set_name,
                    collector_number, language, grade, asset_url, initial_fmv_usd,
                    pick_eligible
                ) VALUES ($1,'pokemon_tcg',$2,$3,'INT','Integration Set',$4,
                          'English','RAW',$5,100,TRUE)
                RETURNING id
                """,
                date.today(),
                f"bell-card-{index}",
                name,
                f"{index + 4}/4",
                f"https://index.renaissos.com/cards/bell-{index}",
            )
            snapshot_id = await conn.fetchval(
                """
                INSERT INTO renaiss_market_price_snapshots (
                    board_card_id,fmv_usd,price_source,confidence,confidence_score,
                    source_count,observation_count,pick_eligible,price_updated_at,captured_at,
                    asset_url
                ) VALUES ($1,$2,'renaiss-index-api','high',0.9,3,10,TRUE,$3,$3,$4)
                RETURNING id
                """,
                board_id,
                fmv,
                snapshot_time,
                f"https://index.renaissos.com/cards/bell-{index}",
            )
            board_ids.append(board_id)
            snapshot_ids.append(snapshot_id)

        await conn.executemany(
            """
            INSERT INTO renaiss_market_picks (
                user_id,pick_date,board_card_id,entry_fmv_usd,entry_price_updated_at,
                community_chat_id,picked_at,settles_at,settlement_snapshot_id,settled_at
            ) VALUES ($1,$2,$3,100,$4,-1009301,$5,$6,$7,$8)
            """,
            [
                (
                    9300 + index,
                    cohort_date,
                    board_ids[0] if index < 4 else board_ids[1],
                    settles_at - timedelta(days=1),
                    settles_at - timedelta(days=1),
                    settles_at,
                    snapshot_ids[0] if index < 4 else snapshot_ids[1],
                    snapshot_time,
                )
                for index in range(6)
            ],
        )
        # A newer but incomplete cohort must never hide or contaminate the complete one.
        await conn.execute(
            """
            INSERT INTO renaiss_market_picks (
                user_id,pick_date,board_card_id,entry_fmv_usd,entry_price_updated_at,
                community_chat_id,picked_at,settles_at
            ) VALUES (9399,$1,$2,100,$3,-1009301,$3,$4)
            """,
            bell_date - timedelta(days=1),
            board_ids[0],
            now - timedelta(hours=1),
            now + timedelta(hours=23),
        )

    cohort = await get_latest_complete_result_cohort(
        chat_id=-1009301,
        before_date=bell_date,
        oldest_date=cohort_date,
        as_of=now,
    )

    assert cohort is not None and cohort["pick_date"] == cohort_date
    assert cohort["participant_count"] == 6
    assert [card["support_count"] for card in cohort["cards"]] == [4, 2]
    assert cohort["cards"][0]["median_move_pct"] == pytest.approx(10.0)
    assert cohort["cards"][0]["min_source_count"] == 3


async def test_global_refresh_lease_fences_owner_and_enforces_cooldown(postgres_pool):
    owners = [f"integration-refresh-{index}" for index in range(8)]
    acquired = await asyncio.gather(
        *(
            acquire_market_refresh_job_lease(
                lease_owner=owner,
                lease_seconds=30,
            )
            for owner in owners
        )
    )
    assert sum(acquired) == 1
    winner = owners[acquired.index(True)]
    loser = next(owner for owner in owners if owner != winner)

    assert not await renew_market_refresh_job_lease(
        lease_owner=loser,
        lease_seconds=30,
    )
    assert not await finish_market_refresh_job_lease(
        lease_owner=loser,
        cadence_seconds=300,
        status="wrong_owner",
    )
    assert await renew_market_refresh_job_lease(
        lease_owner=winner,
        lease_seconds=30,
    )
    async with postgres_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE renaiss_job_leases
            SET lease_expires_at = clock_timestamp() - interval '1 second'
            WHERE job_name = 'daily_pick_price_refresh' AND lease_owner = $1
            """,
            winner,
        )
    # The same fenced owner may finish after expiry so 429/cadence is not lost.
    assert await finish_market_refresh_job_lease(
        lease_owner=winner,
        cadence_seconds=300,
        status="completed",
    )

    assert not await acquire_market_refresh_job_lease(
        lease_owner="integration-refresh-cooldown",
        lease_seconds=30,
    )

    async with postgres_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE renaiss_job_leases
            SET lease_expires_at = clock_timestamp() - interval '1 second',
                next_attempt_at = clock_timestamp() - interval '1 second'
            WHERE job_name = 'daily_pick_price_refresh'
            """
        )

    successor = "integration-refresh-successor"
    assert await acquire_market_refresh_job_lease(
        lease_owner=successor,
        lease_seconds=30,
    )
    assert not await finish_market_refresh_job_lease(
        lease_owner=winner,
        cadence_seconds=300,
        status="stale_owner",
    )
    assert await finish_market_refresh_job_lease(
        lease_owner=successor,
        cadence_seconds=300,
        status="completed",
    )


async def test_catalog_refresh_lease_allows_only_one_instance(postgres_pool):
    owners = [f"catalog-refresh-{index}" for index in range(6)]
    acquired = await asyncio.gather(
        *(acquire_catalog_refresh_lease(owner=owner, lease_seconds=300) for owner in owners)
    )

    assert sum(acquired) == 1
    winner = owners[acquired.index(True)]
    loser = next(owner for owner in owners if owner != winner)
    assert not await finish_catalog_refresh_lease(owner=loser, status="wrong_owner")
    assert await finish_catalog_refresh_lease(
        owner=winner,
        status="completed",
        retry_after_seconds=300,
    )
    assert not await acquire_catalog_refresh_lease(owner="too-early", lease_seconds=300)


async def test_result_bell_outbox_cross_chat_claims_are_idempotent(postgres_pool):
    bell_date = datetime.now(timezone.utc).astimezone(
        ZoneInfo("Asia/Seoul")
    ).date()
    cohort_pick_date = bell_date - timedelta(days=1)
    chats = (-1009101, -1009102)

    first = await enqueue_daily_pick_result_bell(
        chat_id=chats[0],
        bell_date=bell_date,
        cohort_pick_date=cohort_pick_date,
        message_text="First community result",
        metrics={"participant_count": 6},
    )
    duplicate = await enqueue_daily_pick_result_bell(
        chat_id=chats[0],
        bell_date=bell_date,
        cohort_pick_date=cohort_pick_date,
        message_text="Duplicate community result",
        metrics={"participant_count": 6},
    )
    second_chat = await enqueue_daily_pick_result_bell(
        chat_id=chats[1],
        bell_date=bell_date,
        cohort_pick_date=cohort_pick_date,
        message_text="Second community result",
        metrics={"participant_count": 8},
    )

    assert first is not None
    assert duplicate is None
    assert second_chat is not None

    claim_attempts = [
        (chat_id, index)
        for chat_id in chats
        for index in range(4)
    ]
    claims = await asyncio.gather(
        *(
            claim_daily_pick_result_bell(
                chat_id=chat_id,
                bell_date=bell_date,
                attempt_token=f"integration-bell-{chat_id}-attempt-{index}",
                lease_owner=f"integration-bell-{chat_id}-worker-{index}",
            )
            for chat_id, index in claim_attempts
        )
    )
    claimed = [row for row in claims if row is not None]
    assert len(claimed) == 2
    assert {int(row["chat_id"]) for row in claimed} == set(chats)
    assert len({int(row["id"]) for row in claimed}) == 2

    for index, row in enumerate(claimed, start=1):
        outbox_id = int(row["id"])
        attempt_token = str(row["attempt_token"])
        assert await begin_daily_pick_result_bell_delivery(
            outbox_id=outbox_id,
            attempt_token="wrong-attempt-token",
        ) is None
        inflight = await begin_daily_pick_result_bell_delivery(
            outbox_id=outbox_id,
            attempt_token=attempt_token,
        )
        assert inflight is not None
        assert int(inflight["chat_id"]) == int(row["chat_id"])
        assert await mark_daily_pick_result_bell_sent(
            outbox_id=outbox_id,
            attempt_token=attempt_token,
            telegram_message_id=7700 + index,
        )
        assert not await mark_daily_pick_result_bell_sent(
            outbox_id=outbox_id,
            attempt_token=attempt_token,
            telegram_message_id=8800 + index,
        )

    for chat_id in chats:
        assert await claim_daily_pick_result_bell(
            chat_id=chat_id,
            bell_date=bell_date,
            attempt_token=f"integration-bell-{chat_id}-after-sent",
            lease_owner=f"integration-bell-{chat_id}-after-sent",
        ) is None

    async with postgres_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chat_id, state, attempt_count, telegram_message_id
            FROM renaiss_result_bell_outbox
            ORDER BY chat_id
            """
        )
    assert [row["chat_id"] for row in rows] == sorted(chats)
    assert all(row["state"] == "sent" for row in rows)
    assert all(row["attempt_count"] == 1 for row in rows)
    assert len({row["telegram_message_id"] for row in rows}) == 2
