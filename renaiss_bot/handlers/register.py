"""Register Telegram handlers for the standalone Renaiss bot."""

from __future__ import annotations

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from renaiss_bot.handlers.callbacks import on_callback
from renaiss_bot.handlers.cardpack import cmd_mycards, cmd_open, cmd_pack
from renaiss_bot.handlers.flex import cmd_flex, on_flex_props
from renaiss_bot.handlers.market import cmd_market, on_market
from renaiss_bot.handlers.price import cmd_price
from renaiss_bot.handlers.spawn import catch_handler, on_spawn_guess
from renaiss_bot.handlers.start import cmd_sets, cmd_start


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sets", cmd_sets))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("pack", cmd_pack))
    app.add_handler(CommandHandler("mycards", cmd_mycards))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("flex", cmd_flex))
    app.add_handler(CommandHandler("market", cmd_market))

    # 시즌1식 스폰 잡기: 'c' 한 글자로 진행 중인 스폰 포획
    app.add_handler(MessageHandler(filters.Regex(r"^[cC]$"), catch_handler))
    # 구체 패턴 콜백은 generic "renaiss:" 보다 먼저 등록해야 잡힌다.
    app.add_handler(CallbackQueryHandler(on_market, pattern=r"^renaiss:market(?::|$)"))
    app.add_handler(CallbackQueryHandler(on_spawn_guess, pattern=r"^renaiss:spawn_guess:"))
    app.add_handler(CallbackQueryHandler(on_flex_props, pattern=r"^renaiss:props:"))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^renaiss:"))
