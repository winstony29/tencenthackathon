"""Draft builder: assembles the draft from Jev's picks; no LLM writes any value.

Owner: Winston
Diagram box: Draft builder (Intent detection column)
Contract: docs/CONTRACTS.md §2
"""

import re
import secrets
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app import config
from app.bank import readonly
from app.contracts import (
    Account,
    BillPaymentAction,
    Draft,
    Evidence,
    Objective,
    QuoteResponse,
    Scenario,
    StockOrderAction,
    TransferAction,
)
from app.intent.gate import Decision, Pick
from app.intent.options import Options, UserData

CENT = Decimal("0.01")
# ponytail: actions are ordered by where their first keyword appears; ask Jev for the order
# if multi-action commands come out misordered in H-01.
_MENTIONS = {
    "transfer": re.compile(r"\b(send|transfer|give|wire)\b"),
    "bill_payment": re.compile(r"\b(bill|card)\b"),
    "stock_order": re.compile(r"\b(buy|sell|invest|shares?|stocks?)\b"),
}


class Unbuildable(Exception):
    """The picks are trustworthy but can't make a valid action; the message tells the user why."""


def build(
    transcript: str,
    user_id: str,
    session_id: str,
    decision: Decision,
    options: Options,
    data: UserData,
) -> Draft:
    """Turn settled picks into the draft the user will sign.

    Every value comes from the user's data or from numbers code parsed out of the
    transcript. Raises Unbuildable when, for example, no account holds the right currency.
    """
    p = {name: pick.option for name, pick in decision.picks.items()}
    lowered = transcript.lower()
    ordered = sorted(decision.actions, key=lambda kind: _first_mention(lowered, kind))
    builders = {"transfer": _transfer, "bill_payment": _bill, "stock_order": _stock}
    spent: dict[str, Decimal] = {}  # money earlier actions take, by source account
    actions = [builders[kind](p, options, data, spent) for kind in ordered]
    return Draft(
        draft_id=f"d_{secrets.token_hex(3)}",
        user_id=user_id,
        session_id=session_id,
        transcript=transcript,
        created_at=datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0),
        actions=actions,
        evidence=[evidence(pick) for pick in decision.picks.values()],
    )


def _transfer(
    p: dict[str, str], options: Options, data: UserData, spent: dict[str, Decimal]
) -> TransferAction:
    source = _account(data, _from(options, p["transfer_amount"], p["transfer_from"]), "savings")
    _same_currency(options, p["transfer_amount"], source)
    payee = next(x for x in data.accounts.payees if x.payee_id == p["recipient"])
    amount = _money(_sum(p["transfer_amount"], options, data, spent))
    _spend(spent, source, amount)
    return TransferAction(
        type="transfer",
        from_account_id=source.account_id,
        from_account_label=source.label,
        to_payee_id=payee.payee_id,
        to_payee_label=payee.label,
        amount=amount,
        currency=source.currency,
    )


def _bill(
    p: dict[str, str], options: Options, data: UserData, spent: dict[str, Decimal]
) -> BillPaymentAction:
    due = p["bill_amount"] == "due"
    chosen = p["bill_from"] if due else _from(options, p["bill_amount"], p["bill_from"])
    source = _account(data, chosen, prefer="savings")
    biller = next(x for x in data.accounts.billers if x.biller_id == p["biller"])
    if not due:
        _same_currency(options, p["bill_amount"], source)
    amount = biller.amount_due if due else _sum(p["bill_amount"], options, data, spent)
    amount = _money(amount)
    _spend(spent, source, amount)
    return BillPaymentAction(
        type="bill_payment",
        from_account_id=source.account_id,
        from_account_label=source.label,
        biller_id=biller.biller_id,
        biller_label=biller.label,
        bill_reference=readonly.get_bill_reference(biller.biller_id),
        amount=amount,
        currency=source.currency,
    )


def _stock(
    p: dict[str, str], options: Options, data: UserData, spent: dict[str, Decimal]
) -> StockOrderAction:
    quote = next(q for q in data.quotes if q.ticker == p["ticker"])
    quantity, chosen = _shares(p["quantity"], quote, options, data, spent)
    source = _account(data, chosen, prefer="invest", currency=quote.currency)
    limit = p["order_type"] == "limit"
    if p["side"] == "buy":  # an estimate at the quote, so a later "rest" of Invest stays sensible
        _spend(spent, source, quantity * quote.price)
    return StockOrderAction(
        type="stock_order",
        from_account_id=source.account_id,
        from_account_label=source.label,
        side=p["side"],
        ticker=quote.ticker,
        company_name=quote.company_name,
        quantity=quantity,
        order_type=p["order_type"],
        limit_price=_money(options.numbers[p["limit_price"]]) if limit else None,
        currency=quote.currency,
    )


def _shares(
    oid: str, quote: QuoteResponse, options: Options, data: UserData, spent: dict[str, Decimal]
) -> tuple[int, str]:
    """The share count and the account a relative sum names ("default" when none)."""
    if not oid.startswith("m"):
        return _whole(options.numbers[oid]), "default"
    oid = "n" + oid[1:]
    n = options.said[oid]
    money = _sum(oid, options, data, spent)
    if n.currency != quote.currency:
        raise Unbuildable(f"I can't convert {n.currency} to {quote.currency} yet.")
    quantity = int(money / quote.price)  # whole shares, rounding down (§10.3)
    if quantity < 1:
        raise Unbuildable(
            f"{n.currency} {money:,} doesn't buy one {quote.company_name} share "
            f"({quote.price} {quote.currency})."
        )
    return quantity, _from(options, oid, "default")


