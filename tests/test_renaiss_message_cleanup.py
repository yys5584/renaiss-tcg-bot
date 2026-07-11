"""Telegram group command cleanup tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.error import BadRequest

from renaiss_bot.handlers.message_cleanup import (
    command_delete_delay_seconds,
    delete_group_command_job,
    schedule_group_command_delete,
)


def _update(*, text: str, chat_type: str = "supergroup"):
    message = SimpleNamespace(message_id=77, text=text)
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001, type=chat_type),
        effective_message=message,
    )


@pytest.mark.parametrize("text", ["c", "C", "/flex", "/mycards@RenaissBot"])
async def test_group_commands_are_scheduled_for_sixty_second_cleanup(text):
    queue = SimpleNamespace(run_once=Mock())
    await schedule_group_command_delete(
        _update(text=text),
        SimpleNamespace(job_queue=queue),
    )

    callback, = queue.run_once.call_args.args
    kwargs = queue.run_once.call_args.kwargs
    assert callback is delete_group_command_job
    assert kwargs["when"] == 60
    assert kwargs["data"] == {"chat_id": -1001, "message_id": 77}
    assert kwargs["job_kwargs"] == {"misfire_grace_time": 300}


@pytest.mark.parametrize(
    ("text", "chat_type"),
    [("hello", "group"), ("c", "private"), ("/market", "private")],
)
async def test_non_commands_and_private_commands_are_not_scheduled(text, chat_type):
    queue = SimpleNamespace(run_once=Mock())
    await schedule_group_command_delete(
        _update(text=text, chat_type=chat_type),
        SimpleNamespace(job_queue=queue),
    )
    queue.run_once.assert_not_called()


async def test_cleanup_job_deletes_the_original_user_message():
    bot = SimpleNamespace(delete_message=AsyncMock())
    context = SimpleNamespace(
        bot=bot,
        job=SimpleNamespace(data={"chat_id": -1001, "message_id": 77}),
    )

    await delete_group_command_job(context)

    bot.delete_message.assert_awaited_once_with(chat_id=-1001, message_id=77)


async def test_cleanup_job_failure_does_not_escape():
    bot = SimpleNamespace(
        delete_message=AsyncMock(side_effect=BadRequest("message is already gone"))
    )
    context = SimpleNamespace(
        bot=bot,
        job=SimpleNamespace(data={"chat_id": -1001, "message_id": 77}),
    )

    await delete_group_command_job(context)


def test_cleanup_delay_is_bounded(monkeypatch):
    monkeypatch.setenv("RENAISS_COMMAND_DELETE_DELAY_SECONDS", "1")
    assert command_delete_delay_seconds() == 10
    monkeypatch.setenv("RENAISS_COMMAND_DELETE_DELAY_SECONDS", "999")
    assert command_delete_delay_seconds() == 300
    monkeypatch.setenv("RENAISS_COMMAND_DELETE_DELAY_SECONDS", "invalid")
    assert command_delete_delay_seconds() == 60
