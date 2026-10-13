"""Draft builder: assembles the draft from Jev's picks; no LLM writes any value.

Owner: Winston
Diagram box: Draft builder (Intent detection column)
Contract: docs/CONTRACTS.md §2
"""

import re
import secrets
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

from app import config
from app.bank import readonly
from app.contracts import (
    Account,
    AccountTransferAction,
    BillPaymentAction,
    Draft,
    Evidence,
    FxConversionAction,
    FxResponse,
    HoldingValue,
    Objective,
    QuoteResponse,
    Scenario,
    StockOrderAction,
    TransferAction,
)
from app.intent.gate import Decision, Pick
from app.intent.options import Options, UserData, parse_numbers

CENT = Decimal("0.01")
# ponytail: actions are ordered by where their first keyword appears; ask Jev for the order
# if multi-action commands come out misordered in H-01.
_MENTIONS = {
    "transfer": re.compile(r"\b(send|transfer|give|wire)\b"),
    "bill_payment": re.compile(r"\b(bill|card)\b"),
    "stock_order": re.compile(r"\b(buy|sell|invest|shares?|stocks?)\b"),
    "account_transfer": re.compile(r"\b(move|shift|top up|convert)\b"),
}


class Unbuildable(Exception):
    """The picks are trustworthy but can't make a valid action; the message tells the user why."""


HELP = (
    "I can send money to your payees, pay your card bill, buy or sell shares, and convert "
    "currency for a purchase. I can also tell you your balances, recent transactions, "
    "upcoming bills, your shares, share prices, exchange rates and who you can pay."
)
LATEST = 5  # transactions shown when no period is said (D-035)
_SPAN = re.compile(r"\s*(day|week)s?\b")


def _said_days(transcript: str) -> int:
    """How many days "the last 30 days" or "past 2 weeks" covers; 7 when no number is said."""
    for n in parse_numbers(transcript):
        unit = _SPAN.match(transcript.lower(), n.span[1]) if n.span else None
        if unit and n.value == n.value.to_integral_value() and 0 < n.value <= 366:
            return int(n.value) * (7 if unit.group(1) == "week" else 1)
    return 7


def _period(period: str, now: datetime, transcript: str) -> tuple[datetime, datetime | None]:
    """When a transactions period begins, and when it ends (None: now)."""
    if period == "days":
        return now - timedelta(days=_said_days(transcript)), None
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == "this_month":
        return month, None
    return (month - timedelta(days=1)).replace(day=1), month  # last_month


def answer(kind: str, picks: dict[str, str], data: UserData, user_id: str, transcript: str) -> dict:
    """The payload of a read-only answer (CONTRACTS §3.1 answers); every value read by code."""
    a = data.accounts
    if kind == "balance":
        return {
            "accounts": [x for x in a.accounts if picks["balance_account"] in ("all", x.account_id)]
        }
    if kind == "transactions":
        return {"transactions": _transactions(picks, user_id, transcript)}
    if kind == "bills":
        recurring = picks["bills_scope"] == "recurring"
        return {"bills": [b for b in readonly.list_bills(user_id) if b.recurrence or not recurring]}
    if kind == "holdings":
        quotes = {q.ticker: q for q in data.quotes}
        return {
            "holdings": [
                HoldingValue(
                    ticker=h.ticker,
                    company_name=quotes[h.ticker].company_name,
                    quantity=h.quantity,
                    price=quotes[h.ticker].price,
                    currency=quotes[h.ticker].currency,
                    value=(quotes[h.ticker].price * h.quantity).quantize(CENT),
                    as_of=quotes[h.ticker].as_of,
                )
                for h in a.holdings
                if h.ticker in quotes
            ]
        }
    if kind == "quote":  # no company named: every price
        return {"quotes": [q for q in data.quotes if picks["ticker"] in ("none", q.ticker)]}
    if kind == "fx_rate":
        sell, buy = picks["fx_pair"].split("-")
        return {"rate": _rate(sell, buy)}
    if kind == "payees":
        return {"payees": a.payees}
    return {"message": HELP}


