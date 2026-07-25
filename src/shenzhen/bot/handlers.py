"""Telegram handlers."""

from __future__ import annotations

import asyncio
import functools
import html
import logging
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from ..cards import card_code
from ..game import InvalidBoard, State
from ..notation import LANGS, board_to_text, describe_steps, render_board
from ..solver import SolveResult, Status, solve
from ..textio import parse_board
from ..vision import LayoutError, RecognitionError, TemplateBank, load_image, recognize
from .i18n import normalise_lang, t
from .storage import Session, Sessions

log = logging.getLogger(__name__)

MOVES_PER_MESSAGE = 5


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

    await _accept_board(update, context, session, board, uncertain=[], warnings=[])


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
    except (LayoutError, RecognitionError) as exc:
        await message.reply_text(t(session.lang, "bad_image", reason=str(exc)))
        return
    except Exception as exc:  # pragma: no cover -- defensive
        log.exception("recognition failed")
        await message.reply_text(t(session.lang, "error", reason=str(exc)))
        return

    uncertain = [f"{r.where} = {card_code(r.card)}" for r in result.uncertain]
    await _accept_board(
        update, context, session, result.state, uncertain=uncertain, warnings=result.warnings
    )


def _recognize_bytes(data: bytes, bank: TemplateBank):
    return recognize(load_image(data), bank)


async def _accept_board(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: Session,
    board: State,
    *,
    uncertain: list[str],
    warnings: list[str],
) -> None:
    """Show the position back and ask for a nod before spending time on it."""
    session.board = board
    session.result = None
    session.shown = 0

    lines = [t(session.lang, "board_read"), _pre(render_board(board, session.lang))]
    if uncertain:
        lines.append(t(session.lang, "uncertain", cards=", ".join(uncertain)))
    if warnings:
        lines.append(t(session.lang, "warnings", items="; ".join(warnings)))
    lines.append(t(session.lang, "board_confirm"))

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(session.lang, "btn_correct"), callback_data="solve")],
            [InlineKeyboardButton(t(session.lang, "btn_fix"), callback_data="fix")],
        ]
    )
    await update.message.reply_text(
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

    if data == "fix":
        await query.answer()
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
