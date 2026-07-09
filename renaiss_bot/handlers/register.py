"""Register Telegram handlers for the standalone Renaiss bot."""

from __future__ import annotations

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from renaiss_bot.handlers.callbacks import on_callback
from renaiss_bot.handlers.cardpack import cmd_mycards, cmd_open, cmd_pack, cmd_rank
from renaiss_bot.handlers.drop import call_drop_handler, feed_drop_handler
from renaiss_bot.handlers.flex import cmd_flex, on_flex_props
from renaiss_bot.handlers.price import cmd_price
from renaiss_bot.handlers.quiz import on_quiz_answer
from renaiss_bot.handlers.trade import cmd_sell, on_sell
from renaiss_bot.handlers.spawn import catch_handler
from renaiss_bot.handlers.start import cmd_sets, cmd_start


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sets", cmd_sets))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("pack", cmd_pack))
    app.add_handler(CommandHandler("mycards", cmd_mycards))
    app.add_handler(CommandHandler("rank", cmd_rank))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("flex", cmd_flex))
    app.add_handler(CommandHandler("sell", cmd_sell))

    # 시즌1식 스폰 잡기: 'c' 한 글자로 진행 중인 스폰 포획
    app.add_handler(MessageHandler(filters.Regex(r"^[cC]$"), catch_handler))
    # 레거시 d/f 드랍 (스폰 시스템으로 교체 예정 — 당분간 병행)
    app.add_handler(MessageHandler(filters.Regex(r"^[dD]$"), call_drop_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^[fF]$"), feed_drop_handler))

    # 구체 패턴 콜백은 generic "renaiss:" 보다 먼저 등록해야 잡힌다.
    app.add_handler(CallbackQueryHandler(on_quiz_answer, pattern=r"^renaiss:quiz:"))
    app.add_handler(CallbackQueryHandler(on_flex_props, pattern=r"^renaiss:props:"))
    app.add_handler(CallbackQueryHandler(on_sell, pattern=r"^renaiss:sell:"))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^renaiss:"))