def _transactions(picks: dict[str, str], user_id: str, transcript: str) -> list:
    """The user's transactions, filtered and ordered as Jev picked (D-037)."""
    account, period = picks["transactions_account"], picks["transactions_period"]
    payee, direction = picks["transactions_payee"], picks["transactions_direction"]
    entries = [
        e
        for e in readonly.list_transactions(user_id)
        if account in ("all", e.account_id)
        and payee in ("any", e.payee_id)
        and direction in ("all", "in" if e.direction == "credit" else "out")
    ]
    if period != "latest":
        start, end = _period(period, datetime.now(ZoneInfo(config.TIMEZONE)), transcript)
        entries = [e for e in entries if e.time >= start and (end is None or e.time < end)]
    if picks["transactions_order"] == "largest":
        # Rank by SGD value, so US$2,000 outranks S$2,500; the amounts shown stay as recorded.
        rates = {c: _rate(c, "SGD").rate for c in {e.currency for e in entries} - {"SGD"}}
        entries.sort(key=lambda e: e.amount * rates.get(e.currency, 1), reverse=True)
    return entries[:LATEST] if period == "latest" else entries


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
    builders = {
        "transfer": _transfer,
        "bill_payment": _bill,
        "stock_order": _stock,
        "account_transfer": _move,
    }
    spent: dict[str, Decimal] = {}  # money earlier actions take, by source account
    actions = []
    for kind in ordered:
        built = builders[kind](p, options, data, spent)
        actions += built if isinstance(built, list) else [built]  # a conversion precedes its order
    if len(actions) > config.MAX_ACTIONS_PER_DRAFT:
        raise Unbuildable(
            f"I can do up to {config.MAX_ACTIONS_PER_DRAFT} steps at once. Please split it up."
        )
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


