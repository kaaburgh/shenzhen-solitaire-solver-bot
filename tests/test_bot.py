"""The conversation flow, driven against stand-ins for Telegram's objects.

Only the handful of attributes the handlers actually touch are faked, so these
break loudly if a handler starts reaching for something new.
"""

from __future__ import annotations

import pytest

from shenzhen.bot import handlers
from shenzhen.bot.handlers import BotConfig
from shenzhen.bot.storage import Sessions
from shenzhen.game import deal
from shenzhen.notation import board_to_text

SOLVABLE = board_to_text(deal(0))

DEAD = """
free: G9 R9 B9
flower: 1
foundations: 0 0 0
1: G1 DG G2 G3 DG
2: G4 G5 G6 DG
3: G7 G8 R1 DG
4: R2 DR R3 R4 DR
5: R5 R6 R7 DR
6: R8 B1 B2 DR
7: B3 DB B4 B5 DB
8: B6 DB B7 B8 DB
"""


class FakeChat:
    def __init__(self, chat_id: int = 1) -> None:
        self.id = chat_id

    async def send_action(self, action):  # noqa: D102
        return None


class FakeMessage:
    def __init__(self, chat: FakeChat, text: str | None = None, log: list | None = None) -> None:
        self.chat = chat
        self.text = text
        self.photo = []
        self.document = None
        self.log = log if log is not None else []

    async def reply_text(self, text, **kwargs):
        self.log.append((text, kwargs))
        return FakeMessage(self.chat, text, log=self.log)

    async def edit_text(self, text, **kwargs):
        self.log.append((text, kwargs))
        return self


class FakePhoto:
    """The smallest stand-in `handle_image` will accept for a photo."""

    file_size = 200_000

    def __init__(self, data: bytes = b"not really a png") -> None:
        self.data = data

    async def get_file(self):
        return self

    async def download_as_bytearray(self):
        return bytearray(self.data)


class FakeDocument(FakePhoto):
    """A screenshot sent the way /help asks for it: as a file."""

    def __init__(
        self,
        mime_type: str | None = "application/octet-stream",
        file_name: str | None = "IMG000.jpg",
        file_size: int = 1_400_000,
    ) -> None:
        super().__init__()
        self.mime_type = mime_type
        self.file_name = file_name
        self.file_size = file_size


class FakeUser:
    def __init__(self, language_code: str = "ru") -> None:
        self.language_code = language_code


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answered = False

    async def answer(self, *args, **kwargs):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.message.log.append((text, kwargs))


class FakeUpdate:
    def __init__(self, message=None, query=None, user=None, chat=None) -> None:
        self.message = message
        self.callback_query = query
        self.effective_message = message or query.message
        self.effective_chat = chat or (message.chat if message else query.message.chat)
        self.effective_user = user or FakeUser()


class FakeApplication:
    def __init__(self, bot_data: dict) -> None:
        self.bot_data = bot_data


class FakeContext:
    def __init__(self, application: FakeApplication) -> None:
        self.application = application
        #: what PTB puts there before calling the error handler
        self.error: Exception | None = None


@pytest.fixture
def context():
    return FakeContext(
        FakeApplication(
            {
                "config": BotConfig(max_nodes=150_000, time_limit=15.0, bank=None),
                "sessions": Sessions(),
                # None means "the default thread pool"; the solver still runs
                # off the event loop, which is all the handler cares about.
                "executor": None,
            }
        )
    )


def texts(log) -> str:
    body = "\n".join(text for text, _ in log)
    return body.replace("<pre>", "").replace("</pre>", "")


async def send_text(context, body: str, log: list):
    message = FakeMessage(FakeChat(), body, log=log)
    await handlers.handle_text(FakeUpdate(message=message), context)


async def press(context, data: str, log: list):
    query = FakeQuery(data, FakeMessage(FakeChat(), log=log))
    await handlers.on_callback(FakeUpdate(query=query), context)


async def send_file(context, document, log: list):
    message = FakeMessage(FakeChat(), log=log)
    message.document = document
    await handlers.handle_image(FakeUpdate(message=message), context)


@pytest.mark.asyncio
async def test_a_typed_board_is_echoed_back_for_confirmation(context):
    log: list = []
    await send_text(context, SOLVABLE, log)

    assert "1:" in texts(log)  # the rendered board
    keyboard = log[-1][1]["reply_markup"]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == ["solve", "fix"]


