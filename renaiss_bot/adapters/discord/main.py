"""Discord entrypoint for Renaiss Edition."""

from __future__ import annotations

import asyncio
import logging
import os
from io import BytesIO
from time import monotonic

import discord
from discord import app_commands

from renaiss_bot.adapters.discord.formatting import (
    format_collection_message,
    format_pack_message,
    format_price_message,
)
from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.database.queries import (
    cancel_pack_open_reservation,
    finalize_command_free_pack,
    reserve_command_free_packs,
)
from renaiss_bot.database.schema import create_tables
from renaiss_bot.renderers.overlay import render_overlay_card
from renaiss_bot.renderers.playwright_render import close_renderer
from renaiss_bot.runtime import load_runtime_environment
from renaiss_bot.services.categories import get_category, resolve_category_key, split_category_args
from renaiss_bot.services.features import private_free_packs_enabled
from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.pack import open_pack
from renaiss_bot.services.pack_rules import (
    DAILY_FREE_PACKS,
    MAX_BATCH_PACKS,
    normalize_pack_type,
)
from renaiss_bot.services.pricing import fetch_price
from renaiss_bot.services.tracking import build_tracked_url

logger = logging.getLogger(__name__)
_discord_price_next_allowed: dict[int, float] = {}


def _configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    )


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _discord_bot_identity_issue(
    actual_bot_id: int | None,
    *,
    require_actual: bool = False,
) -> str | None:
    raw_expected = os.getenv("RENAISS_EXPECTED_DISCORD_BOT_ID", "").strip()
    try:
        expected = int(raw_expected)
    except ValueError:
        expected = 0
    if expected <= 0:
        return "RENAISS_EXPECTED_DISCORD_BOT_ID must be a positive integer"
    if require_actual and actual_bot_id is None:
        return "Discord bot identity is unavailable after login"
    if actual_bot_id is not None and actual_bot_id != expected:
        return "Discord bot identity does not match RENAISS_EXPECTED_DISCORD_BOT_ID"
    return None


def _production_startup_issues() -> list[str]:
    """Reject local configuration gaps before Discord performs any API call."""
    issues = []
    if not os.getenv("RENAISS_DISCORD_TOKEN", "").strip():
        issues.append("RENAISS_DISCORD_TOKEN is missing")
    identity_issue = _discord_bot_identity_issue(None)
    if identity_issue:
        issues.append(identity_issue)
    if os.getenv("RENAISS_API_MOCK_JSON", "").strip():
        issues.append("RENAISS_API_MOCK_JSON must be unset for Discord")
    if os.getenv("RENAISS_SKIP_DB", "").strip().lower() in {"1", "true", "yes"}:
        issues.append("RENAISS_SKIP_DB cannot be enabled for Discord")
    if not os.getenv("DATABASE_URL", "").strip():
        issues.append("DATABASE_URL is missing")
    guild_id = os.getenv("RENAISS_DISCORD_GUILD_ID", "").strip()
    if guild_id:
        try:
            valid_guild_id = int(guild_id) > 0
        except ValueError:
            valid_guild_id = False
        if not valid_guild_id:
            issues.append("RENAISS_DISCORD_GUILD_ID must be a positive integer")
    elif not _env_enabled("RENAISS_DISCORD_ALLOW_GLOBAL_SYNC"):
        issues.append(
            "RENAISS_DISCORD_GUILD_ID is required unless global sync is explicitly enabled"
        )
    return issues


def _claim_price_slot(user_id: int) -> int:
    try:
        cooldown = min(
            60,
            max(3, int(os.getenv("RENAISS_PRICE_USER_COOLDOWN_SECONDS", "10"))),
        )
    except ValueError:
        cooldown = 10
    now = monotonic()
    remaining = _discord_price_next_allowed.get(user_id, 0.0) - now
    if remaining > 0:
        return max(1, int(remaining + 0.999))
    _discord_price_next_allowed[user_id] = now + cooldown
    return 0


