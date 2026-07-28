"""Telegram handlers."""

from __future__ import annotations

import asyncio
import functools
import html
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..game import InvalidBoard, State
from ..notation import (
    DEFAULT_LANG,
    LANGS,
    board_to_text,
    card_mark,
    describe_deck_problem,
    describe_slot,
    describe_steps,
    mark_code_legend,
    render_board,
)
from ..solver import SolveResult, Status, solve
from ..textio import parse_board
from ..vision import LayoutError, RecognitionError, TemplateBank, load_image, recognize
from ..vision.resolve import Unresolvable, resolve, widest_options
from ..vision.verify import Check, column_depths, spot_checks
from .i18n import normalise_lang, t
from .storage import Session, Sessions

log = logging.getLogger(__name__)

MOVES_PER_MESSAGE = 5

#: card buttons per row when offering a question's answers
OPTIONS_PER_ROW = 4

#: Telegram will not hand a bot the contents of anything larger than this.
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
)


@dataclass
class BotConfig:
    max_nodes: int
    time_limit: float
    bank: TemplateBank | None


def _config(context: ContextTypes.DEFAULT_TYPE) -> BotConfig:
    return context.application.bot_data["config"]


def _sessions(context: ContextTypes.DEFAULT_TYPE) -> Sessions:
    return context.application.bot_data["sessions"]


def _session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Session:
    user = update.effective_user
    return _sessions(context).get(
        update.effective_chat.id,
        default_lang=normalise_lang(user.language_code) if user else None,
    )


def _pre(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


def _reason(exc: Exception, lang: str) -> str:
    """What to put in a message's ``{reason}``.

    A complaint about the deck not adding up names cards, and naming them is
    the whole message: whoever reads it is about to go and look for that card
    on the screen, where a suit is a colour and not a letter.  So it is
    rendered here from the cards themselves rather than taken from the
    exception's own text, which stays the letter notation for the log.
    """
    deck = getattr(exc, "deck", None)
    if deck is not None:
        return describe_deck_problem(deck, lang)
    return str(exc)


def _slot(reads: Sequence, index: int, lang: str) -> str:
    """Where read ``index`` sits, said the way someone looking at the screen
    would find it."""
    where = reads[index].where
    return describe_slot(
        where, lang, depth_total=column_depths(reads).get(where.partition(".")[0])
    )


def _check_lines(checks: Sequence[Check], lang: str) -> list[str]:
    return [
        t(
            lang,
            "check_line",
            slot=describe_slot(check.where, lang, depth_total=check.depth_total),
            card=card_mark(check.card),
        )
        for check in checks
    ]


# --- commands --------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    await update.message.reply_text(t(session.lang, "start"))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    await update.message.reply_text(t(session.lang, "help"), parse_mode=ParseMode.HTML)


async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(code.upper(), callback_data=f"lang:{code}") for code in LANGS]]
    )
    await update.message.reply_text(t(session.lang, "lang_prompt"), reply_markup=keyboard)


# --- input -----------------------------------------------------------------


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    text = update.message.text or ""

    if not any(character.isdigit() for character in text):
        await update.message.reply_text(t(session.lang, "send_something"))
        return

    try:
        board = parse_board(text)
    except InvalidBoard as exc:
        lines = [t(session.lang, "bad_board", reason=_reason(exc, session.lang))]
        if exc.deck is not None:
            # The cards were just named in marks and the text they came from is
            # in letters, so the two notations need lining up.
            lines.append(t(session.lang, "legend_codes", items=mark_code_legend()))
        await update.message.reply_text("\n".join(lines))
        return

    session.pending = None
    await _accept_board(update.message, session, board, uncertain=[], warnings=[])


