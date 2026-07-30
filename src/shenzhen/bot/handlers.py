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
    describe_moves,
    describe_plan,
    describe_slot,
    describe_steps,
    mark_code_legend,
    render_board,
)
from ..plan import plan
from ..solver import SolveResult, Status, solve
from ..textio import parse_board
from ..vision import LayoutError, RecognitionError, TemplateBank, load_image, recognize
from ..vision.crop import card_crop
from ..vision.resolve import Unresolvable, resolve, widest_options
from ..vision.verify import Check, column_depths, spot_check
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


def _crops(data: bytes, result, indices: Sequence[int]) -> dict[int, bytes]:
    """A picture of each of ``indices``, cut from the screenshot.

    Taken while the bytes are still in hand: the question that needs the
    picture may not be asked until several messages later, and by then the
    only thing kept is what fits in the session.  Best effort throughout -- a
    question with no picture beside it is the question the bot used to ask,
    which is worse but not broken.
    """
    if not indices:
        return {}
    try:
        image = load_image(data)
    except Exception:  # pragma: no cover -- it decoded once already
        log.warning("could not decode the screenshot to cut a crop from it")
        return {}

    card_w = result.source_card_w or 0
    crops: dict[int, bytes] = {}
    for index in indices:
        try:
            crop = card_crop(image, result.source_box(index), card_w)
        except Exception:  # pragma: no cover -- defensive
            log.exception("could not cut a crop for read %d", index)
            continue
        if crop is not None:
            crops[index] = crop
    return crops


async def _cut_crops(data: bytes, result, indices: Sequence[int]) -> dict[int, bytes]:
    """:func:`_crops`, off the event loop.

    Decoding and resizing a screenshot is the same kind of work as reading it
    in the first place, and it is a dozen crops rather than one.
    """
    if not indices:
        return {}
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, functools.partial(_crops, data, result, list(indices))
    )


def _is_photo(message) -> bool:
    """Was this message sent as a picture with a caption?

    Telegram changes a caption and a body of text through different methods
    and rejects the wrong one outright, so any edit of a message the bot may
    have sent either way has to ask first.
    """
    return bool(getattr(message, "photo", None))


async def _replace(query, text: str, *, reply_markup=None) -> None:
    """Put ``text`` in place of whatever the pressed button was attached to."""
    if _is_photo(query.message):
        await query.edit_message_caption(
            caption=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
        return
    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
    )


