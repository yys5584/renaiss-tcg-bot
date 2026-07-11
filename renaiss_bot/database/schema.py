"""Schema for standalone Renaiss bot tables."""

from __future__ import annotations

import os

import asyncpg


async def create_tables(pool: asyncpg.Pool) -> None:
    # Every runtime entrypoint reaches this function.  Do not let an ambient
    # DATABASE_URL bypass the explicit prepare_database target fence.
    configured_dsn = os.getenv("DATABASE_URL", "").strip()
    if configured_dsn:
        from renaiss_bot.tools.prepare_database import (
            database_mutation_target_issue,
        )

        if database_mutation_target_issue(configured_dsn):
            raise RuntimeError("database mutation target is not approved")
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended('renaiss-schema-v1', 0))"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_runtime_settings (
                key TEXT PRIMARY KEY,
                value JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS renaiss_card_links (
                id BIGSERIAL PRIMARY KEY,
                local_card_id TEXT,
                category TEXT NOT NULL,
                card_name TEXT NOT NULL,
                set_code TEXT,
                collector_number TEXT,
                language TEXT,
                rarity TEXT,
                grade TEXT,
                renaiss_asset_id TEXT,
                renaiss_url TEXT,
                referral_url TEXT,
                match_status TEXT NOT NULL DEFAULT 'search_only',
                match_confidence NUMERIC,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_catalog_cards (
                local_card_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                card_name TEXT NOT NULL,
                grade TEXT NOT NULL DEFAULT 'R',
                set_code TEXT,
                set_name TEXT,
                collector_number TEXT,
                rarity TEXT,
                language TEXT NOT NULL DEFAULT 'Japanese',
                image_url TEXT,
                market_price_usd NUMERIC,
                metadata JSONB,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_catalog_category_grade
                ON renaiss_catalog_cards(category, grade)
                WHERE is_active = TRUE
            """
        )
        await conn.execute(
            """
            UPDATE renaiss_catalog_cards
            SET market_price_usd = NULL, updated_at = clock_timestamp()
            WHERE market_price_usd IS NOT NULL
              AND (
                  market_price_usd <= 0
                  OR market_price_usd::text IN ('NaN', 'Infinity', '-Infinity')
              )
            """
        )
        await conn.execute(
            """
            DO $$
            BEGIN
                ALTER TABLE renaiss_catalog_cards
                ADD CONSTRAINT renaiss_catalog_positive_market_price
                CHECK (
                    market_price_usd IS NULL
                    OR (
                        market_price_usd > 0
                        AND market_price_usd::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    )
                );
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_price_snapshots (
                id BIGSERIAL PRIMARY KEY,
                category TEXT,
                card_name TEXT,
                local_card_id TEXT,
                renaiss_asset_id TEXT,
                match_status TEXT,
                fmv_usd NUMERIC,
                listed_price_usd NUMERIC,
                last_sale_usd NUMERIC,
                change_24h_pct NUMERIC,
                change_7d_pct NUMERIC,
                change_30d_pct NUMERIC,
                market_status TEXT,
                source TEXT,
                asset_url TEXT,
                referral_url TEXT,
                image_url TEXT,
                grade_label TEXT,
                grading_company TEXT,
                confidence TEXT,
                confidence_score NUMERIC,
                source_count INTEGER,
                observation_count INTEGER,
                valuation_method TEXT,
                price_updated_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        for column_sql in (
            "ADD COLUMN IF NOT EXISTS category TEXT",
            "ADD COLUMN IF NOT EXISTS card_name TEXT",
            "ADD COLUMN IF NOT EXISTS match_status TEXT",
            "ADD COLUMN IF NOT EXISTS asset_url TEXT",
            "ADD COLUMN IF NOT EXISTS referral_url TEXT",
            "ADD COLUMN IF NOT EXISTS image_url TEXT",
            "ADD COLUMN IF NOT EXISTS grade_label TEXT",
            "ADD COLUMN IF NOT EXISTS grading_company TEXT",
            "ADD COLUMN IF NOT EXISTS confidence TEXT",
            "ADD COLUMN IF NOT EXISTS confidence_score NUMERIC",
            "ADD COLUMN IF NOT EXISTS source_count INTEGER",
            "ADD COLUMN IF NOT EXISTS observation_count INTEGER",
            "ADD COLUMN IF NOT EXISTS valuation_method TEXT",
        ):
            await conn.execute(f"ALTER TABLE IF EXISTS renaiss_price_snapshots {column_sql}")
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_price_snapshots_card_created
                ON renaiss_price_snapshots(local_card_id, category, created_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_referral_clicks (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                chat_id BIGINT,
                local_card_id TEXT,
                renaiss_asset_id TEXT,
                source TEXT,
                source_message_id BIGINT,
                tracking_token_hash TEXT,
                destination_url TEXT,
                clicked_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_referral_clicks
            ADD COLUMN IF NOT EXISTS tracking_token_hash TEXT
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_referral_clicks
            ADD COLUMN IF NOT EXISTS destination_url TEXT
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_referral_clicks_time_source
                ON renaiss_referral_clicks(clicked_at DESC, source)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_referral_links (
                token_hash TEXT PRIMARY KEY,
                destination_url TEXT NOT NULL,
                user_id BIGINT,
                chat_id BIGINT,
                local_card_id TEXT,
                source TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_referral_links_expires
                ON renaiss_referral_links(expires_at)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_pack_events (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                chat_id BIGINT,
                category TEXT NOT NULL,
                pack_type TEXT NOT NULL DEFAULT 'free',
                pack_count INT NOT NULL DEFAULT 1,
                card_count INT NOT NULL DEFAULT 0,
                pool_source TEXT,
                best_local_card_id TEXT,
                match_status TEXT,
                fmv_usd NUMERIC,
                request_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE IF EXISTS renaiss_pack_events
            ADD COLUMN IF NOT EXISTS pack_type TEXT NOT NULL DEFAULT 'free'
            """
        )
        await conn.execute(
            """
            ALTER TABLE IF EXISTS renaiss_pack_events
            ADD COLUMN IF NOT EXISTS pack_count INT NOT NULL DEFAULT 1
            """
        )
        await conn.execute(
            """
            ALTER TABLE IF EXISTS renaiss_pack_events
            ADD COLUMN IF NOT EXISTS card_count INT NOT NULL DEFAULT 0
            """
        )
        await conn.execute(
            """
            ALTER TABLE IF EXISTS renaiss_pack_events
            ADD COLUMN IF NOT EXISTS pool_source TEXT
            """
        )
        await conn.execute(
            """
            ALTER TABLE IF EXISTS renaiss_pack_events
            ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'command'
            """
        )
        await conn.execute(
            """
            ALTER TABLE IF EXISTS renaiss_pack_events
            ADD COLUMN IF NOT EXISTS card_name TEXT
            """
        )
        await conn.execute(
            """
            ALTER TABLE IF EXISTS renaiss_pack_events
            ADD COLUMN IF NOT EXISTS request_id TEXT
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_renaiss_pack_events_request
                ON renaiss_pack_events(request_id)
                WHERE request_id IS NOT NULL
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_pack_events_user_created
                ON renaiss_pack_events(user_id, created_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_pack_open_requests (
                request_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chat_id BIGINT,
                platform TEXT NOT NULL,
                category TEXT NOT NULL,
                pack_type TEXT NOT NULL DEFAULT 'free',
                pack_count INT NOT NULL CHECK (pack_count > 0),
                used_before INT NOT NULL DEFAULT 0,
                quota_date DATE NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved'
                    CHECK (status IN ('reserved', 'completed', 'failed', 'expired')),
                reserved_until TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_pack_open_requests_quota
                ON renaiss_pack_open_requests(user_id, quota_date, status)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_user_cards (
                user_id BIGINT NOT NULL,
                category TEXT NOT NULL,
                local_card_id TEXT NOT NULL,
                card_name TEXT NOT NULL,
                grade TEXT,
                set_code TEXT,
                collector_number TEXT,
                image_url TEXT,
                market_price_usd NUMERIC,
                quantity INT NOT NULL DEFAULT 1,
                is_tutorial BOOLEAN NOT NULL DEFAULT FALSE,
                first_obtained_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, category, local_card_id)
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_user_cards
            ADD COLUMN IF NOT EXISTS is_tutorial BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
        await conn.execute(
            """
            UPDATE renaiss_user_cards
            SET is_tutorial = TRUE
            WHERE local_card_id = 'renaiss:starter:welcome:v1'
              AND is_tutorial IS NOT TRUE
            """
        )
        await conn.execute(
            """
            UPDATE renaiss_user_cards
            SET market_price_usd = NULL, updated_at = clock_timestamp()
            WHERE market_price_usd IS NOT NULL
              AND (
                  market_price_usd <= 0
                  OR market_price_usd::text IN ('NaN', 'Infinity', '-Infinity')
              )
            """
        )
        await conn.execute(
            """
            DO $$
            BEGIN
                ALTER TABLE renaiss_user_cards
                ADD CONSTRAINT renaiss_user_cards_positive_market_price
                CHECK (
                    market_price_usd IS NULL
                    OR (
                        market_price_usd > 0
                        AND market_price_usd::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    )
                );
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_user_cards_user_updated
                ON renaiss_user_cards(user_id, updated_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_user_preferences (
                user_id BIGINT PRIMARY KEY,
                default_category TEXT NOT NULL DEFAULT 'pokemon_tcg',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_user_points (
                user_id BIGINT PRIMARY KEY,
                points BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_point_events (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INT NOT NULL,
                source TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_portfolio_snapshots (
                user_id BIGINT NOT NULL,
                snapshot_date DATE NOT NULL,
                total_value_usd NUMERIC NOT NULL DEFAULT 0,
                unique_cards INT NOT NULL DEFAULT 0,
                total_cards INT NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, snapshot_date)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_quiz_rounds (
                id BIGSERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                message_id BIGINT,
                category TEXT NOT NULL DEFAULT 'pokemon_tcg',
                card_name TEXT NOT NULL,
                local_card_id TEXT,
                card_image_url TEXT,
                correct_price_usd NUMERIC NOT NULL,
                options_json JSONB NOT NULL,
                correct_index INT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                price_source TEXT,
                referral_url TEXT,
                posted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                closes_at TIMESTAMPTZ,
                revealed_at TIMESTAMPTZ
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_quiz_answers (
                round_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                display_name TEXT,
                choice_index INT NOT NULL,
                is_correct BOOLEAN NOT NULL DEFAULT FALSE,
                answered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (round_id, user_id)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_quiz_answers_user_time
                ON renaiss_quiz_answers(user_id, answered_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_flex_posts (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chat_id BIGINT NOT NULL,
                message_id BIGINT,
                local_card_id TEXT,
                card_name TEXT,
                grade TEXT,
                market_price_usd NUMERIC,
                flexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_flex_posts_user_time
                ON renaiss_flex_posts(user_id, flexed_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_flex_daily_slots (
                user_id BIGINT NOT NULL,
                flex_date DATE NOT NULL,
                reservation_token TEXT NOT NULL UNIQUE,
                chat_id BIGINT NOT NULL,
                state TEXT NOT NULL DEFAULT 'reserved'
                    CHECK (state IN ('reserved', 'sent', 'delivery_unknown', 'dead')),
                message_id BIGINT,
                local_card_id TEXT,
                card_name TEXT,
                grade TEXT,
                market_price_usd NUMERIC,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                sent_at TIMESTAMPTZ,
                CHECK (state <> 'sent' OR (message_id IS NOT NULL AND sent_at IS NOT NULL))
            )
            """
        )
        # Flex moved from one-per-day to a short cooldown: the old
        # (user_id, flex_date) primary key must not survive on live tables.
        await conn.execute(
            """
            ALTER TABLE renaiss_flex_daily_slots
                DROP CONSTRAINT IF EXISTS renaiss_flex_daily_slots_pkey
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_renaiss_flex_daily_slots_token
                ON renaiss_flex_daily_slots(reservation_token)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_flex_daily_slots_user_date
                ON renaiss_flex_daily_slots(user_id, flex_date)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_flex_daily_slots_state
                ON renaiss_flex_daily_slots(state, updated_at)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_telegram_media_cache (
                bot_id BIGINT NOT NULL,
                render_key TEXT NOT NULL,
                telegram_file_id TEXT NOT NULL,
                telegram_file_unique_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (bot_id, render_key),
                CHECK (length(render_key) = 64),
                CHECK (length(telegram_file_id) BETWEEN 1 AND 512)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_flex_props (
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                tapper_user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (chat_id, message_id, tapper_user_id)
            )
            """
        )
        # Daily Market Pick: 공개된 검증 카드 중 하루 한 장을 고르는 안목 기록.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_market_board (
                id BIGSERIAL PRIMARY KEY,
                week_start DATE NOT NULL,
                category TEXT NOT NULL,
                local_card_id TEXT NOT NULL,
                card_name TEXT NOT NULL,
                set_code TEXT,
                set_name TEXT,
                collector_number TEXT,
                variation TEXT NOT NULL DEFAULT '',
                language TEXT,
                grade TEXT,
                image_url TEXT,
                asset_url TEXT,
                initial_fmv_usd NUMERIC NOT NULL,
                price_source TEXT,
                confidence TEXT,
                confidence_score NUMERIC,
                source_count INTEGER,
                observation_count INTEGER,
                valuation_method TEXT,
                pick_eligible BOOLEAN NOT NULL DEFAULT FALSE,
                revealed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (week_start, category, local_card_id)
            )
            """
        )
        # 기존 파일럿 DB에도 구조 식별 필드를 호환 방식으로 추가한다.
        await conn.execute(
            "ALTER TABLE renaiss_market_board ADD COLUMN IF NOT EXISTS set_name TEXT"
        )
        await conn.execute(
            "ALTER TABLE renaiss_market_board ADD COLUMN IF NOT EXISTS asset_url TEXT"
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_board
            ADD COLUMN IF NOT EXISTS variation TEXT NOT NULL DEFAULT ''
            """
        )
        await conn.execute(
            "ALTER TABLE renaiss_market_board ADD COLUMN IF NOT EXISTS confidence_score NUMERIC"
        )
        await conn.execute(
            "ALTER TABLE renaiss_market_board ADD COLUMN IF NOT EXISTS source_count INTEGER"
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_board
            ADD COLUMN IF NOT EXISTS observation_count INTEGER
            """
        )
        await conn.execute(
            "ALTER TABLE renaiss_market_board ADD COLUMN IF NOT EXISTS valuation_method TEXT"
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_market_board_week_revealed
                ON renaiss_market_board(week_start, revealed_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_market_price_snapshots (
                id BIGSERIAL PRIMARY KEY,
                board_card_id BIGINT NOT NULL REFERENCES renaiss_market_board(id) ON DELETE CASCADE,
                fmv_usd NUMERIC NOT NULL,
                price_source TEXT,
                confidence TEXT,
                confidence_score NUMERIC,
                source_count INTEGER,
                observation_count INTEGER,
                valuation_method TEXT,
                asset_url TEXT,
                pick_eligible BOOLEAN NOT NULL DEFAULT FALSE,
                price_updated_at TIMESTAMPTZ,
                captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_market_snapshots_card_time
                ON renaiss_market_price_snapshots(board_card_id, captured_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_market_refresh_leases (
                board_card_id BIGINT PRIMARY KEY
                    REFERENCES renaiss_market_board(id) ON DELETE CASCADE,
                lease_token TEXT,
                lease_until TIMESTAMPTZ NOT NULL DEFAULT now(),
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_status TEXT,
                last_started_at TIMESTAMPTZ,
                last_completed_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_market_refresh_leases_until
                ON renaiss_market_refresh_leases(lease_until)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_job_leases (
                job_name TEXT PRIMARY KEY,
                lease_owner TEXT NOT NULL,
                acquired_at TIMESTAMPTZ NOT NULL,
                lease_expires_at TIMESTAMPTZ NOT NULL,
                next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity'::timestamptz,
                last_status TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_api_cooldowns (
                api_name TEXT PRIMARY KEY,
                blocked_until TIMESTAMPTZ NOT NULL DEFAULT '-infinity'::timestamptz,
                reason TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_api_request_gates (
                api_name TEXT PRIMARY KEY,
                next_request_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity'::timestamptz,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_price_snapshots
            ADD COLUMN IF NOT EXISTS confidence_score NUMERIC
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_price_snapshots
            ADD COLUMN IF NOT EXISTS source_count INTEGER
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_price_snapshots
            ADD COLUMN IF NOT EXISTS observation_count INTEGER
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_price_snapshots
            ADD COLUMN IF NOT EXISTS valuation_method TEXT
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_price_snapshots
            ADD COLUMN IF NOT EXISTS asset_url TEXT
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_price_snapshots
            ALTER COLUMN price_updated_at DROP NOT NULL
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_market_picks (
                user_id BIGINT NOT NULL,
                pick_date DATE NOT NULL,
                board_card_id BIGINT NOT NULL REFERENCES renaiss_market_board(id),
                entry_fmv_usd NUMERIC NOT NULL,
                entry_price_updated_at TIMESTAMPTZ NOT NULL,
                community_chat_id BIGINT,
                picked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                settles_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '24 hours'),
                settlement_snapshot_id BIGINT REFERENCES renaiss_market_price_snapshots(id),
                settled_at TIMESTAMPTZ,
                PRIMARY KEY (user_id, pick_date)
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_picks
            ADD COLUMN IF NOT EXISTS community_chat_id BIGINT
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_picks
            ADD COLUMN IF NOT EXISTS settlement_snapshot_id BIGINT
                REFERENCES renaiss_market_price_snapshots(id)
            """
        )
        await conn.execute(
            """
            ALTER TABLE renaiss_market_picks
            ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_market_picks_date_card
                ON renaiss_market_picks(pick_date, board_card_id)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_market_picks_chat_date
                ON renaiss_market_picks(community_chat_id, pick_date)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_market_picks_due
                ON renaiss_market_picks(settles_at, board_card_id)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_market_picks_unsettled_due
                ON renaiss_market_picks(settles_at, board_card_id)
                WHERE settlement_snapshot_id IS NULL
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_result_bell_outbox (
                id BIGSERIAL PRIMARY KEY,
                event_key TEXT NOT NULL UNIQUE,
                chat_id BIGINT NOT NULL,
                bell_date DATE NOT NULL,
                cohort_pick_date DATE NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending'
                    CHECK (state IN (
                        'pending', 'claimed', 'inflight', 'retryable', 'sent',
                        'delivery_unknown', 'dead', 'suppressed'
                    )),
                message_text TEXT,
                metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
                suppression_reason TEXT,
                available_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                next_attempt_at TIMESTAMPTZ,
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                attempt_token TEXT,
                lease_owner TEXT,
                lease_expires_at TIMESTAMPTZ,
                attempted_at TIMESTAMPTZ,
                telegram_message_id BIGINT,
                last_error_code TEXT,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                sent_at TIMESTAMPTZ,
                UNIQUE (chat_id, bell_date),
                UNIQUE (chat_id, cohort_pick_date),
                CHECK (
                    (state = 'suppressed' AND message_text IS NULL AND suppression_reason IS NOT NULL)
                    OR (state <> 'suppressed' AND message_text IS NOT NULL)
                ),
                CHECK (
                    state <> 'sent'
                    OR (telegram_message_id IS NOT NULL AND sent_at IS NOT NULL)
                )
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_result_bell_dispatch
                ON renaiss_result_bell_outbox(state, available_at, next_attempt_at)
                WHERE state IN ('pending', 'retryable', 'claimed', 'inflight')
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_spawn_dispatch (
                chat_id BIGINT PRIMARY KEY,
                spawn_date DATE NOT NULL,
                dispatched_count INTEGER NOT NULL DEFAULT 0
                    CHECK (dispatched_count >= 0),
                lease_token TEXT,
                lease_expires_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity'::timestamptz,
                last_dispatched_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_spawn_dispatch_lease
                ON renaiss_spawn_dispatch(lease_expires_at)
            """
        )
        # 제품 퍼널 계측. event_key가 있는 행동은 재시도에도 한 번만 저장된다.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS renaiss_events (
                id BIGSERIAL PRIMARY KEY,
                event_name TEXT NOT NULL,
                event_key TEXT UNIQUE,
                user_id BIGINT,
                chat_id BIGINT,
                session_id TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_events_name_time
                ON renaiss_events(event_name, created_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_renaiss_events_user_time
                ON renaiss_events(user_id, created_at DESC)
            """
        )
