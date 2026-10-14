"""Mock ledger: balances and holdings; the only code that moves money.

Owner: Jayanth
Diagram box: Mock ledger
Contract: none; only app/bank/gateway.py may import it

Gateway interface: execute_batch(draft, execution_id). J-06 must authorise the exact
draft and supply the identifier; this function performs no authentication. Identical
retries return the original ExecutedResponse. Changed payloads cannot reuse an ID.
Every new execution requires a configured backend policy, a risk allow decision
and unpaused runtime controls under the same lock. Step-up remains uncommitted
until J-06 supplies securely bound verification. Unconfigured action types are closed.

Atomicity covers this local simulated ledger, not real bank/exchange settlement.
Mock orders fill immediately at the stored quote (no fees); limits must permit that
price. FX uses the stored directed rate and rounds received money down to cents.
Bill payments cannot exceed the outstanding obligation. Historical seed entries
describe an opening snapshot and are never replayed to calculate balances.
"""

import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from pydantic import ValidationError

from app import audit, config, evidence
from app.bank import readonly, rules
from app.contracts import Draft, ExecutedResponse, FxResponse, QuoteResponse

_LOCK = threading.RLock()


class LedgerRejected(ValueError):
    """A deterministic invariant failed; reason is an internal gateway/audit code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class LedgerAuditPending(RuntimeError):
    """Audit delivery failed. Money may be committed: retry only with the SAME ID."""


class LedgerRiskRejected(LedgerRejected):
    """No financial commit; J-06 must map status and reason to its public response."""

    def __init__(self, reason: str, status: str = "reject") -> None:
        self.status = status
        super().__init__(reason)


@contextmanager
def _locked(path: Path) -> Iterator[bool]:
    # Lock a stable inode, not the JSON inode that atomic replacement changes.
    with _LOCK:
        try:
            parent_existed = path.parent.exists()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not parent_existed:
                _sync_directory(path.parent.parent)
            lock_path = path.with_suffix(".lock")
            with lock_path.open("a+b") as handle:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    # The durable marker is written before initialisation. Missing state
                    # after this point requires investigation, even after a first-write crash.
                    handle.seek(0)
                    established = bool(handle.read())
                    if not established:
                        handle.write(b"ledger-initialisation-started\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                        _sync_directory(path.parent)
                    yield established
                finally:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            raise readonly.LedgerStorageError(
                "Cannot lock or synchronise banking storage"
            ) from None


def _sync_directory(path: Path) -> None:
    # Windows flushes file contents; POSIX additionally syncs directory entries.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _save_state(path: Path, state: dict[str, Any]) -> None:
    temporary = None
    try:
        state["checksum"] = readonly._ledger_digest(state)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".ledger-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(state, handle, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except (OSError, ValueError, TypeError):
        # Replacement may already have happened. Never advise a caller to use a new ID.
        raise readonly.LedgerStorageError(
            "Banking write failed; reconcile storage and retry the same execution ID"
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load_state(path: Path, established: bool, audit_path: Path | None) -> dict[str, Any]:
    if path.exists() or established:
        return readonly._read_ledger_state(path)
    try:
        log = audit.read_events(sys.maxsize, storage_path=audit_path)
        if not log.chain_valid:
            raise audit.AuditError("Cannot initialise against an invalid audit chain")
        if any(
            entry.actor == "bank"
            and entry.event
            in {
                "transaction_executed",
                "fx_converted",
                "risk_rejected",
                "step_up_required",
                "kill_switch_activated",
                "kill_switch_deactivated",
            }
            for entry in log.entries
        ):
            raise readonly.LedgerStorageError("Execution audits exist but ledger state is missing")
    except audit.AuditError:
        raise LedgerAuditPending("Audit storage must be reconciled before initialisation") from None
    try:
        state = {
            "schema_version": "1.0",
            "users": copy.deepcopy(readonly._load_database()["users"]),
            "transactions": [],
            "executions": {},
            "outbox": [],
            "next_reference": 1,
            "payment_control": {"paused": True},
            "security_events": [],
        }
        readonly._validate_ledger_users(state["users"])
        _save_state(path, state)
        return readonly._read_ledger_state(path)
    except (ValueError, KeyError, TypeError, OSError):
        raise readonly.LedgerStorageError("Cannot initialise banking storage") from None


def _event_data(execution_id: str, draft: Draft) -> dict[str, Any]:
    # Correlation without transcripts, customer IDs, recipient names or financial values.
    return {
        "transaction_id": "tx_" + hashlib.sha256(execution_id.encode()).hexdigest(),
        "count": len(draft.actions),
    }


def _execution_events(execution_id: str, draft: Draft) -> list[dict[str, Any]]:
    events = [{"event": "transaction_executed", "data": _event_data(execution_id, draft)}]
    for index, action in enumerate(draft.actions):
        if action.type == "fx_conversion":
            fields = action.model_dump(mode="json")
            events.append(
                {
                    "event": "fx_converted",
                    "data": {
                        "transaction_id": events[0]["data"]["transaction_id"],
                        "draft_id": draft.draft_id,
                        "action_index": index,
                        **{
                            key: fields[key]
                            for key in (
                                "sell_amount",
                                "sell_currency",
                                "buy_amount",
                                "buy_currency",
                                "rate",
                            )
                        },
                    },
                }
            )
    for event in events:
        try:
            audit._safe_data(event["data"])
        except audit.AuditError:
            raise LedgerRejected("invalid_audit_metadata") from None
    return events


def _event_key(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def _delivered_events(entries: list[Any]) -> set[str]:
    identities = {}
    for entry in entries:
        if entry.actor != "bank" or entry.event not in {
            "transaction_executed",
            "fx_converted",
            "risk_rejected",
            "step_up_required",
            "kill_switch_activated",
            "kill_switch_deactivated",
        }:
            continue
        identity = (entry.event, entry.data["transaction_id"], entry.data.get("action_index"))
        payload = _event_key({"event": entry.event, "data": entry.data})
        if identity in identities and identities[identity] != payload:
            raise audit.AuditError("Conflicting execution audit evidence")
        identities[identity] = payload
    return set(identities.values())


def _reconcile(state: dict[str, Any], path: Path, audit_path: Path | None) -> None:
    try:
        log = audit.read_events(sys.maxsize, storage_path=audit_path)
        if not log.chain_valid:
            raise audit.AuditError("Audit chain is invalid")
        delivered = _delivered_events(log.entries)
        pending = {_event_key(event) for event in state["outbox"]}
        expected = {
            _event_key(event)
            for execution in state["executions"].values()
            for event in execution["audit_events"]
        }
        expected.update(_event_key(event) for event in state.get("security_events", []))
        if not expected.issubset(delivered | pending):
            raise audit.AuditError("An acknowledged execution audit record is missing")
        for event in state["outbox"]:
            # Retry delivery under the audit lock without adding another logical event.
            audit.append_event(
                "bank", event["event"], event["data"], storage_path=audit_path, idempotent=True
            )
        if config.BANK_EXECUTION_ENABLED:
            for execution_id, execution in state["executions"].items():
                records = [r for r in state["transactions"] if r["execution_id"] == execution_id]
                # Legacy execution records remain valid; newer records carry request links.
                links = execution.get("evidence_links", {})
                # Recovery may run during a different request; use original links only.
                token = evidence.context.set(None)
                try:
                    evidence.record(
                        "bank_transaction_committed",
                        {
                            "execution_id": execution_id,
                            "result": execution["response"],
                            "transactions": records,
                            "draft_digest": execution["fingerprint"],
                        },
                        idempotency_key="bank-commit:" + execution_id,
                        execution_id=execution_id,
                        draft_digest=execution["fingerprint"],
                        **links,
                    )
                finally:
                    evidence.context.reset(token)

        if state["outbox"]:
            state["outbox"] = []
            _save_state(path, state)
    except (audit.AuditError, OSError):
        raise LedgerAuditPending(
            "Audit reconciliation required; retry only with the same execution ID"
        ) from None
    except (KeyError, TypeError, AttributeError):
        raise readonly.LedgerStorageError("Invalid execution or outbox records") from None


def reconcile_audit(*, storage_path: Path | None = None, audit_path: Path | None = None) -> None:
    """Recover pending audit events under the ledger lock, without moving money.

    Refuses unreadable or tampered audit logs. A missing acknowledged audit record
    needs operator investigation. Ledger and audit files are not committed together.
    """
    path = readonly._ledger_path(storage_path)
    with _locked(path) as established:
        _reconcile(_load_state(path, established, audit_path), path, audit_path)


def _security_event(
    state: dict[str, Any], event: dict[str, Any], path: Path, audit_path: Path | None
) -> None:
    events = state.setdefault("security_events", [])
    prior = next(
        (e for e in events if e["data"]["transaction_id"] == event["data"]["transaction_id"]), None
    )
    if prior is not None:
        _require(prior == event, "control_operation_conflict")
    else:
        audit._safe_data(event["data"])
        events.append(event)
        state["outbox"].append(event)
        _save_state(path, state)
    _reconcile(state, path, audit_path)


def set_payment_pause(
    paused: bool,
    *,
    operation_id: str,
    storage_path: Path | None = None,
    audit_path: Path | None = None,
) -> None:
    """Restricted internal operator control; never expose it through an API or AI.

    Run only inside an access-controlled backend/operator process. Python access is
    not operator authentication: deployment must restrict OS/process/file access.
    Initialise a genuinely fresh demo with reconcile_audit(), review current state,
    then (only AFTER approved backend policy configuration) call this with False and
    a unique op_ identifier. With no configured policy, enabling is refused. Use True
    to pause; it cannot reverse committed payments. Legacy missing controls mean paused
    and this operation adds controls without resetting any balances or history.

    A write/audit failure may already have persisted the control: retry the SAME ID
    and direction. Identical retries recover evidence without repeating old changes.
    Activation persists even when audit delivery is unavailable; resume needs a healthy
    audit chain. Lock order is always ledger then audit, never the reverse.
    """
    if (
        type(paused) is not bool
        or not isinstance(operation_id, str)
        or not re.fullmatch(r"op_[A-Za-z0-9_-]{1,80}", operation_id)
    ):
        raise LedgerRejected("invalid_operator_control")
    event = {
        "event": "kill_switch_activated" if paused else "kill_switch_deactivated",
        "data": {
            "transaction_id": "tx_"
            + hashlib.sha256(("operator:" + operation_id).encode()).hexdigest(),
            "reason": "operator_control",
        },
    }
    path = readonly._ledger_path(storage_path)
    with _locked(path) as established:
        state = _load_state(path, established, audit_path)
        prior = next(
            (
                e
                for e in state.get("security_events", [])
                if e["data"]["transaction_id"] == event["data"]["transaction_id"]
            ),
            None,
        )
        if prior is not None:
            _require(prior == event, "control_operation_conflict")
            _reconcile(state, path, audit_path)
            return
        if not paused:
            _reconcile(state, path, audit_path)
            policy = rules.execution_policy()
            if policy is None:
                raise LedgerRejected("policy_missing")
            try:
                rules.TransferPolicy.model_validate(
                    policy.model_dump(mode="python", warnings=False)
                )
            except (ValueError, TypeError, AttributeError):
                raise LedgerRejected("policy_invalid") from None
        state["payment_control"] = {"paused": paused}
        _security_event(state, event, path, audit_path)


def _enforce_risk(
    state: dict[str, Any],
    draft: Draft,
    execution_id: str,
    fingerprint: str,
    now: datetime,
    path: Path,
    audit_path: Path | None,
) -> None:
    # No request-supplied policy/proof/skip flag. This call is mandatory and remains
    # under the same lock through staging, replacement and audit acknowledgement.
    decision = rules.evaluate_risk(
        draft, execution_id=execution_id, policy=rules.execution_policy(), state=state, now=now
    )
    paused = state.get("payment_control", {"paused": True})["paused"]
    reason = "payments_paused" if paused else decision.reason
    status = "reject" if paused else decision.status
    if status == "allow":
        return
    identity = json.dumps(["risk", execution_id, fingerprint, reason], separators=(",", ":"))
    _security_event(
        state,
        {
            "event": "step_up_required" if status == "needs_extra_check" else "risk_rejected",
            "data": {
                "transaction_id": "tx_" + hashlib.sha256(identity.encode()).hexdigest(),
                "count": len(draft.actions),
                "reason": reason,
            },
        },
        path,
        audit_path,
    )
    raise LedgerRiskRejected(reason, status)


def _record(records: list[dict[str, Any]], field: str, identifier: str) -> dict[str, Any]:
    matches = [record for record in records if record.get(field) == identifier]
    if len(matches) != 1:
        raise LedgerRejected("invalid_reference")
    return matches[0]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise LedgerRejected(reason)


def _money(value: object) -> Decimal:
    try:
        return readonly._forecast_money(value)
    except ValueError:
        raise readonly.LedgerStorageError("Invalid monetary state") from None


def _change_balance(account: dict[str, Any], delta: Decimal) -> None:
    amount = _money(account["balance"]) + delta
    _require(amount >= 0, "insufficient_funds")
    account["balance"] = f"{amount:.2f}"


def _account(user: dict[str, Any], action: dict[str, Any], prefix: str = "from") -> dict[str, Any]:
    account = _record(user["accounts"], "account_id", action[f"{prefix}_account_id"])
    _require(account["label"] == action[f"{prefix}_account_label"], "label_mismatch")
    _require(account.get("status") == "active", "account_inactive")
    return account


def _pay(user: dict[str, Any], action: dict[str, Any], account: dict[str, Any]) -> None:
    _require(account["type"] in {"savings", "current"}, "invalid_account_type")
    amount = _money(action["amount"])
    _require(amount > 0, "invalid_amount")
    if action["type"] == "transfer":
        payee = _record(user["payees"], "payee_id", action["to_payee_id"])
        _require(payee["label"] == action["to_payee_label"], "label_mismatch")
        _require(payee.get("status") == "active", "recipient_inactive")
        _require(payee["currency"] == action["currency"], "invalid_currency")
    else:
        bill = _record(user["billers"], "biller_id", action["biller_id"])
        _require(bill["label"] == action["biller_label"], "label_mismatch")
        _require(bill["bill_reference"] == action["bill_reference"], "invalid_reference")
        _require(bill["currency"] == action["currency"], "invalid_currency")
        _require(bill.get("status") == "active", "biller_inactive")
        _require(bill["payment_state"] == "outstanding", "bill_settled")
        remaining = _money(bill["amount_due"]) - amount
        _require(remaining >= 0, "bill_overpayment")
        bill["amount_due"] = f"{remaining:.2f}"
        if remaining == 0:
            bill["payment_state"] = "paid"
            for occurrence in user.get("cashflow_schedule", []):
                if occurrence.get("biller_id") == bill["biller_id"]:
                    occurrence["state"] = "paid"
    _change_balance(account, -amount)


def _stock(
    user: dict[str, Any], action: dict[str, Any], account: dict[str, Any], market: dict[str, Any]
) -> dict[str, Any]:
    _require(account["type"] in {"investment", "invest"}, "invalid_account_type")
    record = market["quotes"].get(action["ticker"])
    _require(record is not None, "invalid_ticker")
    quote = QuoteResponse.model_validate({key: record[key] for key in QuoteResponse.model_fields})
    _require(quote.ticker == action["ticker"], "invalid_ticker")
    _require(quote.currency == action["currency"], "invalid_currency")
    _require(quote.company_name == action["company_name"], "label_mismatch")
    _require(quote.price > 0, "invalid_price")
    quantity = action["quantity"]
    _require(type(quantity) is int and quantity > 0, "invalid_quantity")
    if action["order_type"] == "limit":
        limit = _money(action["limit_price"])
        _require(
            quote.price <= limit if action["side"] == "buy" else quote.price >= limit,
            "order_not_executable",
        )
    holdings = user["holdings"]
    matches = [
        h
        for h in holdings
        if h["ticker"] == action["ticker"] and h["account_id"] == account["account_id"]
    ]
    if len(matches) > 1:
        raise readonly.LedgerStorageError("Duplicate holdings")
    holding = (
        matches[0]
        if matches
        else {"ticker": action["ticker"], "account_id": account["account_id"], "quantity": 0}
    )
    cost = quote.price * quantity
    if action["side"] == "sell":
        _require(holding["quantity"] >= quantity, "insufficient_holdings")
        holding["quantity"] -= quantity
        _change_balance(account, cost)
    else:
        _change_balance(account, -cost)
        holding["quantity"] += quantity
        if not matches:
            holdings.append(holding)
    return {
        "execution_price": f"{quote.price:.2f}",
        "gross_amount": f"{cost:.2f}",
        "quote_as_of": quote.as_of.isoformat(),
    }


def _fx(
    user: dict[str, Any], action: dict[str, Any], account: dict[str, Any], market: dict[str, Any]
) -> dict[str, Any]:
    destination = _account(user, action, "to")
    _require(account["account_id"] != destination["account_id"], "invalid_account")
    _require(account["currency"] == action["sell_currency"], "invalid_currency")
    _require(destination["currency"] == action["buy_currency"], "invalid_currency")
    _require(action["sell_currency"] != action["buy_currency"], "invalid_currency")
    _require(
        all(
            a["type"] in {"savings", "current", "investment", "invest"}
            for a in (account, destination)
        ),
        "invalid_account_type",
    )
    rate = market.get("fx_rates", {}).get(f"{action['sell_currency']}-{action['buy_currency']}")
    _require(rate is not None, "rate_changed")
    quote = FxResponse.model_validate(rate)
    _require(
        quote.from_ == action["sell_currency"]
        and quote.to == action["buy_currency"]
        and quote.rate == Decimal(action["rate"])
        and quote.as_of == datetime.fromisoformat(action["rate_as_of"]),
        "rate_changed",
    )
    sold = _money(action["sell_amount"])
    received = (sold * quote.rate).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    _require(sold > 0 and received > 0, "invalid_amount")
    _require(received == _money(action["buy_amount"]), "rate_changed")
    _change_balance(account, -sold)
    _change_balance(destination, received)
    return {"rounding": "ROUND_DOWN"}


def _stage(
    state: dict[str, Any], draft: Draft, execution_id: str, *, completed_at: datetime
) -> ExecutedResponse:
    # CONTRACTS §2.1 permits at most one of each customer action; FX still counts
    # toward the model's total action limit. Pydantic does not enforce this uniqueness.
    for kind in {"transfer", "bill_payment", "stock_order"}:
        _require(sum(action.type == kind for action in draft.actions) <= 1, "invalid_action_count")
    _require(draft.user_id in state["users"], "unknown_user")
    user = state["users"][draft.user_id]
    market = readonly._load_database()
    results = []
    for index, model in enumerate(draft.actions):
        action = model.model_dump(mode="json")
        account = _account(user, action)
        details = {}
        if action["type"] == "fx_conversion":
            details = _fx(user, action, account, market)
        else:
            _require(account["currency"] == action["currency"], "invalid_currency")
            if action["type"] == "stock_order":
                details = _stock(user, action, account, market)
            else:
                _pay(user, action, account)
        reference = f"tx_{state['next_reference']:08d}"
        state["next_reference"] += 1
        state["transactions"].append(
            {
                "reference": reference,
                "execution_id": execution_id,
                "user_id": draft.user_id,
                "action_index": index,
                "completed_at": completed_at.isoformat(),
                "action": action,
                "execution_details": details,
            }
        )
        results.append({"action_index": index, "reference": reference})
    return ExecutedResponse(status="executed", results=results)


def execute_batch(
    draft: Draft,
    execution_id: str,
    *,
    storage_path: Path | None = None,
    audit_path: Path | None = None,
) -> ExecutedResponse:
    """Apply an authorised draft once; only J-06's gateway should call this function.

    Returns original references for identical retries, including after a restart.
    All actions are calculated on a detached state before one atomic replacement.
    Rejects conflicts and invalid invariants without financial changes. On storage
    or audit errors the outcome may be committed: reconcile/retry using the SAME ID.
    Internal reasons require gateway mapping to the existing public error contract.
    Risk checks are mandatory against committed state and backend time under the
    ledger lock. Missing controls mean paused; no client proof or bypass is accepted.
    """
    if not isinstance(execution_id, str) or not re.fullmatch(
        r"ex_[A-Za-z0-9_-]{1,80}", execution_id
    ):
        raise LedgerRejected("invalid_execution_id")
    try:
        # Recheck even constructed or subsequently mutated Pydantic objects.
        draft = Draft.model_validate(draft.model_dump(mode="python", warnings=False))
        payload = draft.model_dump(mode="json")
        fingerprint = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()
    except (ValidationError, ValueError, AttributeError, TypeError):
        raise LedgerRejected("invalid_draft") from None
    path = readonly._ledger_path(storage_path)
    with _locked(path) as established:
        state = _load_state(path, established, audit_path)
        _reconcile(state, path, audit_path)
        prior = state["executions"].get(execution_id)
        if prior is None:
            now = datetime.now(UTC)
            _enforce_risk(state, draft, execution_id, fingerprint, now, path, audit_path)
        try:
            if prior is not None:
                _require(prior["fingerprint"] == fingerprint, "execution_conflict")
                return ExecutedResponse.model_validate(prior["response"])
            staged = copy.deepcopy(state)
            events = _execution_events(execution_id, draft)
            # Avoid ambient Decimal context rounding large balances or share costs.
            with localcontext() as context:
                context.prec = max(28, len(json.dumps(staged)) + len(json.dumps(payload)))
                response = _stage(staged, draft, execution_id, completed_at=now)
        except LedgerRejected as error:
            try:
                audit.append_event(
                    "bank",
                    "transaction_rejected",
                    {
                        **_event_data(execution_id, draft),
                        "reason": error.reason,
                    },
                    storage_path=audit_path,
                )
            except audit.AuditError:
                raise LedgerAuditPending("Rejected transaction audit delivery failed") from None
            raise
        except (ValidationError, KeyError, TypeError, ValueError):
            raise readonly.LedgerStorageError("Invalid banking or execution data") from None
        staged["executions"][execution_id] = {
            "fingerprint": fingerprint,
            "response": response.model_dump(mode="json"),
            "audit_events": events,
            "evidence_links": {
                "session_id": draft.session_id,
                "draft_id": draft.draft_id,
                "logical_id": draft.session_id,
                **(
                    {"revision": evidence.context.get()["revision"]}
                    if evidence.context.get() and "revision" in evidence.context.get()
                    else {}
                ),
            },
        }
        staged["outbox"].extend(events)
        _save_state(path, staged)
        _reconcile(staged, path, audit_path)
        return response