class RenaissDiscordClient(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        identity_issue = _discord_bot_identity_issue(
            int(self.user.id) if self.user is not None else None,
            require_actual=True,
        )
        if identity_issue:
            raise RuntimeError(identity_issue)
        guild_id = os.getenv("RENAISS_DISCORD_GUILD_ID", "").strip()
        allow_global_sync = _env_enabled("RENAISS_DISCORD_ALLOW_GLOBAL_SYNC")
        if guild_id and not allow_global_sync:
            existing_global_commands = await self.tree.fetch_commands()
            if existing_global_commands:
                raise RuntimeError(
                    "Discord global commands already exist; clear them before guild-scoped startup"
                )
        await _init_db()
        if not private_free_packs_enabled():
            # Remove the experiment from discovery before copying/syncing the
            # process command tree. The handler below still guards stale caches.
            self.tree.remove_command("open")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Discord commands synced to guild %s.", guild_id)
        elif allow_global_sync:
            await self.tree.sync()
            logger.info("Discord commands synced globally.")
        else:
            raise RuntimeError("Discord global command sync was not explicitly enabled")

    async def close(self) -> None:
        try:
            await close_renderer()
        except Exception as exc:
            logger.debug(
                "Renaiss Discord renderer close skipped (error=%s).",
                type(exc).__name__,
            )
        try:
            await close_db()
        except Exception as exc:
            logger.debug(
                "Renaiss Discord DB close skipped (error=%s).",
                type(exc).__name__,
            )
        await super().close()


client = RenaissDiscordClient()


async def _init_db() -> None:
    if os.getenv("RENAISS_SKIP_DB", "").strip().lower() in {"1", "true", "yes"}:
        raise RuntimeError("Discord cannot start with RENAISS_SKIP_DB enabled")
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required for the Discord adapter")
    try:
        pool = await get_db()
        await create_tables(pool)
        logger.info("Renaiss Discord DB tables ready.")
    except Exception as exc:
        logger.critical(
            "Renaiss Discord DB initialization failed (error=%s).",
            type(exc).__name__,
        )
        raise RuntimeError("Renaiss Discord DB initialization failed") from None


def _button_view(url: str | None) -> discord.ui.View | None:
    if not url:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="View on Renaiss", url=url))
    return view


def _category_value(category: str | None) -> str:
    return resolve_category_key(category) or "pokemon_tcg"


@client.tree.command(name="open", description="Open Renaiss TCG card packs.")
@app_commands.describe(
    category="pokemon_tcg or one_piece_tcg",
    pack_type="free",
    count=f"Number of packs to open, up to {MAX_BATCH_PACKS}",
)
async def open_command(
    interaction: discord.Interaction,
    category: str = "pokemon_tcg",
    pack_type: str = "free",
    count: app_commands.Range[int, 1, MAX_BATCH_PACKS] = 1,
) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not private_free_packs_enabled():
        await interaction.followup.send(
            "Private free packs are closed during the collector-market pilot. "
            "Join blind community spawns with `c` in the Telegram collaboration room. "
            "In-game collection only; no physical card or NFT ownership.",
            ephemeral=True,
        )
        return
    normalized_pack_type = normalize_pack_type(pack_type)
    if normalized_pack_type == "premium":
        await interaction.followup.send(
            "Premium and RP packs are not part of the current Renaiss pilot.",
            ephemeral=True,
        )
        return
    category_key = _category_value(category)
    if not get_category(category_key).enabled:
        await interaction.followup.send(
            "That card category is planned but is not open for packs yet.",
            ephemeral=True,
        )
        return
    request_id = f"discord:{interaction.id}:open"
    try:
        reservation = await reserve_command_free_packs(
            request_id=request_id,
            user_id=interaction.user.id,
            chat_id=interaction.channel_id,
            platform="discord",
            category=category_key,
            requested_count=int(count),
            daily_limit=DAILY_FREE_PACKS,
        )
    except Exception as exc:
        logger.warning("Discord pack reservation failed user=%s: %s", interaction.user.id, exc)
        await interaction.followup.send(
            "Pack opening is temporarily unavailable because your daily quota could not be verified.",
            ephemeral=True,
        )
        return
    if not reservation.created:
        if reservation.status == "quota_full":
            text = (
                f"Daily free packs used ({DAILY_FREE_PACKS}/{DAILY_FREE_PACKS}). "
                "Join a Telegram blind spawn with c."
            )
        elif reservation.status == "completed":
            text = "This pack request was already completed. Check `/mycards`."
        elif reservation.status == "reserved":
            text = "This pack request is already being processed."
        else:
            text = "That pack request expired. Run `/open` again."
        await interaction.followup.send(text, ephemeral=True)
        return
    try:
        result = await open_pack(
            user_id=interaction.user.id,
            category_key=category_key,
            pack_type="free",
            count=reservation.allowed_count,
        )
    except Exception as exc:
        logger.warning("Discord pack generation failed user=%s: %s", interaction.user.id, exc)
        try:
            await cancel_pack_open_reservation(request_id)
        except Exception:
            logger.warning("Discord pack reservation cancellation failed request=%s", request_id)
        await interaction.followup.send("Pack opening failed. No pack was recorded.", ephemeral=True)
        return
    try:
        await finalize_command_free_pack(
            request_id=request_id,
            user_id=interaction.user.id,
            chat_id=interaction.channel_id,
            result=result,
        )
    except Exception as exc:
        logger.exception("Discord pack finalization failed request=%s: %s", request_id, exc)
        try:
            confirmation = await reserve_command_free_packs(
                request_id=request_id,
                user_id=interaction.user.id,
                chat_id=interaction.channel_id,
                platform="discord",
                category=category_key,
                requested_count=int(count),
                daily_limit=DAILY_FREE_PACKS,
            )
        except Exception:
            confirmation = None
        if confirmation is None or confirmation.status != "completed":
            await interaction.followup.send(
                "The pack result could not be safely saved, so no successful opening is being announced.",
                ephemeral=True,
            )
            return
        logger.warning("Recovered completed Discord pack after uncertain finalize request=%s", request_id)
    tracked_result, render_result = await asyncio.gather(
        build_tracked_url(
            result.best_price.referral_url,
            user_id=interaction.user.id,
            chat_id=interaction.channel_id,
            local_card_id=result.best_card.local_card_id or None,
            source="discord_pack",
        ),
        render_overlay_card(result.best_card, result.best_price),
        return_exceptions=True,
    )
    if isinstance(tracked_result, BaseException):
        logger.warning("Discord pack tracking link failed request=%s: %s", request_id, tracked_result)
        tracked_url = result.best_price.referral_url
    else:
        tracked_url = tracked_result
    if isinstance(render_result, BaseException):
        logger.warning("Discord pack render failed request=%s: %s", request_id, render_result)
        png = None
    else:
        png = render_result
    send_kwargs = {
        "content": format_pack_message(result),
        "view": _button_view(tracked_url),
        "ephemeral": True,
    }
    if png:
        send_kwargs["file"] = discord.File(BytesIO(png), filename="renaiss_pack_result.png")
    try:
        await interaction.followup.send(**send_kwargs)
    except discord.HTTPException as exc:
        if "file" not in send_kwargs:
            raise
        if getattr(exc, "status", None) != 400:
            logger.error(
                "Discord pack delivery is ambiguous; no automatic duplicate request=%s: %s",
                request_id,
                exc,
            )
            return
        logger.warning("Discord pack image delivery failed; retrying text request=%s", request_id)
        send_kwargs.pop("file", None)
        await interaction.followup.send(**send_kwargs)
    except Exception as exc:
        logger.error(
            "Discord pack delivery failed ambiguously; no automatic duplicate request=%s: %s",
            request_id,
            exc,
        )