def _account(data: UserData, chosen: str, prefer: str, currency: str | None = None) -> Account:
    accounts = data.accounts.accounts
    if chosen != "default":
        return next(a for a in accounts if a.account_id == chosen)
    # ponytail: "usual account" = first account of the preferred type; a flagged default in
    # data/bank.json would replace this guess.
    candidates = [a for a in accounts if currency is None or a.currency == currency]
    if not candidates:
        raise Unbuildable(f"You don't have an account in {currency}.")
    return next((a for a in candidates if prefer in a.type), candidates[0])


def _from(options: Options, oid: str, chosen: str) -> str:
    """A relative amount ("half of savings") names its own account when none was picked."""
    account_id = options.said[oid].account_id
    return account_id if account_id and chosen == "default" else chosen


def _sum(oid: str, options: Options, data: UserData, spent: dict[str, Decimal]) -> Decimal:
    """The money an option stands for; "the rest" subtracts what earlier actions took."""
    n = options.said[oid]
    if not n.remaining:
        return n.value
    account = next(a for a in data.accounts.accounts if a.account_id == n.account_id)
    left = account.balance - spent.get(account.account_id, Decimal(0))
    if left <= 0:
        raise Unbuildable(f"Nothing is left in {account.label} after the earlier actions.")
    return left


def _spend(spent: dict[str, Decimal], source: Account, amount: Decimal) -> None:
    spent[source.account_id] = spent.get(source.account_id, Decimal(0)) + amount


def _same_currency(options: Options, oid: str, source: Account) -> None:
    # Amounts are SGD unless US dollars are named (§10.3); never convert silently.
    if options.said[oid].currency != source.currency:
        raise Unbuildable(f"I can only send {source.currency} from {source.label}.")


def _money(value: Decimal) -> Decimal:
    if value <= 0:
        raise Unbuildable("Please say an amount above zero.")
    return value.quantize(CENT)


def _first_mention(text: str, kind: str) -> int:
    found = _MENTIONS[kind].search(text)
    return found.start() if found else len(text)


def evidence(p: Pick) -> Evidence:
    """Record one of Jev's answers for the audit log and the debug view."""
    return Evidence(
        question=p.question,
        pick=p.option,
        avg_p=round(p.average_probability, 4),
        spread=round(p.spread, 4),
        probabilities={o: round(v, 4) for o, v in p.probabilities.items()},
    )


def amend(
    current: Draft,
    decision: Decision,
    objective: Objective,
    text: str,
    options: Options,
    data: UserData,
) -> tuple[Draft, Scenario | None]:
    """Apply a follow-up turn's picks to the draft, or to a scenario when simulating.

    For `simulate`, new amounts and quantities go into the scenario and the draft
    stays exactly as it was. Otherwise changed fields are rebuilt by code and the
    turn is added to the draft's transcript, so validation sees every word said.
    """
    changed: dict[int, dict] = {}
    for name, p in decision.picks.items():
        if name == "objective" or p.option == "keep":
            continue
        index, field = name[1:].split("_", 1)
        changed.setdefault(int(index), {})[field] = p.option
    if objective == "simulate":
        scenario = {
            i: {
                f: _scenario_value(f, o, current.actions[i], options)
                for f, o in fields.items()
                if f in ("amount", "quantity")
            }
            for i, fields in changed.items()
        }
        return current, {i: v for i, v in scenario.items() if v} or None
    if not changed:
        return current, None
    actions = [
        _amended(a, changed.get(i, {}), options, data) for i, a in enumerate(current.actions)
    ]
    turns = f"{current.transcript}\n{text}"
    updated = Draft.model_validate(
        {
            **current.model_dump(),
            "actions": [a.model_dump() for a in actions],
            "transcript": turns,
            "created_at": datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0),
            "evidence": [e.model_dump() for e in current.evidence]
            + [evidence(p).model_dump() for p in decision.picks.values()],
        }
    )
    return updated, None


def _amended(action, fields: dict[str, str], options: Options, data: UserData):
    if not fields:
        return action
    update = {}
    if "amount" in fields:
        due = next(
            (
                b.amount_due
                for b in data.accounts.billers
                if getattr(action, "biller_id", None) == b.biller_id
            ),
            None,
        )
        # A follow-up has no earlier actions to subtract, so "the rest" is the whole balance.
        update["amount"] = _money(
            due if fields["amount"] == "due" else _sum(fields["amount"], options, data, {})
        )
    if "quantity" in fields:
        update["quantity"] = _whole(options.numbers[fields["quantity"]])
    if "from" in fields:
        source = _account(data, fields["from"], prefer="savings")
        update |= {
            "from_account_id": source.account_id,
            "from_account_label": source.label,
            "currency": source.currency,
        }
    if "recipient" in fields:
        payee = next(x for x in data.accounts.payees if x.payee_id == fields["recipient"])
        update |= {"to_payee_id": payee.payee_id, "to_payee_label": payee.label}
    if "amount" in fields and fields["amount"] != "due":
        source = _account(data, fields.get("from", action.from_account_id), prefer="savings")
        _same_currency(options, fields["amount"], source)
    return type(action).model_validate({**action.model_dump(), **update})


def _scenario_value(field: str, option: str, action, options: Options) -> str:
    if field == "quantity":
        return str(_whole(options.numbers[option]))
    if option == "due":
        return str(action.amount)
    return str(_money(options.numbers[option]))


def _whole(value: Decimal) -> int:
    if value != value.to_integral_value() or value <= 0:
        raise Unbuildable("Please say a whole number of shares.")
    return int(value)
