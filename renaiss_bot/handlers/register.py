"""Register Telegram handlers for the standalone Renaiss bot."""

from __future__ import annotations

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from renaiss_bot.handlers.callbacks import on_callback
from renaiss_bot.handlers.ceremony import ceremony_gate
from renaiss_bot.handlers.cardpack import cmd_mycards, cmd_open, cmd_pack
from renaiss_bot.handlers.flex import cmd_flex, on_flex_props
from renaiss_bot.handlers.market import cmd_market, on_market
from renaiss_bot.handlers.message_cleanup import schedule_group_command_delete
from renaiss_bot.handlers.price import cmd_price
from renaiss_bot.handlers.spawn import catch_handler, on_spawn_guess
from renaiss_bot.handlers.spawn_admin import (
    force_spawn_handler,
    spawn_off_handler,
    spawn_on_handler,
)
from renaiss_bot.handlers.start import cmd_sets, cmd_start


def register_handlers(app: Application) -> None:
    # 22:00 KST 랭킹 발표 동안 그룹 상호작용을 전부 멈춘다 (DM은 계속 동작).
    app.add_handler(TypeHandler(Update, ceremony_gate), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sets", cmd_sets))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("pack", cmd_pack))
    app.add_handler(CommandHandler("mycards", cmd_mycards))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("flex", cmd_flex))
    app.add_handler(CommandHandler("market", cmd_market))
    # 운영자 전용 스폰 제어 (BotFather 명령 목록에는 등록하지 않는다)
    app.add_handler(CommandHandler("spawnon", spawn_on_handler))
    app.add_handler(CommandHandler("spawnoff", spawn_off_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^(?i:force)$"), force_spawn_handler))

    # 시즌1식 스폰 잡기: 'c' 한 글자로 진행 중인 스폰 포획
    app.add_handler(MessageHandler(filters.Regex(r"^[cC]$"), catch_handler))
    # 기능 핸들러와 별도 그룹에서 실행해, 그룹 명령 원문만 60초 뒤 정리한다.
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & (filters.COMMAND | filters.Regex(r"^[cC]$") | filters.Regex(r"^(?i:force)$")),
            schedule_group_command_delete,
        ),
        group=1,
    )
    # 구체 패턴 콜백은 generic "renaiss:" 보다 먼저 등록해야 잡힌다.
    app.add_handler(CallbackQueryHandler(on_market, pattern=r"^renaiss:market(?::|$)"))
    app.add_handler(CallbackQueryHandler(on_spawn_guess, pattern=r"^renaiss:spawn_guess:"))
    app.add_handler(CallbackQueryHandler(on_flex_props, pattern=r"^renaiss:props:"))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^renaiss:"))