def looks_like_image(document) -> bool:
    """Is this document worth trying to decode as a picture?

    Deliberately generous.  Telegram clients disagree wildly about what mime
    type an uncompressed screenshot gets: a phone sending one from the
    clipboard as a file labels it ``application/octet-stream`` or leaves the
    type out entirely, and refusing those is exactly how a screenshot sent the
    way /help asks for it ends up ignored.  Anything that is positively some
    other kind of file -- a PDF, a video, a zip -- is turned away, and whatever
    is left goes to the decoder, which is the real authority anyway.
    """
    mime = (document.mime_type or "").lower()
    if mime.startswith("image/"):
        return True

    name = (document.file_name or "").lower()
    if name.endswith(IMAGE_EXTENSIONS):
        return True

    return mime in ("", "application/octet-stream")


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    config = _config(context)

    message = update.message
    if message.photo:
        source = message.photo[-1]
    elif message.document:
        if not looks_like_image(message.document):
            await message.reply_text(t(session.lang, "not_an_image"))
            return
        source = message.document
    else:  # pragma: no cover -- the filters guarantee one of the two
        return

    if config.bank is None:
        await message.reply_text(t(session.lang, "no_templates"))
        return

    size = getattr(source, "file_size", None)
    if size and size > MAX_DOWNLOAD_BYTES:
        await message.reply_text(
            t(session.lang, "file_too_big", limit=MAX_DOWNLOAD_BYTES // (1024 * 1024))
        )
        return

    await message.chat.send_action(ChatAction.TYPING)
    try:
        file = await source.get_file()
        data = bytes(await file.download_as_bytearray())
    except TelegramError as exc:
        # Telegram refuses getFile for anything oversized, and downloads time
        # out.  Both used to leave the user staring at silence.
        log.warning("could not fetch the file: %s", exc)
        await message.reply_text(t(session.lang, "download_failed", reason=str(exc)))
        return

    loop = asyncio.get_running_loop()
    try:
        # Decoding and matching are pure CPU; keep them off the event loop.
        result = await loop.run_in_executor(
            None, functools.partial(_recognize_bytes, data, config.bank)
        )
    except RecognitionError as exc:
        # The picture is a board, it just did not come out as one that could
        # exist. Nothing here is worth throwing away: most of the forty cards
        # will be right, so hand the reading back for the user to correct
        # rather than asking them for a better screenshot.
        draft = exc.draft
        if draft is None:
            await message.reply_text(t(session.lang, "bad_image", reason=str(exc)))
            return
        lines = [t(session.lang, "bad_reading", reason=_reason(exc, session.lang))]
        if exc.narrow:
            lines.append(t(session.lang, "bad_reading_narrow", card_w=exc.card_w))
        if exc.deck is not None:
            # Right above the reading it decodes: the complaint named cards in
            # marks, the reading below is in the notation you edit and send
            # back, and nothing else in the message pairs the two.
            lines.append(t(session.lang, "legend_codes", items=mark_code_legend()))
        lines.append(_pre(draft))
        await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return
    except LayoutError as exc:
        await message.reply_text(t(session.lang, "bad_image", reason=str(exc)))
        return
    except Exception as exc:  # pragma: no cover -- defensive
        log.exception("recognition failed")
        await message.reply_text(t(session.lang, "error", reason=str(exc)))
        return

    session.pending = None
    resolution = result.resolution

    # Only the cards the deck could not pin down are worth a question, and
    # only if there are few enough of them to be worth anyone's time.
    if resolution is not None and resolution.open and resolution.interviewable:
        session.start_interview(
            skeleton=result.skeleton,
            reads=result.reads,
            resolution=resolution,
            level=resolution.level,
            warnings=result.warnings,
            deduced=result.deduced,
            narrow=result.narrow,
        )
        await _ask(message, session, intro=True)
        return

    await _accept_board(
        message,
        session,
        result.state,
        uncertain=_open_cards(resolution, result.reads, session.lang),
        warnings=result.warnings,
        deduced=result.deduced,
        narrow=result.narrow,
        checks=spot_checks(result.reads, resolution),
    )


def _recognize_bytes(data: bytes, bank: TemplateBank):
    return recognize(load_image(data), bank)


#: how many alternatives to name for one card before the list stops helping
OPTIONS_LISTED = 3


def _open_cards(resolution, reads: Sequence, lang: str) -> list[str]:
    """The cards the deck could not pin down, each with what it might be.

    Named off the resolution rather than off the raw reads, because those two
    disagree exactly where it matters: the board being shown holds the deck's
    best surviving reading of an open card, while the matcher's own winner is
    whatever lost. Listing the latter beside the former tells the user their
    board says G3 and the bot is unsure it is G8 -- two claims about one slot,
    neither of them the question actually being asked.

    And the alternatives come along, since this is the list the user is being
    asked to check against the screen. "column 3, the bottom card is 🟢3 or
    🟢8" says where to look and what to look for; naming one card only reads
    like a claim.
    """
    if resolution is None:
        return []
    lines = []
    for index in resolution.open:
        options = resolution.options(index)[:OPTIONS_LISTED]
        lines.append(
            t(
                lang,
                "check_line",
                slot=_slot(reads, index, lang),
                card=" / ".join(card_mark(card) for card in options),
            )
        )
    return lines


# --- asking about cards the deck could not settle --------------------------


def _option_rows(token: int, index: int, cards: Sequence[int]) -> list[list[InlineKeyboardButton]]:
    rows = []
    for start in range(0, len(cards), OPTIONS_PER_ROW):
        rows.append(
            [
                InlineKeyboardButton(card_mark(card), callback_data=f"pick:{token}:{index}:{card}")
                for card in cards[start : start + OPTIONS_PER_ROW]
            ]
        )
    return rows


def _for_current_interview(session: Session, raw_token: str) -> bool:
    """Is this button from the screenshot the bot is currently asking about?

    A keyboard sent for an earlier screenshot never goes away, and its read
    indices address a different board.  Applied to the current one they would
    be accepted as an answer wherever they happened to be deck-legal, and
    quietly produce the wrong position.
    """
    return session.pending is not None and session.pending.token == int(raw_token)


async def _ask(message, session: Session, *, intro: bool = False, edit=None) -> None:
    """Put the next open card to the user, as buttons.

    One question at a time on purpose.  Each answer is fed back through the
    deck before the next question is chosen, and that usually settles the
    others by elimination -- which is the difference between confirming one
    card and confirming five.
    """
    pending = session.pending
    resolution = pending.resolution
    index = resolution.next_question()
    if index is None:  # pragma: no cover -- guarded by the caller
        return

    options = resolution.options(index)

    lines = []
    if intro:
        lines.append(t(session.lang, "ask_intro"))
        if pending.deduced:
            lines.append(t(session.lang, "ask_deduced", n=pending.deduced))
        lines.append("")
    lines.append(t(session.lang, "ask_slot", slot=_slot(pending.reads, index, session.lang)))
    remaining = len(resolution.open) - 1
    if remaining:
        lines.append(t(session.lang, "ask_left", n=remaining))
    lines.append(t(session.lang, "legend"))

    keyboard = InlineKeyboardMarkup(
        _option_rows(pending.token, index, options)
        + [
            [
                InlineKeyboardButton(t(session.lang, "btn_other"), callback_data=f"wide:{pending.token}:{index}"),
                InlineKeyboardButton(t(session.lang, "btn_type"), callback_data="fix"),
            ]
        ]
    )
    send = edit or message.reply_text
    await send("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def _answer_question(query, session: Session, index: int, card: int) -> None:
    """Take one answer, re-run the deck, and either ask again or show the board."""
    pending = session.pending
    pinned = dict(pending.pinned)
    pinned[index] = card
    try:
        resolution = resolve(
            pending.skeleton, pending.reads, pinned, level=pending.level
        )
    except Unresolvable:
        # The answer is legal on its own but cannot coexist with the rest of
        # the board, which means some other card is misread rather than this
        # one.  Keep the earlier answers and let them try again.
        await query.message.reply_text(t(session.lang, "ask_contradiction"))
        return

    pending.pinned = pinned
    pending.resolution = resolution

    if resolution.open and resolution.interviewable:
        await _ask(session=session, message=query.message, edit=query.edit_message_text)
        return

    session.pending = None
    await _accept_board(
        query.message,
        session,
        resolution.state,
        uncertain=_open_cards(resolution, pending.reads, session.lang),
        warnings=pending.warnings,
        checks=spot_checks(pending.reads, resolution, pinned),
        # `settled` is recomputed from scratch and counts the answers just
        # given as settled too -- they are, but the user gave them, so they do
        # not belong in "cards I worked out for you".
        deduced=len(resolution.settled) - len(pinned),
        narrow=pending.narrow,
    )


async def _offer_everything(query, session: Session, index: int) -> None:
    """Handle "none of these" by widening one card to all the deck allows.

    A question's shortlist comes from how the templates scored, so a card the
    matcher ranked badly will not be on it.  The deck still rules most of the
    pack out, so what comes back is a handful of buttons rather than forty.
    """
    pending = session.pending
    options = widest_options(
        pending.skeleton, pending.reads, pending.pinned, index, level=pending.level
    )
    if not options:
        await query.message.reply_text(t(session.lang, "ask_no_options"))
        return

    lines = [
        t(session.lang, "ask_slot", slot=_slot(pending.reads, index, session.lang)),
        t(session.lang, "ask_other"),
        t(session.lang, "legend"),
    ]
    keyboard = InlineKeyboardMarkup(
        _option_rows(pending.token, index, options)
        + [[InlineKeyboardButton(t(session.lang, "btn_type"), callback_data="fix")]]
    )
    await query.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def _accept_board(
    message,
    session: Session,
    board: State,
    *,
    uncertain: list[str],
    warnings: list[str],
    deduced: int = 0,
    narrow: int | None = None,
    checks: Sequence[Check] = (),
) -> None:
    """Ask for a nod before spending time on the position.

    With ``checks`` that is a spot check of three or four slots rather than
    the whole board -- see :mod:`shenzhen.vision.verify` for why those four.
    The board itself stays one button away, for anyone who would rather look
    at all of it; a position that was typed out has nothing to sample, so it
    gets the board directly.
    """
    session.board = board
    session.result = None
    session.shown = 0

    if checks:
        lines = [t(session.lang, "check_head")]
        lines.extend(_check_lines(checks, session.lang))
        lines.append(t(session.lang, "legend"))
    else:
        lines = [t(session.lang, "board_read"), _pre(render_board(board, session.lang))]
    if deduced:
        lines.append(t(session.lang, "ask_deduced", n=deduced))
    if uncertain:
        # Its own lines rather than one run-on sentence: these read the same
        # way as the spot check above them, and they are checked the same way.
        lines.append(t(session.lang, "uncertain"))
        lines.extend(uncertain)
    if warnings:
        lines.append(t(session.lang, "warnings", items="; ".join(warnings)))
    if narrow is not None:
        lines.append(t(session.lang, "board_narrow", card_w=narrow))
    lines.append(t(session.lang, "check_confirm" if checks else "board_confirm"))

    rows = [
        [InlineKeyboardButton(t(session.lang, "btn_correct"), callback_data="solve")],
        [InlineKeyboardButton(t(session.lang, "btn_fix"), callback_data="fix")],
    ]
    if checks:
        rows[1].append(
            InlineKeyboardButton(t(session.lang, "btn_board"), callback_data="board")
        )
    await message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows)
    )


