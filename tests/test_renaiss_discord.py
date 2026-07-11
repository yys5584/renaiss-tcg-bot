"""Discord user-facing copy follows the English product rule."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from renaiss_bot.adapters.discord import main as discord_main
from renaiss_bot.adapters.discord.formatting import format_collection_message, format_price_message
from renaiss_bot.services.models import RenaissPrice


def test_discord_price_copy_is_english():
    text = format_price_message(
        "Charizard",
        "pokemon_tcg",
        RenaissPrice(
            status="exact",
            source="renaiss-index-api",
            fmv_usd=95,
            change_7d_pct=2.5,
        ),
    )
    assert "Reference FMV" in text or "reference FMV" in text
    assert "7-day change" in text
    assert "Source:" in text


def test_discord_empty_collection_copy_is_english():
    text = format_collection_message(None)
    assert "Join a blind community spawn with `c` in Telegram." in text
    assert "no physical card or NFT ownership" in text
    assert "/open" not in text


async def test_discord_private_pack_gate_stops_before_database(monkeypatch):
    monkeypatch.delenv("RENAISS_PRIVATE_FREE_PACKS_ENABLED", raising=False)
    reserve = AsyncMock()
    monkeypatch.setattr(discord_main, "reserve_command_free_packs", reserve)
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await discord_main.open_command.callback(interaction)

    reserve.assert_not_awaited()
    text = interaction.followup.send.await_args.args[0]
    assert "closed during the collector-market pilot" in text
    assert "no physical card or NFT ownership" in text


async def test_discord_database_init_fails_closed_without_database(monkeypatch):
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await discord_main._init_db()


async def test_discord_database_init_propagates_schema_failure(monkeypatch):
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setattr(discord_main, "get_db", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        discord_main,
        "create_tables",
        AsyncMock(side_effect=RuntimeError("schema failed")),
    )

    with pytest.raises(RuntimeError, match="initialization failed"):
        await discord_main._init_db()


def test_discord_price_slot_throttles_same_user(monkeypatch):
    discord_main._discord_price_next_allowed.clear()
    monkeypatch.setenv("RENAISS_PRICE_USER_COOLDOWN_SECONDS", "10")

    assert discord_main._claim_price_slot(77) == 0
    assert discord_main._claim_price_slot(77) > 0
    assert discord_main._claim_price_slot(78) == 0
