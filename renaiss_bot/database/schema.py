"""Schema for standalone Renaiss bot tables."""

from __future__ import annotations

import asyncpg


async def create_tables(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
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
                clicked_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
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
                first_obtained_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, category, local_card_id)
            )
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
            CREATE TABLE IF NOT EXISTS renaiss_flex_props (
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                tapper_user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (chat_id, message_id, tapper_user_id)
            )
            """
        )
