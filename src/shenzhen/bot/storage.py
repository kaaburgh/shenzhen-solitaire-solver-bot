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
    plus a picture of each slot that could still be asked about.  Not the
    screenshot itself: the questions only ever land on reads the deck could
    not settle, of which there are at most a dozen or so, and a dozen small
    crops is a bounded cost where holding the original is not.
    """

    #: identifies this interview in callback data.  A keyboard from an earlier
    #: screenshot stays live in the chat forever, and its buttons carry read
    #: indices that mean something else entirely against a later one -- so the
    #: answer has to say which screenshot it is answering about.
    token: int
    skeleton: Skeleton
    reads: list
    resolution: Resolution
    #: read index -> the card the user has told us it is
    pinned: dict[int, int] = field(default_factory=dict)
    #: which escalation level produced this reading.  Frozen for the whole
    #: exchange so that the read indices in `pinned` keep meaning the same
    #: thing between one question and the next.
    level: int = 0
    #: the read the bot is asking about right now.  A question that carries a
    #: picture has to be its own message rather than an edit of the last one,
    #: so earlier keyboards stay live in the chat and have to be turned away.
    asked: int | None = None
    warnings: list[str] = field(default_factory=list)
    #: read index -> an encoded picture of that slot, cut from the screenshot
    #: while it was still in hand, so a question can show what it is asking
    #: about rather than describing it
    crops: dict[int, bytes] = field(default_factory=dict)


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
    #: the board went to the solver without the user ever being asked about
    #: it, so whatever comes back has to offer a way to look at the reading
    unconfirmed: bool = False
    #: how many interviews this chat has started, so each gets its own token
    interviews: int = 0

    def start_interview(self, **kwargs) -> Pending:
        self.interviews += 1
        self.pending = Pending(token=self.interviews, **kwargs)
        return self.pending


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
