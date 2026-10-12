"""Clarify-loop sessions: in-memory state between a question and its answer.

Owner: Winston
Diagram box: none; supports the clarify loop around the Confidence gate
Contract: none; called by app/intent/router.py
"""

import time
from dataclasses import dataclass, field

from app import config
from app.contracts import Answer
from app.intent.jev import Reply
from app.intent.options import Options, UserData


@dataclass
class Session:
    user_id: str
    transcript: str
    options: Options
    data: UserData
    replies: list[Reply]
    answers: dict[str, str] = field(default_factory=dict)  # question -> option the user chose
    asked: int = 0
    pending: tuple[str, str, set[str]] | None = None  # question_id, question, offered options
    touched: float = field(default_factory=time.monotonic)

    def record(self, answer: Answer) -> None:
        """Store the user's answer to the open question; refuse answers to anything else."""
        if self.pending is None or answer.question_id != self.pending[0]:
            raise ValueError("That question is no longer open.")
        _, question, offered = self.pending
        if answer.option_id not in offered:
            raise ValueError("That option was not offered.")
        self.answers[question] = answer.option_id
        self.pending = None


# ponytail: one process, in memory; sessions vanish on restart. Fine for one demo server.
_SESSIONS: dict[str, Session] = {}


def save(session_id: str, session: Session) -> None:
    now = time.monotonic()
    for stale in [
        k for k, s in _SESSIONS.items() if now - s.touched > config.SESSION_TIMEOUT_SECONDS
    ]:
        del _SESSIONS[stale]
    session.touched = now
    _SESSIONS[session_id] = session


def load(session_id: str, user_id: str) -> Session | None:
    """Return the open session, or None if it is unknown, someone else's, or expired."""
    session = _SESSIONS.get(session_id)
    if session is None or session.user_id != user_id:
        return None
    if time.monotonic() - session.touched > config.SESSION_TIMEOUT_SECONDS:
        drop(session_id)
        return None
    return session


def drop(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)
