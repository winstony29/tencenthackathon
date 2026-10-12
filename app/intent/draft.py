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
    "stock_order": re.compile(r"\b(buy|sell|shares?|stocks?)\b"),
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
    return Draft(
        draft_id=f"d_{secrets.token_hex(3)}",
        user_id=user_id,
        session_id=session_id,
        transcript=transcript,
        created_at=datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0),
        actions=[builders[kind](p, options, data) for kind in ordered],
        evidence=[evidence(pick) for pick in decision.picks.values()],
    )


def _transfer(p: dict[str, str], options: Options, data: UserData) -> TransferAction:
    source = _account(data, p["transfer_from"], prefer="savings")
    payee = next(x for x in data.accounts.payees if x.payee_id == p["recipient"])
    return TransferAction(
        type="transfer",
        from_account_id=source.account_id,
        from_account_label=source.label,
        to_payee_id=payee.payee_id,
        to_payee_label=payee.label,
        amount=_money(options.numbers[p["transfer_amount"]]),
        currency=source.currency,
    )


def _bill(p: dict[str, str], options: Options, data: UserData) -> BillPaymentAction:
    source = _account(data, p["bill_from"], prefer="savings")
    biller = next(x for x in data.accounts.billers if x.biller_id == p["biller"])
    amount = biller.amount_due if p["bill_amount"] == "due" else options.numbers[p["bill_amount"]]
    return BillPaymentAction(
        type="bill_payment",
        from_account_id=source.account_id,
        from_account_label=source.label,
        biller_id=biller.biller_id,
        biller_label=biller.label,
        bill_reference=readonly.get_bill_reference(biller.biller_id),
        amount=_money(amount),
        currency=source.currency,
    )


def _stock(p: dict[str, str], options: Options, data: UserData) -> StockOrderAction:
    quote = next(q for q in data.quotes if q.ticker == p["ticker"])
    source = _account(data, "default", prefer="invest", currency=quote.currency)
    quantity = options.numbers[p["quantity"]]
    if quantity != quantity.to_integral_value() or quantity <= 0:
        raise Unbuildable("Please say a whole number of shares.")
    limit = p["order_type"] == "limit"
    return StockOrderAction(
        type="stock_order",
        from_account_id=source.account_id,
        from_account_label=source.label,
        side=p["side"],
        ticker=quote.ticker,
        company_name=quote.company_name,
        quantity=int(quantity),
        order_type=p["order_type"],
        limit_price=_money(options.numbers[p["limit_price"]]) if limit else None,
        currency=quote.currency,
    )


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