def _move(
    p: dict[str, str], options: Options, data: UserData, spent: dict[str, Decimal]
) -> AccountTransferAction | FxConversionAction:
    """Money between the user's own accounts (§2.1, v0.7 proposed).

    One currency is an account_transfer; two currencies are a conversion at the bank's rate.
    """
    source = _account(data, p["move_from"], "savings")
    target = _account(data, p["move_to"], "savings")
    if source.account_id == target.account_id:
        raise Unbuildable("Please name two different accounts.")
    _same_currency(options, p["move_amount"], source)
    amount = _money(_sum(p["move_amount"], options, data, spent))
    _spend(spent, source, amount)
    ends = {
        "from_account_id": source.account_id,
        "from_account_label": source.label,
        "to_account_id": target.account_id,
        "to_account_label": target.label,
    }
    if source.currency == target.currency:
        return AccountTransferAction(
            type="account_transfer", **ends, amount=amount, currency=source.currency
        )
    rate = _rate(source.currency, target.currency)
    return FxConversionAction(
        type="fx_conversion",
        **ends,
        sell_amount=amount,
        sell_currency=rate.from_,
        buy_amount=(amount * rate.rate).quantize(CENT, rounding=ROUND_DOWN),
        buy_currency=rate.to,
        rate=rate.rate,
        rate_as_of=rate.as_of,
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
) -> list[FxConversionAction | StockOrderAction]:
    quote = next(q for q in data.quotes if q.ticker == p["ticker"])
    buying = p["side"] == "buy"
    quantity, chosen, conversion = _shares(p["quantity"], quote, buying, options, data, spent)
    source = _account(data, chosen, prefer="invest", currency=quote.currency)
    limit = p["order_type"] == "limit"
    if buying:  # an estimate at the quote, so a later "rest" of Invest stays sensible
        _spend(spent, source, quantity * quote.price)
    order = StockOrderAction(
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
    return [conversion, order] if conversion else [order]


def _shares(
    oid: str,
    quote: QuoteResponse,
    buying: bool,
    options: Options,
    data: UserData,
    spent: dict[str, Decimal],
) -> tuple[int, str, FxConversionAction | None]:
    """The share count, the order's account ("default" when none) and any conversion (§10.3)."""
    if not oid.startswith("m"):
        return _whole(options.numbers[oid]), "default", None
    oid = "n" + oid[1:]
    n = options.said[oid]
    money = _sum(oid, options, data, spent).quantize(CENT, rounding=ROUND_DOWN)
    rate = None if n.currency == quote.currency else _rate(n.currency, quote.currency)
    worth = money if rate is None else (money * rate.rate).quantize(CENT, rounding=ROUND_DOWN)
    quantity = int(worth / quote.price)  # whole shares, rounding down
    if quantity < 1:
        raise Unbuildable(
            f"{n.currency} {money:,} doesn't buy one {quote.company_name} share "
            f"({quote.price} {quote.currency})."
        )
    if rate is None:
        return quantity, _from(options, oid, "default"), None
    if not buying:  # the proceeds stay in the share's currency, so nothing is converted
        return quantity, "default", None
    conversion = _conversion(oid, money, worth, rate, options, data, spent)
    return quantity, conversion.to_account_id, conversion


def _rate(sell: str, buy: str) -> FxResponse:
    # Jayanth's readonly module may not provide rates yet; say so instead of crashing.
    get_fx_rate = getattr(readonly, "get_fx_rate", None)
    if get_fx_rate is None:
        raise Unbuildable("Currency conversion isn't available yet.")
    try:
        return get_fx_rate(sell, buy)
    except LookupError:
        raise Unbuildable(f"I can't convert {sell} to {buy}.") from None


def _conversion(
    oid: str,
    money: Decimal,
    worth: Decimal,
    rate: FxResponse,
    options: Options,
    data: UserData,
    spent: dict[str, Decimal],
) -> FxConversionAction:
    source = _account(data, _from(options, oid, "default"), "savings", currency=rate.from_)
    target = _account(data, "default", prefer="invest", currency=rate.to)
    _spend(spent, source, money)
    return FxConversionAction(
        type="fx_conversion",
        from_account_id=source.account_id,
        from_account_label=source.label,
        to_account_id=target.account_id,
        to_account_label=target.label,
        sell_amount=money,
        sell_currency=rate.from_,
        buy_amount=worth,
        buy_currency=rate.to,
        rate=rate.rate,
        rate_as_of=rate.as_of,
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
    cents = value.quantize(CENT)  # a sub-cent value must fail here, not in model validation
    if cents <= 0:
        raise Unbuildable("Please say an amount above zero.")
    return cents


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
        if objective == "prepare" and _asked_conversion(current):
            raise Unbuildable(
                "To change a currency conversion, please say the whole command again."
            )
        return current, None
    _no_stale_conversion(current, changed)
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


def _asked_conversion(current: Draft) -> bool:
    """A conversion the user asked for, not one that funds the share order after it."""
    return any(
        a.type == "fx_conversion"
        and not any(
            b.type == "stock_order" and b.from_account_id == a.to_account_id
            for b in current.actions[i + 1 :]
        )
        for i, a in enumerate(current.actions)
    )


def _no_stale_conversion(current: Draft, changed: dict[int, dict]) -> None:
    # A conversion is sized from the amounts around it and amending doesn't recompute it, so
    # any amount, source or share-count change would leave it funding the wrong sum.
    if all(a.type != "fx_conversion" for a in current.actions):
        return
    if any({"amount", "from", "quantity"} & fields.keys() for fields in changed.values()):
        raise Unbuildable(
            "Please say the whole command again to change an amount that a conversion depends on."
        )


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
    if "to" in fields:
        target = _account(data, fields["to"], prefer="savings")
        update |= {"to_account_id": target.account_id, "to_account_label": target.label}
    if "amount" in fields and fields["amount"] != "due":
        source = _account(data, fields.get("from", action.from_account_id), prefer="savings")
        _same_currency(options, fields["amount"], source)
    amended = type(action).model_validate({**action.model_dump(), **update})
    if amended.type == "account_transfer":
        target = _account(data, amended.to_account_id, prefer="savings")
        if target.account_id == amended.from_account_id or target.currency != amended.currency:
            raise Unbuildable("Please name two different accounts in the same currency.")
    return amended


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
