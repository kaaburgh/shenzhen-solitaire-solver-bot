"""Per-chat state.

Deliberately in-memory: the bot holds one position and one solution per chat,
both of which are cheap to re-send and meaningless after a restart.  Nothing
here is worth a database, and nothing here is worth keeping on disk after the
user has moved on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..game import State
from ..notation import DEFAULT_LANG  # noqa: F401  (re-exported as the fallback)
from ..solver import SolveResult
from ..vision.resolve import Resolution, Skeleton


@dataclass
class Pending:
    """A screenshot part-way through being pinned down.

    Everything needed to rebuild the board from a different set of answers,
    and nothing else -- notably not the screenshot, which is why answering a
    question costs no memory beyond the card scores already worked out.
    """

    skeleton: Skeleton
    reads: list
    resolution: Resolution
    #: read index -> the card the user has told us it is
    pinned: dict[int, int] = field(default_factory=dict)
    #: which escalation level produced this reading.  Frozen for the whole
    #: exchange so that the read indices in `pinned` keep meaning the same
    #: thing between one question and the next.
    level: int = 0
    warnings: list[str] = field(default_factory=list)
    #: how many shaky reads the deck settled by itself, for the closing note
    deduced: int = 0
    #: card width, when the picture came in under what reads reliably
    narrow: int | None = None


@dataclass
class Session:
    lang: str = DEFAULT_LANG
    board: State | None = None
    result: SolveResult | None = None
    #: how many moves of the solution have already been shown
    shown: int = 0
    busy: bool = False
    #: set while the bot is asking about cards it could not read
    pending: Pending | None = None


class Sessions:
    def __init__(self, limit: int = 5000) -> None:
        self._sessions: dict[int, Session] = {}
        self._limit = limit

    def get(self, chat_id: int, default_lang: str | None = None) -> Session:
        """Fetch a chat's session, creating it if this is the first message.

        ``default_lang`` is only consulted when the session is created, so a
        language the user picked with /lang is never overwritten by the
        language their Telegram client happens to be set to.
        """
        session = self._sessions.get(chat_id)
        if session is None:
            if len(self._sessions) >= self._limit:
                # Crude, but this only ever trims chats that have gone quiet.
                self._sessions.pop(next(iter(self._sessions)))
            session = Session(lang=default_lang or DEFAULT_LANG)
            self._sessions[chat_id] = session
        return session

    def __len__(self) -> int:
        return len(self._sessions)