# --- buttons ---------------------------------------------------------------


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    session = _sessions(context).get(update.effective_chat.id)
    data = query.data or ""

    if data.startswith("lang:"):
        code = data.split(":", 1)[1]
        if code in LANGS:
            session.lang = code
        await query.answer()
        await query.edit_message_text(t(session.lang, "lang_set"))
        return

    if data.startswith("pick:"):
        await query.answer()
        _, raw_token, raw_index, raw_card = data.split(":", 3)
        if not _for_current_interview(session, raw_token):
            await query.message.reply_text(t(session.lang, "ask_expired"))
            return
        await _answer_question(query, session, int(raw_index), int(raw_card))
        return

    if data.startswith("wide:"):
        await query.answer()
        _, raw_token, raw_index = data.split(":", 2)
        if not _for_current_interview(session, raw_token):
            await query.message.reply_text(t(session.lang, "ask_expired"))
            return
        await _offer_everything(query, session, int(raw_index))
        return

    if data == "fix":
        await query.answer()
        # Bailing out of the questions and typing it out instead: the best
        # reading so far is a better starting point than nothing.
        if session.pending is not None:
            session.board = session.pending.resolution.state
            session.pending = None
        if session.board is None:
            await query.message.reply_text(t(session.lang, "no_board"))
            return
        await query.message.reply_text(
            t(session.lang, "fix_hint") + "\n" + _pre(board_to_text(session.board)),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "board":
        await query.answer()
        # The whole position, for anyone who would rather check all of it than
        # the four slots offered.  In colour, since that is the version worth
        # holding up against the screen.
        if session.board is None:
            await query.message.reply_text(t(session.lang, "no_board"))
            return
        await query.message.reply_text(
            "\n".join(
                [
                    t(session.lang, "board_read"),
                    html.escape(render_board(session.board, session.lang, colour=True)),
                    t(session.lang, "legend"),
                ]
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "solve":
        await query.answer()
        await _solve_and_reply(query.message, context, session)
        return

    if data == "more":
        await query.answer()
        await _send_moves(query.message, session)
        return

    await query.answer()  # pragma: no cover -- unknown callback


async def _solve_and_reply(message, context: ContextTypes.DEFAULT_TYPE, session: Session) -> None:
    if session.board is None:
        await message.reply_text(t(session.lang, "no_board"))
        return
    if session.busy:
        await message.reply_text(t(session.lang, "busy"))
        return
    if session.board.is_won:
        await message.reply_text(t(session.lang, "already_won"))
        return

    config = _config(context)
    notice = await message.reply_text(t(session.lang, "solving"))
    await message.chat.send_action(ChatAction.TYPING)

    session.busy = True
    try:
        loop = asyncio.get_running_loop()
        result: SolveResult = await loop.run_in_executor(
            context.application.bot_data["executor"],
            functools.partial(
                solve,
                session.board,
                max_nodes=config.max_nodes,
                time_limit=config.time_limit,
            ),
        )
    except Exception as exc:  # pragma: no cover -- defensive
        log.exception("solve failed")
        session.busy = False
        await notice.edit_text(t(session.lang, "error", reason=str(exc)))
        return
    finally:
        session.busy = False

    session.result = result
    session.shown = 0

    if result.status is Status.UNSOLVABLE:
        await notice.edit_text(t(session.lang, "unsolvable"))
        return
    if result.status is Status.UNKNOWN:
        await notice.edit_text(
            t(session.lang, "unknown", nodes=result.nodes, elapsed=result.elapsed)
        )
        return

    await notice.edit_text(t(session.lang, "solved", total=len(result.steps)))
    await _send_moves(message, session)


async def _send_moves(message, session: Session) -> None:
    result = session.result
    if result is None or not result.solved:
        await message.reply_text(t(session.lang, "no_board"))
        return

    steps = result.steps[session.shown : session.shown + MOVES_PER_MESSAGE]
    if not steps:
        await message.reply_text(
            t(session.lang, "no_more_moves", total=len(result.steps))
        )
        return

    text = describe_steps(steps, session.lang, start=session.shown + 1)
    session.shown += len(steps)

    keyboard = None
    if session.shown < len(result.steps):
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(t(session.lang, "btn_more"), callback_data="more")]]
        )

    await message.reply_text(
        t(session.lang, "solved_head") + "\n" + _pre(text),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# --- last resort -----------------------------------------------------------


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Say *something* when a handler blows up.

    Without this, an unexpected exception is logged on the server and the chat
    just goes quiet, which is indistinguishable from the bot ignoring the
    message.
    """
    log.exception("update failed", exc_info=context.error)

    message = getattr(update, "effective_message", None)
    if message is None:
        return

    chat = getattr(update, "effective_chat", None)
    lang = DEFAULT_LANG if chat is None else _sessions(context).get(chat.id).lang
    try:
        await message.reply_text(t(lang, "error", reason=str(context.error)))
    except Exception:  # pragma: no cover -- the chat may be gone entirely
        log.exception("could not report the failure to the chat")
