"""The conversation flow, driven against stand-ins for Telegram's objects.

Only the handful of attributes the handlers actually touch are faked, so these
break loudly if a handler starts reaching for something new.
"""

from __future__ import annotations

import pytest

from shenzhen.bot import handlers
from shenzhen.bot.handlers import BotConfig
from shenzhen.bot.storage import Sessions
from shenzhen.cards import parse_card
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

    async def get_file(self):
        return self

    async def download_as_bytearray(self):
        return bytearray(b"not really a png")


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
        self.effective_chat = chat or (message.chat if message else query.message.chat)
        self.effective_user = user or FakeUser()


class FakeApplication:
    def __init__(self, bot_data: dict) -> None:
        self.bot_data = bot_data


class FakeContext:
    def __init__(self, application: FakeApplication) -> None:
        self.application = application


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


# --- asking about cards the deck could not settle --------------------------


def _screenshot(unsure):
    """A Recognition as `recognize` would have produced it for a board whose
    named slots came out shaky.  Runs the real resolver, so what the handlers
    get here is what they would get from a real picture."""
    from test_resolve import AMBIGUOUS, screen

    from shenzhen.vision.recognize import Recognition
    from shenzhen.vision.resolve import resolve

    view = screen(AMBIGUOUS, unsure=unsure)
    resolution = resolve(view.skeleton, view.reads)
    return view, Recognition(
        state=resolution.state,
        reads=view.reads,
        skeleton=view.skeleton,
        resolution=resolution,
    )


async def send_photo(context, log, recognition, monkeypatch):
    context.application.bot_data["config"].bank = object()
    message = FakeMessage(FakeChat(), log=log)
    message.photo = [FakePhoto()]
    monkeypatch.setattr(handlers, "_recognize_bytes", lambda _data, _bank: recognition)
    await handlers.handle_image(FakeUpdate(message=message), context)


def buttons(log):
    keyboard = log[-1][1]["reply_markup"]
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_cards_the_deck_settles_are_never_asked_about(context, monkeypatch):
    """Three shaky reads, none of which collide -- so the deck names all three
    and the user is asked nothing at all."""
    _, recognition = _screenshot({
        "1.1": ["G1", "G5"],
        "3.1": ["R2", "R7"],
        "5.1": ["B3", "B8"],
    })
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    body = texts(log)
    assert "что там?" not in body, body
    assert "1:" in body                      # went straight to the board
    assert buttons(log) == ["solve", "fix"]
    assert "подставил" in body, body         # and said it had filled them in


@pytest.mark.asyncio
async def test_one_open_card_is_put_as_a_question_with_the_answers_as_buttons(context, monkeypatch):
    view, recognition = _screenshot({"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    body = texts(log)
    assert "что там?" in body, body
    assert "колонка" in body, body           # says where to look, in words
    picks = [b for b in buttons(log) if b.startswith("pick:")]
    assert len(picks) == 2                   # G3 or G8, one tap either way
    assert "wide:" in " ".join(buttons(log))
    assert context.application.bot_data["sessions"].get(1).pending is not None


@pytest.mark.asyncio
async def test_answering_the_one_question_settles_the_rest_and_shows_the_board(context, monkeypatch):
    view, recognition = _screenshot({"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    answer = next(b for b in buttons(log) if b.startswith("pick:"))
    await press(context, answer, log)

    body = texts(log)
    assert buttons(log) == ["solve", "fix"]  # done asking
    assert "Всё верно?" in body
    session = context.application.bot_data["sessions"].get(1)
    assert session.pending is None
    assert session.board == recognition.state


@pytest.mark.asyncio
async def test_none_of_these_widens_the_choice_instead_of_dead_ending(context, monkeypatch):
    view, recognition = _screenshot({
        # B7 scored ninth here, past where the shortlist stops looking.
        "5.1": ["B3", "B4", "B5", "B6", "B8", "B9", "G1", "G2", "B7"],
        "5.2": ["B4", "B3"],
        "5.5": ["B7", "B3"],
    })
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    offered = [b for b in buttons(log) if b.startswith("pick:")]
    await press(context, next(b for b in buttons(log) if b.startswith("wide:")), log)
    widened = [b for b in buttons(log) if b.startswith("pick:")]

    assert set(offered) < set(widened), (offered, widened)


@pytest.mark.asyncio
async def test_an_answer_that_breaks_the_deck_is_refused_rather_than_accepted(context, monkeypatch):
    view, recognition = _screenshot({"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    session = context.application.bot_data["sessions"].get(1)
    # Force both slots to G3, which the deck cannot supply twice.
    first, second = view.index_of("1.3"), view.index_of("2.3")
    g3 = parse_card("G3")
    session.pending.pinned = {first: g3}
    await press(context, f"pick:{session.pending.token}:{second}:{g3}", log)

    assert "не сходится" in texts(log)
    assert session.pending is not None       # earlier answers kept


@pytest.mark.asyncio
async def test_bailing_out_to_typing_keeps_the_best_reading_so_far(context, monkeypatch):
    view, recognition = _screenshot({"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)
    await press(context, "fix", log)

    body = texts(log)
    assert "free:" in body and "1:" in body   # something to edit, not nothing
    assert context.application.bot_data["sessions"].get(1).pending is None


@pytest.mark.asyncio
async def test_the_users_own_answer_is_not_reported_back_as_deduced(context, monkeypatch):
    """After answering, the closing note should count only what the deck
    worked out -- not the card the user just supplied."""
    view, recognition = _screenshot({"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    await press(context, next(b for b in buttons(log) if b.startswith("pick:")), log)

    # Two shaky cards, one answered by hand and one falling out of it: the
    # note should either be absent or say one, never two.
    body = texts(log)
    assert "подставил" not in body or " 1 " in body, body


@pytest.mark.asyncio
async def test_a_button_from_an_earlier_screenshot_is_not_taken_as_an_answer(
    context, monkeypatch
):
    """Old keyboards never go away. A read index from the first screenshot
    addresses a different card on the second, so applying it there would be
    accepted wherever it happened to be deck-legal and quietly give the wrong
    board."""
    view, first = _screenshot({"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    log: list = []
    await send_photo(context, log, first, monkeypatch)
    stale = next(b for b in buttons(log) if b.startswith("pick:"))

    _, second = _screenshot({"3.3": ["R4", "R5"], "3.4": ["R5", "R4"]})
    await send_photo(context, log, second, monkeypatch)

    session = context.application.bot_data["sessions"].get(1)
    before = dict(session.pending.pinned)
    await press(context, stale, log)

    assert "неактуален" in texts(log)
    assert session.pending.pinned == before  # the stale answer changed nothing