@pytest.mark.asyncio
async def test_confirming_solves_and_lists_five_moves(context):
    log: list = []
    await send_text(context, SOLVABLE, log)
    await press(context, "solve", log)

    body = texts(log)
    assert "Решение есть" in body
    moves = [line for line in body.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.")]
    assert len(moves) == 5


@pytest.mark.asyncio
async def test_the_next_button_continues_the_same_solution(context):
    log: list = []
    await send_text(context, SOLVABLE, log)
    await press(context, "solve", log)
    await press(context, "more", log)

    assert "6." in texts(log)
    session = context.application.bot_data["sessions"].get(1)
    assert session.shown == 10


@pytest.mark.asyncio
async def test_a_dead_board_is_reported_as_unwinnable(context):
    log: list = []
    await send_text(context, DEAD, log)
    await press(context, "solve", log)
    assert "Решения нет" in texts(log)


@pytest.mark.asyncio
async def test_an_impossible_board_is_rejected_with_the_reason(context):
    log: list = []
    await send_text(context, SOLVABLE.replace("free: . . .", "free: G1 . ."), log)
    body = texts(log)
    assert "Так не бывает" in body
    assert "G1" in body


@pytest.mark.asyncio
async def test_chatter_gets_the_usage_hint(context):
    log: list = []
    await send_text(context, "привет", log)
    assert "/help" in texts(log)


@pytest.mark.asyncio
async def test_screenshots_are_refused_when_no_templates_are_installed(context):
    log: list = []
    message = FakeMessage(FakeChat(), log=log)
    message.photo = [object()]
    await handlers.handle_image(FakeUpdate(message=message), context)
    assert "текстом" in texts(log)


@pytest.mark.asyncio
async def test_language_switches_to_english(context):
    log: list = []
    await press(context, "lang:en", log)
    await send_text(context, SOLVABLE, log)
    await press(context, "solve", log)
    assert "Winnable" in texts(log)


@pytest.mark.asyncio
async def test_fix_hands_back_an_editable_position(context):
    log: list = []
    await send_text(context, SOLVABLE, log)
    await press(context, "fix", log)
    body = texts(log)
    assert "free:" in body and "foundations:" in body


@pytest.mark.asyncio
async def test_a_rescaled_screenshot_is_explained_not_reported_as_deck_arithmetic(
    context, monkeypatch
):
    """What a user actually hit: a screenshot sent as a Telegram photo came
    back as "missing G3x1, B3x1, B4x1; duplicated G8x1, B2x2, B7x1". That is
    true and completely unactionable. The reply has to name the cause and the
    fix instead."""
    from shenzhen.vision.recognize import RecognitionError

    def blow_up(_data, _bank):
        raise RecognitionError(
            "missing G3x1, B3x1, B4x1; duplicated G8x1, B2x2, B7x1", card_w=97
        )

    context.application.bot_data["config"].bank = object()  # any non-None bank
    monkeypatch.setattr(handlers, "_recognize_bytes", blow_up)

    log: list = []
    message = FakeMessage(FakeChat(), log=log)
    message.photo = [FakePhoto()]
    await handlers.handle_image(FakeUpdate(message=message), context)

    body = texts(log)
    assert "97" in body, body            # says how small it came in
    assert "файлом" in body, body        # says what to do about it
    assert "missing" not in body, body   # and not the deck arithmetic


# --- screenshots sent as files ---------------------------------------------


@pytest.fixture
def reading(context, monkeypatch):
    """A bot that recognises whatever bytes it is handed as SOLVABLE."""
    from shenzhen.textio import parse_board
    from shenzhen.vision.recognize import Recognition

    context.application.bot_data["config"].bank = object()
    monkeypatch.setattr(
        handlers, "_recognize_bytes", lambda _data, _bank: Recognition(parse_board(SOLVABLE))
    )
    return context


@pytest.mark.parametrize(
    "mime_type",
    [
        "image/png",
        # What the phone actually sent, and what used to be ignored.
        "application/octet-stream",
        None,
    ],
)
@pytest.mark.asyncio
async def test_a_screenshot_sent_as_a_file_is_read_like_any_other(reading, mime_type):
    log: list = []
    await send_file(reading, FakeDocument(mime_type=mime_type), log)

    assert "1:" in texts(log)  # the rendered board, not silence
    keyboard = log[-1][1]["reply_markup"]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == ["solve", "fix"]


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_picture_gets_told_so(reading):
    log: list = []
    await send_file(reading, FakeDocument(mime_type="application/pdf", file_name="rules.pdf"), log)
    assert "не похоже на картинку" in texts(log)


@pytest.mark.asyncio
async def test_a_file_too_big_to_fetch_is_explained_before_the_download(reading):
    """Telegram will not hand a bot more than 20 MB, and finding that out from
    a failed getFile is both slower and less clear than saying so up front."""
    log: list = []
    await send_file(reading, FakeDocument(file_size=40 * 1024 * 1024), log)
    body = texts(log)
    assert "слишком большой" in body
    assert "20" in body


@pytest.mark.asyncio
async def test_a_download_that_fails_is_reported_not_swallowed(reading):
    from telegram.error import TimedOut

    class Unfetchable(FakeDocument):
        async def get_file(self):
            raise TimedOut()

    log: list = []
    await send_file(reading, Unfetchable(), log)
    assert "Не получилось забрать файл" in texts(log)


@pytest.mark.asyncio
async def test_an_unexpected_failure_still_answers_the_chat(context):
    """Anything the handlers do not catch reaches `on_error`; before it
    existed the traceback went to the server log and the chat went quiet."""
    log: list = []
    message = FakeMessage(FakeChat(), log=log)
    update = FakeUpdate(message=message)
    context.error = RuntimeError("boom")

    await handlers.on_error(update, context)

    assert "boom" in texts(log)