async def _send(message, text: str, *, photo: bytes | None = None, reply_markup=None):
    """Say ``text``, with a picture above it where there is one to show."""
    if photo is not None:
        try:
            return await message.reply_photo(
                photo=photo,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        except TelegramError:
            # Telegram refuses photos for reasons that have nothing to do with
            # the question being asked; the question still has to be asked.
            log.warning("could not send the crop; falling back to text")
    return await message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
    )


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
    _hold(session, board)
    # A typed position has nothing to be unsure of and nothing to sample, so
    # it goes back in full for the user to check against what they meant.
    await _confirm(update.message, session)


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
            # The one place the "send it as a file" advice belongs: the
            # picture was too small *and* that is why this failed.
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
            # Every slot a question could still land on, so that each one can
            # show what it is asking about long after the bytes are gone.
            crops=await _cut_crops(data, result, [u.index for u in resolution.unknowns]),
        )
        await _ask(message, session, intro=True)
        return

    check = spot_check(result.reads, resolution, warnings=result.warnings)
    crops = await _cut_crops(data, result, [check.index] if check else [])
    await _accept_reading(
        message,
        context,
        session,
        result.state,
        uncertain=_open_cards(resolution, result.reads, session.lang),
        warnings=result.warnings,
        check=check,
        crop=crops.get(check.index) if check else None,
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


def _for_live_question(session: Session, raw_token: str, raw_index: str) -> bool:
    """Is this button the one the bot is waiting on right now?

    Two ways it can fail to be.  It can belong to an earlier screenshot, where
    its read index means a different card entirely; or it can belong to an
    earlier question of this same interview, since a question carrying a crop
    is a new message rather than an edit and leaves the keyboard before it
    standing.  Both name a slot the interview has moved past, and neither
    "answer it" nor "widen it" means anything there.
    """
    return (
        _for_current_interview(session, raw_token)
        and session.pending.asked == int(raw_index)
    )


def _stale(session: Session, raw_token: str) -> str:
    """Why a button did not do anything -- which of the two ways it was old."""
    key = "ask_superseded" if _for_current_interview(session, raw_token) else "ask_expired"
    return t(session.lang, key)


async def _ask(message, session: Session, *, intro: bool = False, query=None) -> None:
    """Put the next open card to the user, as buttons and a picture of the slot.

    One question at a time on purpose.  Each answer is fed back through the
    deck before the next question is chosen, and that usually settles the
    others by elimination -- which is the difference between confirming one
    card and confirming five.

    The next question replaces the one just answered only when both are plain
    text.  A question that has a crop cannot be an edit of anything, since a
    message cannot grow a photo; and a question that has none must not be
    edited over one that did, or the picture of the card already dealt with
    would be left standing above a question about a different slot.  Either
    way it becomes a new message, which leaves the previous keyboard live in
    the chat -- and that is what :attr:`Pending.asked` is for.
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
    pending.asked = index

    crop = pending.crops.get(index)
    text = "\n".join(lines)
    if crop is None and query is not None and not _is_photo(query.message):
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=keyboard
        )
        return
    await _send(message, text, photo=crop, reply_markup=keyboard)


async def _answer_question(
    query, context: ContextTypes.DEFAULT_TYPE, session: Session, index: int, card: int
) -> None:
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
        await _ask(session=session, message=query.message, query=query)
        return

    session.pending = None
    check = spot_check(pending.reads, resolution, pinned, warnings=pending.warnings)
    await _accept_reading(
        query.message,
        context,
        session,
        resolution.state,
        uncertain=_open_cards(resolution, pending.reads, session.lang),
        warnings=pending.warnings,
        check=check,
        crop=pending.crops.get(check.index) if check else None,
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
    # In place, so the crop of the slot stays above the wider list -- this is
    # the same question, and it is the one question where seeing the card
    # matters most, since the shortlist has just been said not to contain it.
    await _replace(query, "\n".join(lines), reply_markup=keyboard)


def _hold(session: Session, board: State) -> None:
    """Take ``board`` as the position, dropping whatever was solved before."""
    session.board = board
    session.result = None
    session.plan = []
    session.shown = 0
    session.unconfirmed = False


async def _accept_reading(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    session: Session,
    board: State,
    *,
    uncertain: Sequence[str] = (),
    warnings: Sequence[str] = (),
    check: Check | None = None,
    crop: bytes | None = None,
) -> None:
    """Take a board read off a picture, asking only about what is in doubt.

    A reading nothing disagreed with -- see :mod:`shenzhen.vision.verify` --
    goes straight to the solver.  It is a position the deck has already proved
    complete, so a confirmation step would be the bot reading its own homework
    back and charging a tap for it.  The answer carries the way out instead:
    the whole board is one button away, and so is correcting it.
    """
    _hold(session, board)

    if check is None and not uncertain and not warnings:
        session.unconfirmed = True
        await _solve_and_reply(message, context, session)
        return

    await _confirm(message, session, uncertain=uncertain, warnings=warnings, check=check, crop=crop)


async def _confirm(
    message,
    session: Session,
    *,
    uncertain: Sequence[str] = (),
    warnings: Sequence[str] = (),
    check: Check | None = None,
    crop: bytes | None = None,
) -> None:
    """Ask for a nod before spending time on the position.

    With a ``check`` that is one slot and a picture of it, which is a question
    that can be answered from the chat rather than from the game.  Without
    one -- a position that was typed out, or one with more open cards than an
    interview is worth -- it is the board itself.
    """
    if check is not None:
        lines = [
            t(
                session.lang,
                "check_one",
                slot=describe_slot(check.where, session.lang, depth_total=check.depth_total),
                card=card_mark(check.card),
            ),
            t(session.lang, "legend"),
        ]
    else:
        lines = [
            t(session.lang, "board_read"),
            _pre(render_board(session.board, session.lang)),
        ]
    if uncertain:
        # Its own lines rather than one run-on sentence: these are checked
        # against the screen one at a time, so they are read that way.
        lines.append(t(session.lang, "uncertain"))
        lines.extend(uncertain)
    if warnings:
        lines.append(t(session.lang, "warnings", items="; ".join(warnings)))
    if check is None:
        lines.append(t(session.lang, "board_confirm"))

    rows = [
        [InlineKeyboardButton(t(session.lang, "btn_correct"), callback_data="solve")],
        [InlineKeyboardButton(t(session.lang, "btn_fix"), callback_data="fix")],
    ]
    if check is not None:
        rows[1].append(
            InlineKeyboardButton(t(session.lang, "btn_board"), callback_data="board")
        )
    await _send(
        message, "\n".join(lines), photo=crop, reply_markup=InlineKeyboardMarkup(rows)
    )


def _escape_hatch(session: Session) -> InlineKeyboardMarkup | None:
    """The way back for a reading the user never got the chance to confirm."""
    if not session.unconfirmed:
        return None
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(session.lang, "btn_board"), callback_data="board"),
                InlineKeyboardButton(t(session.lang, "btn_fix"), callback_data="fix"),
            ]
        ]
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
        if not _for_live_question(session, raw_token, raw_index):
            await query.message.reply_text(_stale(session, raw_token))
            return
        await _answer_question(query, context, session, int(raw_index), int(raw_card))
        return

    if data.startswith("wide:"):
        await query.answer()
        _, raw_token, raw_index = data.split(":", 2)
        if not _for_live_question(session, raw_token, raw_index):
            await query.message.reply_text(_stale(session, raw_token))
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
        # The whole position, for anyone who would rather check all of it
        # themselves.  In colour, since that is the version worth holding up
        # against the screen.
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
        # They have looked at it and said yes, so the answer needs no way back.
        session.unconfirmed = False
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
        await message.reply_text(
            t(session.lang, "already_won"), reply_markup=_escape_hatch(session)
        )
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
    session.plan = plan(result.steps)
    session.shown = 0

    # A verdict on a reading nobody confirmed carries the way back to it --
    # most of all "not winnable", which is exactly the answer worth doubting
    # if a card came out wrong.
    hatch = _escape_hatch(session)

    if result.status is Status.UNSOLVABLE:
        await notice.edit_text(t(session.lang, "unsolvable"), reply_markup=hatch)
        return
    if result.status is Status.UNKNOWN:
        await notice.edit_text(
            t(session.lang, "unknown", nodes=result.nodes, elapsed=result.elapsed),
            reply_markup=hatch,
        )
        return

    # The verdict carries the shape of the line, not just its length.  Which
    # moves are legal is the easy half of a hard position; which way to set off
    # -- dragons, or the ace, or unloading the table first -- is the half a
    # numbered list of thirty moves does not answer.
    #
    # Not when the whole line arrives in one message, though: the goals are over
    # each run of moves already, and a plan above five visible moves is the same
    # thing said twice.
    verdict = t(
        session.lang, "solved", total=describe_moves(len(result.steps), session.lang)
    )
    if len(session.plan) > 1 and len(result.steps) > MOVES_PER_MESSAGE:
        goals = _pre(describe_plan(session.plan, session.lang))
        verdict += f"\n\n{t(session.lang, 'plan_head')}\n{goals}"
    await notice.edit_text(verdict, parse_mode=ParseMode.HTML, reply_markup=hatch)
    await _send_moves(message, session)


async def _send_moves(message, session: Session) -> None:
    result = session.result
    if result is None or not result.solved:
        await message.reply_text(t(session.lang, "no_board"))
        return

    steps = result.steps[session.shown : session.shown + MOVES_PER_MESSAGE]
    if not steps:
        await message.reply_text(
            t(
                session.lang,
                "no_more_moves",
                total=describe_moves(len(result.steps), session.lang),
            )
        )
        return

    text = describe_steps(
        steps, session.lang, start=session.shown + 1, phases=session.plan
    )
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