@client.tree.command(name="price", description="Check a Renaiss reference price.")
@app_commands.describe(query="Example: Charizard or one_piece_tcg Luffy")
async def price_command(interaction: discord.Interaction, query: str) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    retry_after = _claim_price_slot(interaction.user.id)
    if retry_after:
        await interaction.followup.send(
            f"Price lookup is cooling down. Try again in {retry_after}s.",
            ephemeral=True,
        )
        return
    category, query_args = split_category_args(query.split())
    card_name = " ".join(query_args).strip()
    if not card_name:
        await interaction.followup.send(
            "Usage: `/price Charizard` or `/price one_piece_tcg Luffy`",
            ephemeral=True,
        )
        return
    card = CardIdentity(category=category, card_name=card_name, grade="PSA 10")
    price = await fetch_price(card)
    await interaction.followup.send(
        content=format_price_message(card.card_name, category, price),
        view=_button_view(
            await build_tracked_url(
                price.referral_url,
                user_id=interaction.user.id,
                chat_id=interaction.channel_id,
                local_card_id=card.local_card_id or None,
                source="discord_price",
            )
        ),
        ephemeral=True,
    )


@client.tree.command(name="mycards", description="View your Renaiss collection.")
async def mycards_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        from renaiss_bot.database.queries import get_collection_detail

        detail = await get_collection_detail(interaction.user.id)
    except Exception as exc:
        logger.warning("Discord collection lookup failed user=%s: %s", interaction.user.id, exc)
        await interaction.followup.send(
            "Your collection is temporarily unavailable because it could not be verified.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(format_collection_message(detail), ephemeral=True)


@client.tree.command(name="sets", description="View supported card categories.")
async def sets_command(interaction: discord.Interaction) -> None:
    from renaiss_bot.services.categories import list_categories

    lines = ["**Supported Card Categories**", "────────────"]
    for category in list_categories():
        state = "enabled" if category.enabled else "planned"
        lines.append(f"- **{category.label}** `{category.key}` : {state}")
        lines.append(f"  {category.description}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


def main() -> None:
    load_runtime_environment()
    _configure_logging()
    issues = _production_startup_issues()
    if issues:
        logger.error("Renaiss Discord startup refused: %s", "; ".join(issues))
        raise SystemExit(2)
    token = os.environ["RENAISS_DISCORD_TOKEN"].strip()
    client.run(token)


if __name__ == "__main__":
    main()
