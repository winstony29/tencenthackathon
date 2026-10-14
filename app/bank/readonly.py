"""Read-only views of accounts, payees, billers and quotes; the only bank file the AI may import.

Owner: Jayanth
Diagram box: User data
Contract: docs/CONTRACTS.md §3.3 (GET /api/accounts, GET /api/quote, readonly functions)
"""

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.contracts import (
    Account,
    AccountsResponse,
    Biller,
    FxResponse,
    Holding,
    Payee,
    QuoteResponse,
)


def _load_database() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "data" / "bank.json"
    # No cache or shared objects: callers cannot change later reads by mutating a result.
    database = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    if not isinstance(database, dict) or database.get("schema_version") != "1.0":
        raise ValueError("Invalid banking database version or structure")
    if not isinstance(database.get("users"), dict) or not isinstance(database.get("quotes"), dict):
        raise ValueError("Invalid banking database collections")
    return database


def get_accounts(user_id: str) -> AccountsResponse:
    """Read a user's public banking data; unknown users raise HTTP 404.

    Invalid or missing seed data raises an error without substituting demo values.
    """
    users = _load_database()["users"]
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found.")
    user = users[user_id]
    # Select allowed fields before validation because contract models forbid extras.
    collections = {"accounts": Account, "payees": Payee, "billers": Biller, "holdings": Holding}
    return AccountsResponse.model_validate(
        {
            name: [{field: record[field] for field in model.model_fields} for record in user[name]]
            for name, model in collections.items()
        }
    )


def get_quote(ticker: str) -> QuoteResponse:
    """Read a stored quote by exact ticker; unknown tickers raise HTTP 404.

    Prices and timestamps come from the seed, and aliases remain internal.
    """
    quotes = _load_database()["quotes"]
    if ticker not in quotes:
        raise HTTPException(status_code=404, detail="Quote not found.")
    record = quotes[ticker]
    quote = QuoteResponse.model_validate(
        {field: record[field] for field in QuoteResponse.model_fields}
    )
    if quote.ticker != ticker:
        raise ValueError("Banking quote ticker does not match its database key")
    return quote


def list_quotes() -> list[QuoteResponse]:
    """Every tradable ticker's stored quote, for the intent engine's ticker options."""
    return [get_quote(ticker) for ticker in _load_database()["quotes"]]


def get_bill_reference(biller_id: str) -> str:
    """The stored reference for a biller; an unknown biller raises LookupError."""
    for user in _load_database()["users"].values():
        for biller in user["billers"]:
            if biller["biller_id"] == biller_id:
                return biller["bill_reference"]
    raise LookupError(f"Unknown biller {biller_id}")


def get_fx_rate(from_currency: str, to_currency: str) -> FxResponse:
    """The stored mock rate for a currency pair; an unknown pair raises LookupError."""
    record = _load_database().get("fx_rates", {}).get(f"{from_currency}-{to_currency}")
    if record is None:
        raise LookupError(f"No rate for {from_currency} to {to_currency}")
    return FxResponse.model_validate(record)


class ForecastDataError(ValueError):
    """Scheduled cashflow is missing or invalid; do not substitute a zero-expense forecast."""


def _forecast_money(value: object) -> Decimal:
    if isinstance(value, Decimal):
        if not value.is_finite() or value < 0 or value.as_tuple().exponent < -2:
            raise ForecastDataError(
                "Forecast amounts must be finite nonnegative two-decimal values"
            )
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]{2}", str(value)):
        raise ForecastDataError("Forecast amounts must be nonnegative two-decimal values")
    return Decimal(value)


def _forecast_time(value: object) -> datetime:
    try:
        time = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if time.utcoffset() is None:
            raise ValueError
        return time
    except (TypeError, ValueError):
        raise ForecastDataError("Forecast dates must include a timezone offset") from None


