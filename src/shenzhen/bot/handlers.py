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
from telegram.ext import ContextTypes

from ..cards import card_code
from ..game import InvalidBoard, State
from ..notation import LANGS, board_to_text, describe_slot, describe_steps, render_board
from ..solver import SolveResult, Status, solve
from ..textio import parse_board
from ..vision import LayoutError, RecognitionError, TemplateBank, load_image, recognize
from ..vision.resolve import Unresolvable, resolve, widest_options
from .i18n import normalise_lang, t
from .storage import Session, Sessions

log = logging.getLogger(__name__)

MOVES_PER_MESSAGE = 5

#: card buttons per row when offering a question's answers
OPTIONS_PER_ROW = 4


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
        await update.message.reply_text(t(session.lang, "bad_board", reason=str(exc)))
        return

    session.pending = None
    await _accept_board(update.message, session, board, uncertain=[], warnings=[])


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    config = _config(context)

    if config.bank is None:
        await update.message.reply_text(t(session.lang, "no_templates"))
        return

    message = update.message
    if message.photo:
        source = message.photo[-1]
    elif message.document:
        source = message.document
    else:  # pragma: no cover -- the filters guarantee one of the two
        return

    await message.chat.send_action(ChatAction.TYPING)
    file = await source.get_file()
    data = bytes(await file.download_as_bytearray())

    loop = asyncio.get_running_loop()
    try:
        # Decoding and matching are pure CPU; keep them off the event loop.
        result = await loop.run_in_executor(
            None, functools.partial(_recognize_bytes, data, config.bank)
        )
    except RecognitionError as exc:
        # A board that cannot exist usually means the picture was too small to
        # read, not that it was the wrong picture -- and those two need
        # completely different things from the user, so say which.
        if exc.likely_rescaled:
            await message.reply_text(
                t(session.lang, "image_rescaled", card_w=exc.card_w),
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply_text(t(session.lang, "bad_image", reason=str(exc)))
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

    uncertain = [f"{r.where} = {card_code(r.card)}" for r in result.uncertain]
    await _accept_board(
        message,
        session,
        result.state,
        uncertain=uncertain,
        warnings=result.warnings,
        deduced=result.deduced,
        narrow=result.narrow,
    )


def _recognize_bytes(data: bytes, bank: TemplateBank):
    return recognize(load_image(data), bank)


# --- asking about cards the deck could not settle --------------------------


def _option_rows(token: int, index: int, cards: Sequence[int]) -> list[list[InlineKeyboardButton]]:
    rows = []
    for start in range(0, len(cards), OPTIONS_PER_ROW):
        rows.append(
            [
                InlineKeyboardButton(card_code(card), callback_data=f"pick:{token}:{index}:{card}")
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

    read = pending.reads[index]
    options = resolution.options(index)

    lines = []
    if intro:
        lines.append(t(session.lang, "ask_intro"))
        if pending.deduced:
            lines.append(t(session.lang, "ask_deduced", n=pending.deduced))
        lines.append("")
    lines.append(t(session.lang, "ask_slot", slot=describe_slot(read.where, session.lang)))
    remaining = len(resolution.open) - 1
    if remaining:
        lines.append(t(session.lang, "ask_left", n=remaining))

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
    uncertain = [
        f"{pending.reads[i].where} = {card_code(resolution.options(i)[0])}"
        for i in resolution.open
    ]
    await _accept_board(
        query.message,
        session,
        resolution.state,
        uncertain=uncertain,
        warnings=pending.warnings,
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

    read = pending.reads[index]
    lines = [
        t(session.lang, "ask_slot", slot=describe_slot(read.where, session.lang)),
        t(session.lang, "ask_other"),
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
) -> None:
    """Show the position back and ask for a nod before spending time on it."""
    session.board = board
    session.result = None
    session.shown = 0

    lines = [t(session.lang, "board_read"), _pre(render_board(board, session.lang))]
    if deduced:
        lines.append(t(session.lang, "ask_deduced", n=deduced))
    if uncertain:
        lines.append(t(session.lang, "uncertain", cards=", ".join(uncertain)))
    if warnings:
        lines.append(t(session.lang, "warnings", items="; ".join(warnings)))
    if narrow is not None:
        lines.append(t(session.lang, "board_narrow", card_w=narrow))
    lines.append(t(session.lang, "board_confirm"))

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(session.lang, "btn_correct"), callback_data="solve")],
            [InlineKeyboardButton(t(session.lang, "btn_fix"), callback_data="fix")],
        ]
    )
    await message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=keyboard
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
