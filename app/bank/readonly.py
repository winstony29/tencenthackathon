"""Read-only views of accounts, payees, billers and quotes; the only bank file the AI may import.

Owner: Jayanth
Diagram box: User data
Contract: docs/CONTRACTS.md §3.3 (GET /api/accounts, GET /api/quote, readonly functions)
"""

import hashlib
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import TypeAdapter

from app import audit
from app.config import MAX_ACTIONS_PER_DRAFT
from app.contracts import (
    Account,
    AccountsResponse,
    Action,
    Biller,
    ExecutedResponse,
    FxResponse,
    Holding,
    Payee,
    QuoteResponse,
)


class LedgerStorageError(RuntimeError):
    """Runtime state is unavailable; never replace it with seed balances."""


def _ledger_digest(state: dict[str, Any]) -> str:
    payload = {key: value for key, value in state.items() if key != "checksum"}
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _ledger_path(storage_path: Path | None) -> Path:
    return storage_path or Path(__file__).resolve().parents[2] / "var" / "ledger.json"


def _validate_ledger_users(users: dict[str, Any]) -> None:
    all_accounts = set()
    for user_id, user in users.items():
        if user.get("user_id") != user_id:
            raise ValueError("Invalid runtime user")
        collections = {
            "accounts": (Account, "account_id"),
            "billers": (Biller, "biller_id"),
            "payees": (Payee, "payee_id"),
        }
        for group, (model, identifier) in collections.items():
            records = user[group]
            if not isinstance(records, list) or len({r[identifier] for r in records}) != len(
                records
            ):
                raise ValueError("Invalid runtime records")
            for record in records:
                model.model_validate({field: record[field] for field in model.model_fields})
                if group in {"accounts", "billers"}:
                    _forecast_money(record["balance" if group == "accounts" else "amount_due"])
        accounts = {record["account_id"] for record in user["accounts"]}
        if accounts & all_accounts:
            raise ValueError("Duplicate account ownership")
        all_accounts.update(accounts)
        for bill in user["billers"]:
            if bill.get("payment_state") not in {"outstanding", "paid"} or (
                (bill["payment_state"] == "paid") != (_forecast_money(bill["amount_due"]) == 0)
            ):
                raise ValueError("Invalid bill settlement")
        holdings = set()
        for holding in user["holdings"]:
            key = (holding["account_id"], holding["ticker"])
            if key in holdings or key[0] not in accounts:
                raise ValueError("Invalid holding reference")
            holdings.add(key)
            if type(holding["quantity"]) is not int or holding["quantity"] < 0:
                raise ValueError("Invalid runtime holding")