def _bill_occurrence(record: dict[str, Any], billers: dict[str, Any]) -> dict[str, Any]:
    bill = billers.get(record["biller_id"])
    if not isinstance(bill, dict) or bill.get("payment_state") not in {"outstanding", "paid"}:
        raise ForecastDataError("Scheduled bill has no valid outstanding-payment state")
    if (
        record.get("kind") != "expense"
        or any(
            record.get(field) != bill.get(bill_field)
            for field, bill_field in (
                ("account_id", "from_account_id"),
                ("currency", "currency"),
                ("obligation_id", "obligation_id"),
            )
        )
        or not record.get("obligation_id")
    ):
        raise ForecastDataError("Scheduled bill reference or funding account is inconsistent")
    if _forecast_time(record.get("due_at")) != _forecast_time(bill.get("due_at")):
        raise ForecastDataError("Scheduled bill date is inconsistent")
    amount = _forecast_money(bill.get("amount_due"))
    if "amount" in record and _forecast_money(record["amount"]) != amount:
        raise ForecastDataError("Scheduled payment duplicates a different bill amount")
    if record.get("state") not in {"scheduled", "paid", "cancelled"}:
        raise ForecastDataError("Scheduled bill state is invalid")
    if record["state"] == "paid" and bill["payment_state"] != "paid":
        raise ForecastDataError("Bill and payment states need reconciliation")
    # Cancelling a scheduled payment does not cancel the underlying outstanding debt.
    # The bill is authoritative. A stale scheduled occurrence cannot resurrect a paid bill.
    return {
        **record,
        "amount": f"{amount:.2f}",
        "state": "paid" if bill["payment_state"] == "paid" else "scheduled",
    }


def _cashflow_occurrences(
    user: dict[str, Any], accounts: dict[str, Account]
) -> list[dict[str, Any]]:
    schedule = user.get("cashflow_schedule")
    if not isinstance(schedule, list):
        raise ForecastDataError("Scheduled cashflow data is unavailable")
    billers = {bill["biller_id"]: bill for bill in user["billers"]}
    occurrences, seen, obligations = [], set(), set()
    # Add outstanding bills lacking scheduled payments, then count each obligation once.
    linked_bills = {record.get("biller_id") for record in schedule if isinstance(record, dict)}
    schedule = list(schedule)
    for bill in billers.values():
        if bill["biller_id"] not in linked_bills:
            schedule.append(
                {
                    "occurrence_id": bill.get("obligation_id"),
                    "obligation_id": bill.get("obligation_id"),
                    "biller_id": bill["biller_id"],
                    "account_id": bill.get("from_account_id"),
                    "currency": bill.get("currency"),
                    "due_at": bill.get("due_at"),
                    "state": "scheduled",
                    "kind": "expense",
                    "label": "Fictional outstanding " + bill["name"],
                    "synthetic": True,
                }
            )
    for raw in schedule:
        if not isinstance(raw, dict) or not isinstance(raw.get("occurrence_id"), str):
            raise ForecastDataError("Scheduled occurrence is malformed")
        record = _bill_occurrence(raw, billers) if "biller_id" in raw else dict(raw)
        identifier = record["occurrence_id"]
        if not identifier or identifier in seen:
            raise ForecastDataError("Scheduled occurrence IDs must be unique")
        seen.add(identifier)
        obligation = record.get("obligation_id")
        if obligation is not None:
            if not isinstance(obligation, str) or not obligation or obligation in obligations:
                raise ForecastDataError("Scheduled obligation is duplicated or malformed")
            obligations.add(obligation)
        account = accounts.get(record.get("account_id"))
        if account is None or record.get("currency") != account.currency:
            raise ForecastDataError("Scheduled account or currency is invalid")
        occurrences.append(record)
    return occurrences


