"""Confidence gate: decides whether to ask the user or proceed.

Owner: Winston
Diagram box: Confidence gate (Intent detection column)
Contract: none; called by app/intent/router.py
"""

from dataclasses import dataclass, field
from decimal import Decimal

from app import config
from app.intent.jev import Reply
from app.intent.options import ACTION_TYPES

SLOTS = {
    "transfer": ("recipient", "transfer_amount", "transfer_from"),
    "bill_payment": ("biller", "bill_amount", "bill_from"),
    "stock_order": ("ticker", "side", "quantity", "order_type"),
}
# "none" on these means the user named something we can offer as chips.
_PICK_FROM_LIST = {"recipient", "biller", "ticker"}
# "none" on these means a number was never said; chips can't offer a value nobody said.
_NEED_NUMBER = {"transfer_amount", "quantity", "limit_price"}


@dataclass(frozen=True)
class Pick:
    question: str
    option: str
    average_probability: float
    spread: float
    probabilities: dict[str, float]  # average probability of every option


@dataclass(frozen=True)
class Decision:
    picks: dict[str, Pick] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)  # action types the user asked for
    ask: str | None = None  # first question that needs the user
    missing: str | None = None  # a number the user never said


def pick(replies: list[Reply], question: str, option: str | None = None) -> Pick:
    """Find the option with the highest average probability across phrasings, and its spread.

    Pass `option` to report on that option instead (the one the user chose).
    """
    tables = [r.answers[question].probabilities for r in replies]
    average = {o: sum(t[o] for t in tables) / len(tables) for o in tables[0]}
    best = option or max(average, key=average.get)
    seen = [t[best] for t in tables]
    return Pick(question, best, average[best], max(seen) - min(seen), average)


def tau(amount: Decimal) -> float:
    """Least average probability a pick needs; stricter when large amounts are involved."""
    return config.GATE_TAU_HIGH if amount > config.GATE_HIGH_AMOUNT else config.GATE_TAU_BASE


def trusted(p: Pick, amount: Decimal) -> bool:
    """Proceed only if Jev is sure (average >= tau) and the wording didn't matter (small spread)."""
    return p.average_probability >= tau(amount) and p.spread <= config.GATE_DELTA


def decide(replies: list[Reply], answers: dict[str, str], amount: Decimal) -> Decision:
    """Decide whether Jev's answers are trustworthy enough to build a draft.

    Checks which action types were asked for, then every slot those actions need,
    in a fixed order. The user's own answers count as certain. Returns the first
    question that needs the user, or a missing number, or the settled picks.
    """
    picks: dict[str, Pick] = {}

    def settle(name: str) -> bool:
        picks[name] = pick(replies, name, answers.get(name))
        return name in answers or trusted(picks[name], amount)

    for kind in ACTION_TYPES:
        if not settle(f"has_{kind}"):
            return Decision(picks, ask=f"has_{kind}")
    actions = [kind for kind in ACTION_TYPES if picks[f"has_{kind}"].option == "yes"]
    # ponytail: one action per type; a second transfer is refused rather than silently dropped.
    if "transfer" in actions:
        if not settle("several_transfers"):
            return Decision(picks, actions, ask="several_transfers")
        if picks["several_transfers"].option == "yes":
            return Decision(picks, actions, missing="several_transfers")
    for kind in actions:
        slots = list(SLOTS[kind])
        for name in slots:  # a limit order appends limit_price, which this loop still visits
            if not settle(name) or (picks[name].option == "none" and name in _PICK_FROM_LIST):
                return Decision(picks, actions, ask=name)
            if picks[name].option == "none" and name in _NEED_NUMBER:
                return Decision(picks, actions, missing=name)
            if name == "order_type" and picks[name].option == "limit":
                slots.append("limit_price")
    return Decision(picks, actions)


def decide_each(
    replies: list[Reply], answers: dict[str, str], amount: Decimal, names: list[str]
) -> Decision:
    """Settle the listed questions in order, stopping at the first that needs the user.

    Used for follow-up turns, where every question is about an existing task.
    """
    picks: dict[str, Pick] = {}
    for name in names:
        picks[name] = pick(replies, name, answers.get(name))
        if name not in answers and not trusted(picks[name], amount):
            return Decision(picks, ask=name)
    return Decision(picks)
