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
from shenzhen.notation import board_to_text, card_mark

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

    async def reply_photo(self, photo, caption=None, **kwargs):
        self.log.append((caption or "", {"photo": photo, **kwargs}))
        return FakeMessage(self.chat, caption, log=self.log)

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
        #: which edit method the handler reached for, so the tests can catch
        #: the one Telegram would have rejected
        self.edited_as: str | None = None

    async def answer(self, *args, **kwargs):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        assert not self.message.photo, "Telegram rejects editMessageText on a photo"
        self.edited_as = "text"
        self.message.log.append((text, kwargs))

    async def edit_message_caption(self, caption=None, **kwargs):
        assert self.message.photo, "a caption belongs to a photo"
        self.edited_as = "caption"
        self.message.log.append((caption or "", kwargs))


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


async def press(context, data: str, log: list, on_photo: bool = False):
    """Press a button. ``on_photo`` when the keyboard is under a crop, which is
    what a question about a card the bot could show a picture of looks like."""
    message = FakeMessage(FakeChat(), log=log)
    if on_photo:
        message.photo = [object()]
    query = FakeQuery(data, message)
    await handlers.on_callback(FakeUpdate(query=query), context)
    return query


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
    """The card is named the way it is drawn -- 🟢1, not G1 -- because the
    complaint's whole job is to send someone back to the screen to find it.
    The text it came from is in letters, so the two are lined up for them."""
    log: list = []
    await send_text(context, SOLVABLE.replace("free: . . .", "free: G1 . ."), log)
    body = texts(log)
    assert "Так не бывает" in body
    assert card_mark(parse_card("G1")) in body, body
    assert "🟢 = G" in body, body


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


def _misread(text: str, slot: str, card: int):
    """A :class:`RecognitionError` carrying a reading of ``text`` with the card
    at ``slot`` misread as ``card`` -- which is what makes it not add up."""
    from shenzhen.cards import make_card
    from shenzhen.game import InvalidBoard
    from shenzhen.textio import parse_board
    from shenzhen.vision.classify import Guess
    from shenzhen.vision.layout import Box
    from shenzhen.vision.recognize import ReadCard, RecognitionError
    from shenzhen.vision.resolve import Skeleton

    board = parse_board(text)
    reads: list[ReadCard] = []
    columns = []
    for number, column in enumerate(board.columns, start=1):
        group = []
        for depth, seen in enumerate(column, start=1):
            where = f"{number}.{depth}"
            seen = card if where == slot else seen
            reads.append(
                ReadCard(
                    card=seen,
                    guess=Guess(card=seen, confidence=0.9, margin=0.2, colour=None),
                    where=where,
                    box=Box(0, 0, 1, 1),
                )
            )
            group.append(len(reads) - 1)
        columns.append(tuple(group))

    # The collected cards are read too, so the only card this reading is short
    # of is the one that was misread -- otherwise the complaint would blame the
    # foundations for every board the fixture builds.
    foundations: list[int | None] = [None, None, None]
    for suit, top in enumerate(board.foundations):
        if not top:
            continue
        seen = make_card(suit, top)
        reads.append(
            ReadCard(
                card=seen,
                guess=Guess(card=seen, confidence=0.9, margin=0.2, colour=None),
                where=f"foundation{suit + 1}",
                box=Box(0, 0, 1, 1),
            )
        )
        foundations[suit] = len(reads) - 1

    skeleton = Skeleton(
        columns=tuple(columns),
        free=(None, None, None),
        locked=(),
        flower_slot=board.flower,
        foundations=tuple(foundations),
    )
    # Take the complaint from the reading itself rather than writing one out,
    # so the fixture carries the same structured mismatch the recogniser would
    # have raised -- including the cards it names.
    try:
        skeleton.build([read.card for read in reads])
    except InvalidBoard as exc:
        complaint = exc
    else:  # pragma: no cover -- the misread is what makes it illegal
        raise AssertionError("that misreading is a legal board")

    return RecognitionError(
        str(complaint),
        reads=reads,
        card_w=97,
        skeleton=skeleton,
        deck=complaint.deck,
    )


@pytest.mark.asyncio
async def test_a_reading_that_does_not_add_up_comes_back_for_the_user_to_correct(
    context, monkeypatch
):
    """What a user actually hit: a screenshot sent as a Telegram photo came
    back as "missing G3x1, B3x1, B4x1; duplicated G8x1, B2x2, B7x1". That is
    true and completely unactionable.

    Nearly every card in a reading like that is right, so the reply hands the
    whole thing back in the text notation: the user fixes the two that are
    wrong and sends it back, rather than being told to go and take a better
    screenshot."""
    error = _misread(SOLVABLE, "1.1", parse_card("G8"))

    def blow_up(_data, _bank):
        raise error

    context.application.bot_data["config"].bank = object()  # any non-None bank
    monkeypatch.setattr(handlers, "_recognize_bytes", blow_up)

    log: list = []
    message = FakeMessage(FakeChat(), log=log)
    message.photo = [FakePhoto()]
    await handlers.handle_image(FakeUpdate(message=message), context)

    body = texts(log)
    assert error.draft in body, body     # the reading, ready to be edited
    assert "97" in body, body            # says how small it came in
    assert "файлом" in body, body        # and what would avoid the problem

    # The cards that do not add up are named as they are drawn, and the draft
    # below them is in the typed notation, so the message says which is which.
    assert "не хватает" in body, body
    assert card_mark(parse_card("G1")) in body, body   # the card that was eaten
    assert card_mark(parse_card("G8")) in body, body   # and what ate it
    assert "G1x1" not in body, body
    assert "🟢 = G" in body, body


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

    # Read, accepted and answered, rather than ignored for its mime type.
    assert "Решение есть" in texts(log), texts(log)


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