def _validate_ledger_history(state: dict[str, Any]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, transaction in enumerate(state["transactions"], start=1):
        if transaction["reference"] != f"tx_{index:08d}":
            raise ValueError("Invalid transaction sequence")
        _forecast_time(transaction["completed_at"])
        TypeAdapter(Action).validate_python(transaction["action"])
        grouped.setdefault(transaction["execution_id"], []).append(transaction)
    if set(grouped) != set(state["executions"]):
        raise ValueError("Missing execution or transaction")
    expected_events = []
    for identifier, execution in state["executions"].items():
        if not re.fullmatch(r"ex_[A-Za-z0-9_-]{1,80}", identifier) or not re.fullmatch(
            r"[0-9a-f]{64}", execution["fingerprint"]
        ):
            raise ValueError("Invalid execution identity")
        response = ExecutedResponse.model_validate(execution["response"])
        records = grouped[identifier]
        if any(
            sum(record["action"]["type"] == kind for record in records) > 1
            for kind in {"transfer", "bill_payment", "stock_order"}
        ):
            raise ValueError("Duplicate customer action types")
        if not 1 <= len(records) <= MAX_ACTIONS_PER_DRAFT or len(response.results) != len(records):
            raise ValueError("Invalid execution results")
        for index, (result, record) in enumerate(zip(response.results, records, strict=True)):
            if (
                result.action_index != index
                or record["action_index"] != index
                or result.reference != record["reference"]
                or record["user_id"] not in state["users"]
            ):
                raise ValueError("Invalid execution reference")
        events = execution["audit_events"]
        correlation = "tx_" + hashlib.sha256(identifier.encode()).hexdigest()
        if events[0] != {
            "event": "transaction_executed",
            "data": {"transaction_id": correlation, "count": len(records)},
        } or len(events) != 1 + sum(r["action"]["type"] == "fx_conversion" for r in records):
            raise ValueError("Invalid execution audit events")
        for event in events:
            audit._safe_data(event["data"])
            if event["event"] not in {"transaction_executed", "fx_converted"}:
                raise ValueError("Invalid execution event")
        expected_events.extend(events)
    expected_events.extend(_validate_payment_controls(state))
    if any(event not in expected_events for event in state["outbox"]):
        raise ValueError("Unrecognised outbox event")
    if len({json.dumps(event, sort_keys=True) for event in state["outbox"]}) != len(
        state["outbox"]
    ):
        raise ValueError("Duplicate outbox event")


def _validate_payment_controls(state: dict[str, Any]) -> list[dict[str, Any]]:
    # Legacy state can lack controls: the writer treats that as paused. Reads never
    # migrate storage, repair controls or infer permission from an earlier audit.
    if "payment_control" in state:
        control = state["payment_control"]
        if (
            not isinstance(control, dict)
            or set(control) != {"paused"}
            or type(control["paused"]) is not bool
        ):
            raise ValueError("Invalid payment control state")
    events = state.get("security_events", [])
    if not isinstance(events, list):
        raise ValueError("Invalid security events")
    identities = set()
    for event in events:
        if not isinstance(event, dict) or set(event) != {"event", "data"}:
            raise ValueError("Invalid security event")
        data = event["data"]
        audit._safe_data(data)
        if not re.fullmatch(r"tx_[0-9a-f]{64}", data.get("transaction_id", "")):
            raise ValueError("Invalid security event identity")
        if event["event"] in {"kill_switch_activated", "kill_switch_deactivated"}:
            if set(data) != {"transaction_id", "reason"} or data["reason"] != "operator_control":
                raise ValueError("Invalid operator event")
        elif event["event"] in {"risk_rejected", "step_up_required"}:
            if (
                set(data) != {"transaction_id", "reason", "count"}
                or data["reason"]
                not in {
                    "payments_paused",
                    "policy_missing",
                    "policy_invalid",
                    "policy_unsupported",
                    "state_invalid",
                    "clock_invalid",
                    "draft_invalid",
                    "execution_conflict",
                    "unknown_user",
                    "kyc_incomplete",
                    "account_not_owned",
                    "account_inactive",
                    "account_type_invalid",
                    "account_label_mismatch",
                    "currency_mismatch",
                    "invalid_amount",
                    "per_transfer_limit_exceeded",
                    "daily_limit_exceeded",
                    "velocity_exceeded",
                    "step_up_required",
                }
                or (
                    (event["event"] == "step_up_required") != (data["reason"] == "step_up_required")
                )
                or not 1 <= data["count"] <= MAX_ACTIONS_PER_DRAFT
            ):
                raise ValueError("Invalid risk event")
        else:
            raise ValueError("Unsupported security event")
        # Control retries share an identity even when the requested direction changes.
        identity = data["transaction_id"]
        if identity in identities:
            raise ValueError("Duplicate security operation")
        identities.add(identity)
    return events


def _read_ledger_state(path: Path) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate runtime key")
            result[key] = value
        return result

    try:
        payload = path.read_text(encoding="utf-8")
        if not payload.endswith("\n"):
            raise ValueError("Incomplete runtime file")
        state = json.loads(payload, object_pairs_hook=unique_object)
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != "1.0"
            or state.get("checksum") != _ledger_digest(state)
            or not isinstance(state.get("users"), dict)
            or not isinstance(state.get("executions"), dict)
            or not isinstance(state.get("transactions"), list)
            or not isinstance(state.get("outbox"), list)
            or type(state.get("next_reference")) is not int
            or state["next_reference"] != len(state["transactions"]) + 1
        ):
            raise ValueError("Invalid runtime state")
        _validate_ledger_users(state["users"])
        _validate_ledger_history(state)
        return state
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, audit.AuditError):
        raise LedgerStorageError("Banking runtime storage is missing or invalid") from None


def get_ledger_snapshot(user_id: str, *, storage_path: Path | None = None) -> dict[str, Any] | None:
    """Return a detached internal user snapshot, including current bills and balances.

    None means neither state nor marker exists; this cannot prove a first installation
    rather than deletion of all evidence. Public reads fail closed in that case.
    Atomic replacement lets readers see a complete old or new state.
    This reader does not import the ledger or return its execution/outbox metadata.
    """
    path = _ledger_path(storage_path)
    try:
        if not path.exists() and not path.with_suffix(".lock").exists():
            return None
    except OSError:
        raise LedgerStorageError("Cannot inspect banking runtime storage") from None
    users = _read_ledger_state(path)["users"]
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found.")
    return users[user_id]


def _current_user(user_id: str) -> dict[str, Any]:
    try:
        user = get_ledger_snapshot(user_id)
        if user is None:
            raise LedgerStorageError("No established ledger state")
        return user
    except LedgerStorageError:
        raise HTTPException(
            status_code=503, detail="Banking state unavailable; operator review required."
        ) from None


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

    Committed runtime state is authoritative. Missing or invalid state fails with
    a sanitised service-unavailable error, never substituted opening balances.
    """
    user = _current_user(user_id)
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
    runtime_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read current balances and obligations, then estimate a hypothetical deduction.

    Default reads use one committed user snapshot, including current bill states.
    Trusted internal callers can explicitly supply snapshots for isolated calculations;
    they are not public request fields. No initialisation, writes or AI calls occur.
    """
    user = _current_user(user_id) if runtime_snapshot is None else runtime_snapshot
    if runtime_snapshot is not None:
        if runtime_snapshot.get("user_id") != user_id:
            raise ForecastDataError("Runtime snapshot does not match the selected user")
        user = runtime_snapshot
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
