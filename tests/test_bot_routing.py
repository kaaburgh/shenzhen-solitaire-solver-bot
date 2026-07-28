"""What the bot actually listens to.

The handlers are wired up once, in :func:`build_application`, and an update
that matches nothing in that table is answered with silence -- which from the
chat looks exactly like the bot ignoring you.  That is the bug these tests
exist for: a screenshot sent *as a file*, which is what /help asks for, used to
match no handler at all.

So they push real :class:`telegram.Update` objects through the real
registration instead of calling the handlers directly.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from telegram import CallbackQuery, Chat, Document, Message, PhotoSize, Update, User
from telegram.ext import CommandHandler

from shenzhen.bot import handlers
from shenzhen.bot.main import build_application

REPO = Path(__file__).resolve().parent.parent
CHAT = Chat(id=1, type=Chat.PRIVATE)
USER = User(id=1, first_name="Tester", is_bot=False)


@pytest.fixture
def application(monkeypatch):
    monkeypatch.setenv("SHENZHEN_TEMPLATES", str(REPO / "templates" / "default"))
    monkeypatch.setenv("SHENZHEN_WORKERS", "1")
    # The token is never used: nothing here talks to Telegram.
    app = build_application("123456:this-token-is-never-used")
    try:
        yield app
    finally:
        app.bot_data["executor"].shutdown(wait=False, cancel_futures=True)


def message(**kwargs) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=dt.datetime.now(dt.UTC),
            chat=CHAT,
            from_user=USER,
            **kwargs,
        ),
    )


def document(mime_type: str | None, file_name: str | None = None) -> Update:
    return message(
        document=Document(
            file_id="f",
            file_unique_id="u",
            file_name=file_name,
            mime_type=mime_type,
        )
    )


def route(application, update: Update):
    """The callback that would run, or None if the update goes nowhere."""
    for group in sorted(application.handlers):
        for handler in application.handlers[group]:
            if handler.check_update(update):
                return handler.callback
    return None


@pytest.mark.parametrize(
    ("mime_type", "file_name"),
    [
        # What a desktop client labels a screenshot sent as a file.
        ("image/png", "board.png"),
        ("image/jpeg", "IMG000.jpg"),
        # ...and what a phone sends instead.  Filtering on "image/*" dropped
        # these two on the floor, which is the whole reason for this file.
        ("application/octet-stream", "IMG000.jpg"),
        (None, None),
    ],
)
def test_a_screenshot_sent_as_a_file_reaches_the_image_handler(
    application, mime_type, file_name
):
    assert route(application, document(mime_type, file_name)) is handlers.handle_image


def test_a_compressed_photo_still_reaches_the_image_handler(application):
    update = message(photo=(PhotoSize("f", "u", width=1280, height=590),))
    assert route(application, update) is handlers.handle_image


def test_a_file_that_is_not_a_picture_is_answered_rather_than_dropped(application):
    """Routing is deliberately generous; `handle_image` does the sorting.

    Better to reach the handler and be told "that is not a picture" than to
    match nothing and get no reply at all.
    """
    assert route(application, document("application/pdf", "rules.pdf")) is handlers.handle_image


def test_typed_positions_go_to_the_text_handler(application):
    assert route(application, message(text="1: G1 G2")) is handlers.handle_text


def test_button_presses_go_to_the_callback_handler(application):
    update = Update(
        update_id=1,
        callback_query=CallbackQuery(
            id="1", from_user=USER, chat_instance="whatever", data="solve"
        ),
    )
    assert route(application, update) is handlers.on_callback


def test_the_documented_commands_are_registered(application):
    commands = {
        command
        for group in application.handlers.values()
        for handler in group
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    assert commands == {"start", "help", "lang"}


def test_failures_are_reported_instead_of_being_swallowed(application):
    """Without an error handler PTB logs the traceback and the chat stays
    quiet -- the same silence, from a different cause."""
    assert handlers.on_error in application.error_handlers