async def send_photo(context, log, recognition, monkeypatch, crops=None):
    """Hand the bot a screenshot that recognises as ``recognition``.

    ``crops`` stands in for the pictures cut out of the real bytes -- the
    stand-in reads carry no boxes to cut from, and what the handlers do with a
    crop is separate from how it was made.
    """
    context.application.bot_data["config"].bank = object()
    message = FakeMessage(FakeChat(), log=log)
    message.photo = [FakePhoto()]
    monkeypatch.setattr(handlers, "_recognize_bytes", lambda _data, _bank: recognition)
    monkeypatch.setattr(
        handlers,
        "_crops",
        lambda _data, _result, indices: {i: crops[i] for i in indices if i in (crops or {})},
    )
    await handlers.handle_image(FakeUpdate(message=message), context)


def buttons(log):
    keyboard = log[-1][1]["reply_markup"]
    return [button.callback_data for row in keyboard.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_a_reading_nothing_disagreed_with_goes_straight_to_the_answer(
    context, monkeypatch
):
    """Three shaky reads, none of which collide, and the deck came to the same
    answer the matcher did on all three. Two independent verdicts agreeing on
    forty cards that add up to exactly one deck is not something a human can
    improve on, so there is nothing to ask and no reason to charge a tap for
    the privilege."""
    _, recognition = _screenshot({
        "1.1": ["G1", "G5"],
        "3.1": ["R2", "R7"],
        "5.1": ["B3", "B8"],
    })
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    body = texts(log)
    assert "что там?" not in body, body      # no question
    assert "сверь" not in body, body         # and no confirmation either
    assert "Решение есть" in body, body

    # The way back is on the answer, since nobody was asked on the way in.
    assert buttons(log[:-1]) == ["board", "fix"]


@pytest.mark.asyncio
async def test_the_card_the_deck_overruled_is_the_only_one_put_up(context, monkeypatch):
    """G3 misread as G8 and put back by the deck: one slot where the two ways
    of reading the board disagreed, and the only slot worth a human's eyes."""
    _, recognition = _screenshot({"1.3": ["G8", "G3"]})
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    body = texts(log)
    assert "сверь" in body, body
    assert body.count("—") == 1, body        # one slot, not four
    assert "колонка 1" in body, body         # said where to look, in words
    assert "\n1: " not in body, body         # and did not print the board
    assert buttons(log) == ["solve", "fix", "board"]


@pytest.mark.asyncio
async def test_the_card_being_asked_about_comes_with_a_picture_of_itself(
    context, monkeypatch
):
    """Otherwise the only way to answer is to open the screenshot that was
    just sent and squint at it, which is the work the question was meant to
    save."""
    view, recognition = _screenshot({"1.3": ["G8", "G3"]})
    log: list = []
    await send_photo(
        context, log, recognition, monkeypatch, crops={view.index_of("1.3"): b"crop"}
    )

    assert log[-1][1].get("photo") == b"crop", log[-1]
    assert "сверь" in log[-1][0]


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
async def test_answering_the_one_question_settles_the_rest_and_solves(context, monkeypatch):
    """The answer pins its twin by elimination, and once it has there is
    nothing left in doubt -- so the next thing the user sees is the answer."""
    view, recognition = _screenshot({"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    answer = next(b for b in buttons(log) if b.startswith("pick:"))
    await press(context, answer, log)

    body = texts(log)
    assert "что там?" not in body.split("Считаю")[-1]   # done asking
    assert "Решение есть" in body, body
    session = context.application.bot_data["sessions"].get(1)
    assert session.pending is None
    assert session.board == recognition.state


@pytest.mark.asyncio
async def test_cards_too_many_to_ask_about_are_named_as_the_board_reads_them(
    context, monkeypatch
):
    """Past the point where an interview is worth anyone's time the board is
    shown anyway, with the cards the deck could not pin down listed beside it.

    That list has to agree with the board above it. The matcher's own winner
    for an open card is by definition the reading that lost, so naming it here
    puts two different cards in one slot -- the board says G3, the note says
    "not sure about G8" -- and answers a question nobody asked. What the user
    needs is the slot and the choice: G3 or G8, go and look."""
    monkeypatch.setattr("shenzhen.vision.resolve.MAX_QUESTIONS", 0)
    view, recognition = _screenshot({"1.3": ["G3", "G8"], "2.3": ["G8", "G3"]})
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    body = texts(log)
    assert "что там?" not in body, body       # no interview
    assert buttons(log) == ["solve", "fix"]   # the board itself, to confirm or fix

    note = next(line for line in body.splitlines() if "3-я карта сверху" in line)
    named = note.split("—")[-1].strip()
    # The card the board actually shows, first, and what else it could be.
    assert named.startswith(card_mark(recognition.state.columns[0][2])), note
    assert card_mark(parse_card("G8")) in named, note
    assert view.card_at("1.3") == parse_card("G3")


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
async def test_widening_a_question_that_came_as_a_picture_edits_its_caption(
    context, monkeypatch
):
    """Telegram changes a caption and a body of text through different methods
    and rejects the wrong one outright. Reaching for `editMessageText` here
    would strand exactly the user whose card is missing from the shortlist --
    the only user who presses this button."""
    view, recognition = _screenshot({
        "5.1": ["B3", "B4", "B5", "B6", "B8", "B9", "G1", "G2", "B7"],
        "5.2": ["B4", "B3"],
        "5.5": ["B7", "B3"],
    })
    log: list = []
    await send_photo(
        context, log, recognition, monkeypatch, crops={view.index_of("5.1"): b"crop"}
    )
    assert log[-1][1].get("photo") == b"crop"    # the question came as a picture

    wide = next(b for b in buttons(log) if b.startswith("wide:"))
    query = await press(context, wide, log, on_photo=True)

    # In place, so the crop stays above the wider list rather than scrolling
    # away from the question it belongs to.
    assert query.edited_as == "caption"
    assert any(b.startswith("pick:") for b in buttons(log))


@pytest.mark.asyncio
async def test_a_question_without_a_crop_is_not_edited_over_one_that_had_one(
    context, monkeypatch
):
    """Editing text into a photo message is rejected by Telegram outright, and
    editing its caption would be worse than the error: the picture of the slot
    just dealt with would stay put above a question about a different one."""
    view, recognition = _screenshot({
        "5.1": ["B3", "B4"],
        "5.2": ["B4", "B3"],
        "6.3": ["DG", "DR"],
        "7.2": ["DR", "DG"],
    })
    log: list = []
    crops = {u.index: b"crop" for u in recognition.resolution.unknowns}
    await send_photo(context, log, recognition, monkeypatch, crops=crops)

    session = context.application.bot_data["sessions"].get(1)
    assert log[-1][1].get("photo") == b"crop"
    session.pending.crops = {}                   # nothing to show for the next one

    answer = next(b for b in buttons(log) if b.startswith("pick:"))
    query = await press(context, answer, log, on_photo=True)

    assert query.edited_as is None, "edited the message the crop was attached to"
    assert "что там?" in log[-1][0], log[-1]      # asked again, as a new message
    assert log[-1][1].get("photo") is None


@pytest.mark.asyncio
async def test_a_stale_other_button_is_turned_away_like_a_stale_answer(
    context, monkeypatch
):
    """"Other…" from a superseded question widens a slot the interview has
    already moved past, and hands back a keyboard of answers that would then
    be refused one by one."""
    view, recognition = _screenshot({
        "5.1": ["B3", "B4"],
        "5.2": ["B4", "B3"],
        "6.3": ["DG", "DR"],
        "7.2": ["DR", "DG"],
    })
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    session = context.application.bot_data["sessions"].get(1)
    stale = next(b for b in buttons(log) if b.startswith("wide:"))
    session.pending.asked = None  # as if the interview had moved on

    await press(context, stale, log)

    assert "не жду ответа" in texts(log)


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
    session.pending.asked = second
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
async def test_a_stale_button_from_the_same_interview_is_turned_away(context, monkeypatch):
    """A question that carries a picture has to be a message of its own, so the
    keyboard of the question before it stays live in the chat. Answering that
    one would pin a card the interview has already moved past."""
    view, recognition = _screenshot({
        "5.1": ["B3", "B4"],
        "5.2": ["B4", "B3"],
        "6.3": ["DG", "DR"],
        "7.2": ["DR", "DG"],
    })
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    session = context.application.bot_data["sessions"].get(1)
    stale = next(b for b in buttons(log) if b.startswith("pick:"))
    session.pending.asked = None  # as if the interview had moved on

    before = dict(session.pending.pinned)
    await press(context, stale, log)

    assert "не жду ответа" in texts(log)
    assert session.pending.pinned == before


@pytest.mark.asyncio
async def test_the_card_put_up_is_coloured_rather_than_lettered(context, monkeypatch):
    _, recognition = _screenshot({"1.1": ["G5", "G1"]})  # G1 misread as G5
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)

    body = texts(log)
    assert "🟢1" in body, body                # not "G1"
    assert "бамбук" in body                   # with a legend for the colours


@pytest.mark.asyncio
async def test_the_whole_board_is_one_button_away_for_anyone_who_wants_it(context, monkeypatch):
    _, recognition = _screenshot({"1.1": ["G5", "G1"]})
    log: list = []
    await send_photo(context, log, recognition, monkeypatch)
    await press(context, "board", log)

    shown = log[-1][0]
    for index in range(1, 9):
        assert f"{index}:" in shown, shown
    assert "🟩" in shown                       # in colour, same as the sample


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