def _forecast_items(
    account: Account, occurrences: list[dict[str, Any]], start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expenses, income, seen = [], [], set()
    for record in occurrences:
        if not isinstance(record, dict):
            raise ForecastDataError("Scheduled occurrence is malformed")
        identifier = record.get("occurrence_id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ForecastDataError("Scheduled occurrence IDs must be unique")
        seen.add(identifier)
        kind, state = record.get("kind"), record.get("state")
        states = {
            "expense": {"scheduled", "paid", "cancelled"},
            "income": {"confirmed_demo", "unconfirmed", "received", "cancelled"},
        }
        if (
            not isinstance(kind, str)
            or not isinstance(state, str)
            or kind not in states
            or state not in states[kind]
        ):
            raise ForecastDataError("Scheduled occurrence kind or payment state is invalid")
        if (
            record.get("account_id") != account.account_id
            or record.get("currency") != account.currency
        ):
            raise ForecastDataError("Forecast must use one account and currency")
        if record.get("synthetic") is not True or not isinstance(record.get("label"), str):
            raise ForecastDataError("Demo occurrences must be labelled as fictional")
        amount, due = _forecast_money(record.get("amount")), _forecast_time(record.get("due_at"))
        if start <= due < end and state in {"scheduled", "confirmed_demo"}:
            item = {
                "occurrence_id": identifier,
                "label": record["label"],
                "kind": kind,
                "amount": f"{amount:.2f}",
                "currency": account.currency,
                "due_at": due.isoformat(),
                "synthetic": True,
            }
            (expenses if kind == "expense" else income).append(item)
    for items in (expenses, income):
        items.sort(key=lambda item: (_forecast_time(item["due_at"]), item["occurrence_id"]))
    return expenses, income


def calculate_cashflow_forecast(
    *,
    account: Account,
    occurrences: list[dict[str, Any]],
    as_of: datetime,
    horizon_days: int,
    proposed_amount: Decimal,
) -> dict[str, Any]:
    """Estimate cashflow from resolved occurrences without performing any reads or writes.

    The horizon is explicit, with an inclusive start and exclusive end. Input must
    contain only this account's occurrences; missing schedules fail rather than imply
    no expenses. Negative projections are advisory and never reject transactions.
    """
    start = _forecast_time(as_of)
    if type(horizon_days) is not int or horizon_days <= 0 or not isinstance(occurrences, list):
        raise ForecastDataError("Forecast needs a positive horizon and an explicit schedule")
    try:
        end = start + timedelta(days=horizon_days)
    except OverflowError:
        raise ForecastDataError("Forecast horizon is outside supported dates") from None
    balance = _forecast_money(account.balance)
    if not isinstance(proposed_amount, Decimal):
        raise ForecastDataError("Hypothetical deduction must be a Decimal")
    deduction = _forecast_money(proposed_amount)
    expenses, income = _forecast_items(account, occurrences, start, end)
    amounts = [Decimal(item["amount"]) for item in expenses + income]
    # Keep arithmetic exact even if the caller changed the ambient Decimal precision.
    with localcontext() as context:
        context.prec = (
            max(
                max(value.adjusted() + 3, len(value.as_tuple().digits))
                for value in [balance, deduction] + amounts
            )
            + len(str(len(amounts) + 1))
            + 4
        )
        expense_total = sum((Decimal(item["amount"]) for item in expenses), Decimal("0.00"))
        income_total = sum((Decimal(item["amount"]) for item in income), Decimal("0.00"))
        before = balance + income_total - expense_total
        after = before - deduction
        return {
            "estimate": True,
            "synthetic": True,
            "account_id": account.account_id,
            "currency": account.currency,
            "as_of": start.isoformat(),
            "window_end": end.isoformat(),
            "current_balance": f"{balance:.2f}",
            "scheduled_income_total": f"{income_total:.2f}",
            "commitment_total": f"{expense_total:.2f}",
            "proposed_transaction_amount": f"{deduction:.2f}",
            "projected_balance_before": f"{before:.2f}",
            "projected_balance_after": f"{after:.2f}",
            "transaction_difference": f"{Decimal('0.00') - deduction:.2f}",
            "projected_shortfall": f"{max(-after, Decimal('0.00')):.2f}",
            "commitments": expenses,
            "scheduled_income": income,
            "assumptions": [
                "Includes only recorded bills and scheduled occurrences; other expenses may occur.",
                "Income is a fictional demo assumption, not externally verified.",
                "Monthly labels do not generate additional occurrences automatically.",
                "Estimated balances are not guarantees or transaction authorisation.",
                "Closing balance only; funds may be needed before scheduled income arrives.",
            ],
        }


def get_cashflow_forecast(
    user_id: str,
    account_id: str,
    *,
    as_of: datetime,
    horizon_days: int,
    proposed_amount: Decimal,
    balance_snapshot: Account | None = None,
) -> dict[str, Any]:
    """Read the demo schedule and estimate one account's hypothetical transfer impact.

    J-08 callers can supply a trusted current Account snapshot matching this account
    and currency. No ledger import, balance mutation, history replay or AI call occurs.
    """
    users = _load_database()["users"]
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found.")
    user = users[user_id]
    accounts = {
        record["account_id"]: Account.model_validate(
            {field: record[field] for field in Account.model_fields}
        )
        for record in user["accounts"]
    }
    if account_id not in accounts:
        raise HTTPException(status_code=404, detail="Account not found.")
    account = accounts[account_id]
    if balance_snapshot is not None:
        if (
            balance_snapshot.account_id != account_id
            or balance_snapshot.currency != account.currency
        ):
            raise ForecastDataError("Current balance snapshot does not match the selected account")
        account = balance_snapshot
    try:
        occurrences = _cashflow_occurrences(user, accounts)
    except (KeyError, TypeError):
        raise ForecastDataError("Scheduled cashflow data is malformed") from None
    return calculate_cashflow_forecast(
        account=account,
        occurrences=[item for item in occurrences if item["account_id"] == account_id],
        as_of=as_of,
        horizon_days=horizon_days,
        proposed_amount=proposed_amount,
    )


def get_account_insights(user_id: str) -> dict[str, Any]:
    """Read synthetic account history and the next 30 days of scheduled expenses.

    History is a seed sample, not a complete statement. Never infer categories.
    Keep account ownership, dates and currencies intact; expose only allowlisted fields.
    """
    from zoneinfo import ZoneInfo

    public = get_accounts(user_id)
    user = _load_database()["users"][user_id]
    accounts = {a.account_id: a for a in public.accounts}
    now = datetime.now(UTC).astimezone(ZoneInfo("Asia/Singapore"))
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = (start - timedelta(days=1)).replace(day=1)
    history = user.get("transaction_history")
    transactions = None
    if isinstance(history, list):
        transactions = []
        seen = set()
        for item in history:
            account = accounts.get(item.get("account_id"))
            if account is None or item.get("currency") != account.currency:
                raise ForecastDataError("History account or currency is invalid")
            identifier = item["transaction_id"]
            if identifier in seen:
                raise ForecastDataError("Duplicate history record")
            seen.add(identifier)
            amount = _forecast_money(item["amount"])
            when = _forecast_time(item["time"])
            if item.get("direction") not in {"debit", "credit"}:
                raise ForecastDataError("Invalid history direction")
            if when > now or item.get("status") != "completed":
                continue
            transactions.append(
                {
                    "id": identifier,
                    "account_id": account.account_id,
                    "amount": f"{amount:.2f}",
                    "currency": account.currency,
                    "direction": item["direction"],
                    "time": when.isoformat(),
                    "description": str(item["description"]),
                    "category": "Uncategorised",
                }
            )
        transactions.sort(key=lambda t: t["time"], reverse=True)
    forecasts = []
    for account in public.accounts:
        try:
            forecasts.append(
                get_cashflow_forecast(
                    user_id,
                    account.account_id,
                    as_of=now,
                    horizon_days=30,
                    proposed_amount=Decimal("0.00"),
                )
            )
        except ForecastDataError:
            forecasts.append({"account_id": account.account_id, "unavailable": True})
    spending = []
    for currency in sorted({a.currency for a in public.accounts}):
        periods = []
        for lower, upper in [(previous, start), (start, now)]:
            records = [
                t
                for t in transactions or []
                if t["currency"] == currency
                and t["direction"] == "debit"
                and lower <= _forecast_time(t["time"]) < upper
            ]
            total = sum((Decimal(t["amount"]) for t in records), Decimal("0.00"))
            periods.append(
                {
                    "from": lower.isoformat(),
                    "through": upper.isoformat(),
                    "total": f"{total:.2f}",
                    "count": len(records),
                }
            )
        spending.append({"currency": currency, "previous": periods[0], "current": periods[1]})
    return {
        "source": "synthetic",
        "as_of": now.isoformat(),
        "accounts": public.model_dump()["accounts"],
        "transactions": transactions,
        "forecasts": forecasts,
        "spending": spending if transactions is not None else None,
    }
