"""Clarify-loop sessions: in-memory state between a question and its answer.

Owner: Winston
Diagram box: none; supports the clarify loop around the Confidence gate
Contract: none; called by app/intent/router.py
"""

import time
from dataclasses import dataclass, field

from app import config
from app.contracts import Answer, Draft
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
    draft: Draft | None = None  # the current draft, kept for follow-up turns
    followup: str | None = None  # the follow-up turn being worked on, if any

    history: list[dict] = field(default_factory=list)

    def record(self, answer: Answer) -> None:
        """Store the user's answer to the open question; refuse answers to anything else."""
        if self.pending is None or answer.question_id != self.pending[0]:
            raise ValueError("That question is no longer open.")
        _, question, offered = self.pending
        if answer.option_id not in offered:
            raise ValueError("That option was not offered.")
        self.history.append(
            {
                "kind": "clarification",
                "question": question,
                "selected_id": answer.option_id,
                "selected_label": self.options.labels.get(question, {}).get(
                    answer.option_id, answer.option_id
                ),
            }
        )
        self.answers[question] = answer.option_id
        self.pending = None


# ponytail: one process, in memory; sessions vanish on restart. Fine for one demo server.
# Keyed by user too, so one user's request can never drop or replace another's session (M4).
_SESSIONS: dict[tuple[str, str], Session] = {}


def save(session_id: str, session: Session) -> None:
    now = time.monotonic()
    for stale in [
        k for k, s in _SESSIONS.items() if now - s.touched > config.SESSION_TIMEOUT_SECONDS
    ]:
        del _SESSIONS[stale]
    session.touched = now
    _SESSIONS[(session.user_id, session_id)] = session


def load(session_id: str, user_id: str) -> Session | None:
    """Return the open session, or None if it is unknown, someone else's, or expired."""
    session = _SESSIONS.get((user_id, session_id))
    if session is None:
        return None
    if time.monotonic() - session.touched > config.SESSION_TIMEOUT_SECONDS:
        drop(session_id, user_id)
        return None
    return session


def drop(session_id: str, user_id: str) -> None:
    _SESSIONS.pop((user_id, session_id), None)
