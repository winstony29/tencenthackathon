"""Confidence gate: decides whether to ask the user or proceed.

Owner: Winston
Diagram box: Confidence gate (Intent detection column)
Contract: none; called by app/intent/router.py
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app import config
from app.intent.jev import Reply
from app.intent.options import ACTION_TYPES, Options, UserData

SLOTS = {
    "transfer": ("recipient", "transfer_amount", "transfer_from"),
    "bill_payment": ("biller", "bill_amount", "bill_from"),
    "stock_order": ("ticker", "side", "quantity", "order_type"),
    "account_transfer": ("move_from", "move_to", "move_amount"),
}
# "none" on these means the user named something we can offer as chips.
_PICK_FROM_LIST = {"recipient", "biller", "ticker", "move_from", "move_to"}
# "none" on these means a number was never said; chips can't offer a value nobody said.
_NEED_NUMBER = {"transfer_amount", "quantity", "limit_price", "move_amount"}
# Each account question and the amount it pays. "default" resolves to one account, so
# picking that account builds the same draft and their probabilities count together.
_FROM_AMOUNT = {"transfer_from": "transfer_amount", "bill_from": "bill_amount"}


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


def pick(
    replies: list[Reply],
    question: str,
    option: str | None = None,
    same: dict[str, str] | None = None,
) -> Pick:
    """Find the option with the highest average probability across phrasings, and its spread.

    Pass `option` to report on that option instead (the one the user chose). `same` maps
    an option to another that builds the same draft; its probability counts for that one.
    """
    same = same or {}
    tables = []
    for r in replies:
        merged: dict[str, float] = {}
        for o, p in r.answers[question].probabilities.items():
            merged[same.get(o, o)] = merged.get(same.get(o, o), 0.0) + p
        tables.append(merged)
    average = {o: sum(t[o] for t in tables) / len(tables) for o in tables[0]}
    best = same.get(option, option) if option else max(average, key=average.get)
    seen = [t[best] for t in tables]
    return Pick(question, best, average[best], max(seen) - min(seen), average)


def tau(amount: Decimal) -> float:
    """Least average probability a pick needs; stricter when large amounts are involved."""
    return config.GATE_TAU_HIGH if amount > config.GATE_HIGH_AMOUNT else config.GATE_TAU_BASE


def trusted(p: Pick, amount: Decimal) -> bool:
    """Proceed only if Jev is sure (average >= tau) and the wording didn't matter (small spread)."""
    return p.average_probability >= tau(amount) and p.spread <= config.GATE_DELTA


def _same(name: str, picks: dict[str, Pick], options: Options | None) -> dict[str, str]:
    """The account "default" resolves to for this account question, as an alias of it."""
    if options is None or name not in _FROM_AMOUNT:
        return {}
    said = options.said.get(picks[_FROM_AMOUNT[name]].option)
    account = said.account_id if said and said.account_id else options.usual_account
    return {account: "default"} if account else {}


def decide(
    replies: list[Reply],
    answers: dict[str, str],
    amount: Decimal,
    options: Options | None = None,
    data: UserData | None = None,
) -> Decision:
    """Decide whether Jev's answers are trustworthy enough to build a draft.

    Checks which action types were asked for, then every slot those actions need,
    in a fixed order. The user's own answers count as certain. Returns the first
    question that needs the user, or a missing number, or the settled picks.
    """
    picks: dict[str, Pick] = {}

    def settle(name: str, exposure: Decimal = Decimal(0)) -> bool:
        picks[name] = pick(replies, name, answers.get(name), _same(name, picks, options))
        return name in answers or trusted(picks[name], exposure)

    # Uncertain *negative* categories are not missing transaction fields. Only skip
    # them when another task is clear and the text supplies no cue for that category.
    cues = {
        "transfer": r"\b(send|transfer|remit|give|pay)\b",
        "bill_payment": r"\b(bill|card|rent|utilities|invoice)\b",
        "stock_order": r"\b(buy|sell|invest|shares?|stocks?|trade)\b",
        "account_transfer": r"\b(move|between|own|convert|exchange)\b|\bfrom\b.*\bto\b",
    }
    for kind in ACTION_TYPES:
        settle(f"has_{kind}")
    confirmed = any(
        p.option == "yes" and (p.question in answers or trusted(p, Decimal(0)))
        for p in picks.values()
    )
    for kind in ACTION_TYPES:
        name = f"has_{kind}"
        p = picks[name]
        if name in answers or trusted(p, Decimal(0)):
            continue
        unrelated = (
            options is not None
            and confirmed
            and p.option == "no"
            and p.probabilities.get("no", 0) > p.probabilities.get("yes", 0)
            and not re.search(cues[kind], options.text, re.I)
        )
        if not unrelated:
            return Decision(picks, ask=name)
    actions = [kind for kind in ACTION_TYPES if picks[f"has_{kind}"].option == "yes"]
    # ponytail: one action per type; a second transfer is refused rather than silently dropped.
    if "transfer" in actions:
        if not settle("several_transfers"):
            return Decision(picks, actions, ask="several_transfers")
        if picks["several_transfers"].option == "yes":
            return Decision(picks, actions, missing="several_transfers")
    for kind in actions:
        exposure = amount
        if options is not None:
            amount_name = {
                "transfer": "transfer_amount",
                "bill_payment": "bill_amount",
                "account_transfer": "move_amount",
                "stock_order": "quantity",
            }[kind]
            selected = pick(replies, amount_name, answers.get(amount_name)).option
            exposure = options.numbers.get(selected, Decimal(0))
            if data and kind == "bill_payment" and selected == "due":
                biller = pick(replies, "biller", answers.get("biller")).option
                exposure = next(
                    (b.amount_due for b in data.accounts.billers if b.biller_id == biller),
                    Decimal(0),
                )
            if kind == "stock_order":
                if selected.startswith("m"):
                    exposure = options.numbers.get("n" + selected[1:], Decimal(0))
                elif data:
                    ticker = pick(replies, "ticker", answers.get("ticker")).option
                    quote = next((q for q in data.quotes if q.ticker == ticker), None)
                    if quote:
                        exposure *= quote.price
        slots = list(SLOTS[kind])
        for name in slots:  # a limit order appends limit_price, which this loop still visits
            # A single literal amount for a single money task needs interpretation
            # confidence, not the stricter authorization threshold for its magnitude.
            explicit = (
                options is not None
                and len(actions) == 1
                and name in {"transfer_amount", "move_amount"}
                and len(options.said) == 1
                and all(n.span is not None and not n.remaining for n in options.said.values())
            )
            threshold_amount = Decimal(0) if explicit else exposure
            if not settle(name, threshold_amount) or (
                picks[name].option == "none" and name in _PICK_FROM_LIST
            ):
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
