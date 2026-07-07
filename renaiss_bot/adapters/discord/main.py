"""Discord entrypoint for Renaiss Edition."""

from __future__ import annotations

import logging
import os
from io import BytesIO

import discord
from discord import app_commands
from dotenv import load_dotenv

from renaiss_bot.adapters.discord.formatting import (
    format_collection_message,
    format_pack_message,
    format_price_message,
)
from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.database.schema import create_tables
from renaiss_bot.renderers.overlay import render_overlay_card
from renaiss_bot.services.categories import resolve_category_key, split_category_args
from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.pack import open_pack
from renaiss_bot.services.pack_rules import MAX_BATCH_PACKS, normalize_pack_type
from renaiss_bot.services.pricing import fetch_price

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
)
logger = logging.getLogger(__name__)


class RenaissDiscordClient(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await _init_db()
        guild_id = os.getenv("RENAISS_DISCORD_GUILD_ID", "").strip()
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Discord commands synced to guild %s.", guild_id)
        else:
            await self.tree.sync()
            logger.info("Discord commands synced globally.")

    async def close(self) -> None:
        await close_db()
        await super().close()


client = RenaissDiscordClient()


async def _init_db() -> None:
    if os.getenv("RENAISS_SKIP_DB", "").strip().lower() in {"1", "true", "yes"}:
        logger.info("RENAISS_SKIP_DB is set; Discord DB initialization skipped.")
        return
    if not os.getenv("DATABASE_URL"):
        logger.warning("DATABASE_URL not set; Discord DB initialization skipped.")
        return
    try:
        pool = await get_db()
        await create_tables(pool)
        logger.info("Renaiss Discord DB tables ready.")
    except Exception as exc:
        logger.warning("Renaiss Discord DB initialization skipped: %s", exc, exc_info=True)


def _button_view(url: str | None) -> discord.ui.View | None:
    if not url:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Renaiss에서 가격 보기", url=url))
    return view


def _category_value(category: str | None) -> str:
    return resolve_category_key(category) or "pokemon_tcg"


@client.tree.command(name="open", description="Renaiss TCG 카드팩을 엽니다.")
@app_commands.describe(
    category="pokemon_tcg, one_piece_tcg, 원피스",
    pack_type="free 또는 premium",
    count=f"한 번에 열 팩 수. 최대 {MAX_BATCH_PACKS}팩",
)
async def open_command(
    interaction: discord.Interaction,
    category: str = "pokemon_tcg",
    pack_type: str = "free",
    count: app_commands.Range[int, 1, MAX_BATCH_PACKS] = 1,
) -> None:
    await interaction.response.defer(thinking=True)
    result = await open_pack(
        user_id=interaction.user.id,
        category_key=_category_value(category),
        pack_type=normalize_pack_type(pack_type),
        count=int(count),
    )
    png = render_overlay_card(result.best_card, result.best_price)
    file = discord.File(BytesIO(png), filename="renaiss_pack_result.png")
    await interaction.followup.send(
        content=format_pack_message(result),
        file=file,
        view=_button_view(result.best_price.referral_url),
    )

    try:
        from renaiss_bot.database.queries import log_pack_event, register_pack_cards

        await register_pack_cards(user_id=interaction.user.id, chat_id=interaction.channel_id, result=result)
        await log_pack_event(
            user_id=interaction.user.id,
            chat_id=interaction.channel_id,
            category=result.category,
            best_card=result.best_card,
            price=result.best_price,
            pack_type=result.pack_type,
            pack_count=result.pack_count,
            card_count=len(result.cards),
            pool_source=result.pool_source,
        )
    except Exception:
        logger.debug("Discord pack event persistence skipped.", exc_info=True)


@client.tree.command(name="price", description="Renaiss 가격을 확인합니다.")
@app_commands.describe(query="예: 리자몽 또는 one_piece_tcg luffy")
async def price_command(interaction: discord.Interaction, query: str) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    category, query_args = split_category_args(query.split())
    card_name = " ".join(query_args).strip()
    if not card_name:
        await interaction.followup.send("사용법: `/price 리자몽` 또는 `/price one_piece_tcg luffy`", ephemeral=True)
        return
    card = CardIdentity(category=category, card_name=card_name, grade="PSA 10")
    price = await fetch_price(card)
    await interaction.followup.send(
        content=format_price_message(card.card_name, category, price),
        view=_button_view(price.referral_url),
        ephemeral=True,
    )


@client.tree.command(name="mycards", description="내 Renaiss 컬렉션을 봅니다.")
async def mycards_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)
    detail = None
    try:
        from renaiss_bot.database.queries import get_collection_detail

        detail = await get_collection_detail(interaction.user.id)
    except Exception:
        logger.debug("Discord collection lookup skipped.", exc_info=True)
    await interaction.followup.send(format_collection_message(detail), ephemeral=True)


@client.tree.command(name="sets", description="지원 카테고리를 봅니다.")
async def sets_command(interaction: discord.Interaction) -> None:
    from renaiss_bot.services.categories import list_categories

    lines = ["**지원 카드 카테고리**", "────────────"]
    for category in list_categories():
        state = "사용 가능" if category.enabled else "확장 예정"
        lines.append(f"- **{category.label}** `{category.key}` : {state}")
        lines.append(f"  {category.description}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


def main() -> None:
    token = os.getenv("RENAISS_DISCORD_TOKEN")
    if not token:
        logger.error("RENAISS_DISCORD_TOKEN not set.")
        return
    client.run(token)


if __name__ == "__main__":
    main()
