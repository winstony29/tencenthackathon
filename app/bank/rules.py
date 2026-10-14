"""Risk and KYC rules: limits, velocity, KYC and the extra-check threshold.

Owner: Jayanth
Diagram box: Risk & KYC rules
Contract: none; internal evaluator and trusted execution-policy interface

This evaluator reads supplied trusted snapshots only. It does not authenticate,
verify OTPs, reserve funds, execute, log or persist anything. An allow decision is
not authority to move money. The ledger assesses current state under its lock
before every new financial commit; this module never imports that writer.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.audit import AuditError
from app.bank import readonly
from app.contracts import Draft, Money


class TransferPolicy(BaseModel):
    """Explicit SGD-only demo policy; every field is required, with no live defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    currency: Literal["SGD"]
    per_transfer_max: Money = Field(gt=0)
    daily_transfer_max: Money = Field(gt=0)
    step_up_above: Money = Field(gt=0)
    velocity_max_transfers: int = Field(strict=True, gt=0)
    velocity_window_minutes: int = Field(strict=True, gt=0)
    daily_timezone: Literal["Asia/Singapore"]

    @model_validator(mode="after")
    def _consistent_limits(self) -> "TransferPolicy":
        if not self.step_up_above < self.per_transfer_max <= self.daily_transfer_max:
            raise ValueError("Require step-up threshold < transfer maximum <= daily maximum")
        try:
            timedelta(minutes=self.velocity_window_minutes)
        except OverflowError:
            raise ValueError("Velocity window is outside supported dates") from None
        return self


def execution_policy() -> TransferPolicy | None:
    """Read the supervised customer's committed policy or explicit operator override."""
    from app import config

    if not config.BANK_EXECUTION_ENABLED:
        return None
    try:
        if config.BANK_TRANSFER_POLICY:
            return TransferPolicy.model_validate_json(config.BANK_TRANSFER_POLICY)
        user = readonly._current_user(config.BANK_SUPERVISED_USER)
        return TransferPolicy.model_validate(user.get("transfer_policy"))
    except (ValueError, TypeError, readonly.HTTPException):
        return None


RiskReason = Literal[
    "allowed",
    "already_committed",
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
]


class RiskDecision(BaseModel):
    """Controlled internal outcome, without sensitive input data or authorisation proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["allow", "reject", "needs_extra_check"]
    reason: RiskReason


def _decision(reason: RiskReason) -> RiskDecision:
    status = "reject"
    if reason in {"allowed", "already_committed"}:
        status = "allow"
    elif reason == "step_up_required":
        status = "needs_extra_check"
    return RiskDecision(status=status, reason=reason)


def _fingerprint(draft: Draft) -> str:
    return hashlib.sha256(
        json.dumps(
            draft.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _validated_state(state: dict[str, Any]) -> None:
    # These are pure validators, not the readonly module's filesystem reader.
    if (
        not isinstance(state, dict)
        or not isinstance(state.get("users"), dict)
        or not isinstance(state.get("transactions"), list)
        or not isinstance(state.get("executions"), dict)
        or not isinstance(state.get("outbox"), list)
    ):
        raise ValueError("Required committed-state collections are unavailable")
    readonly._validate_ledger_users(state["users"])
    readonly._validate_ledger_history(state)
    for record in state["transactions"]:
        if record.get("status", "completed") != "completed":
            raise ValueError("Committed collection contains an uncommitted record")
        if (
            record["action"]["type"] == "transfer"
            and readonly._forecast_money(record["action"]["amount"]) <= 0
        ):
            raise ValueError("Committed transfer has an invalid amount")


def _eligibility(user: dict[str, Any], action: Any) -> RiskReason | None:
    kyc = user.get("kyc")
    if not isinstance(kyc, dict) or kyc.get("status") != "complete":
        return "kyc_incomplete"
    accounts = [
        record for record in user["accounts"] if record["account_id"] == action.from_account_id
    ]
    if len(accounts) != 1:
        return "account_not_owned"
    account = accounts[0]
    if account.get("status") != "active":
        return "account_inactive"
    if account["type"] not in {"savings", "current"}:
        return "account_type_invalid"
    if account["currency"] != action.currency:
        return "currency_mismatch"
    if account["label"] != action.from_account_label:
        return "account_label_mismatch"
    return None


def _transfer_usage(
    state: dict[str, Any],
    user_id: str,
    now: datetime,
    policy: TransferPolicy,
) -> tuple[list[Decimal], int]:
    today = now.astimezone(ZoneInfo(policy.daily_timezone)).date()
    start = now - timedelta(minutes=policy.velocity_window_minutes)
    amounts, recent = [], 0
    for record in state["transactions"]:
        if record["user_id"] != user_id or record["action"]["type"] != "transfer":
            continue
        completed = readonly._forecast_time(record["completed_at"])
        if start < completed <= now:
            # Frequency is per customer, including any earlier transfers in other currencies.
            recent += 1
        if (
            record["action"]["currency"] == policy.currency
            and completed.astimezone(ZoneInfo(policy.daily_timezone)).date() == today
        ):
            amounts.append(readonly._forecast_money(record["action"]["amount"]))
    return amounts, recent


def _limits(
    amount: Decimal, spent: list[Decimal], recent: int, policy: TransferPolicy
) -> RiskReason:
    if amount <= 0:
        return "invalid_amount"
    if amount > policy.per_transfer_max:
        return "per_transfer_limit_exceeded"
    # Keep cents exact even when the caller's Decimal context has very low precision.
    with localcontext() as context:
        context.prec = (
            max(
                max(value.adjusted() + 3, len(value.as_tuple().digits))
                for value in [amount] + spent
            )
            + len(str(len(spent) + 1))
            + 1
        )
        if sum(spent, Decimal("0.00")) + amount > policy.daily_transfer_max:
            return "daily_limit_exceeded"
    if recent >= policy.velocity_max_transfers:
        return "velocity_exceeded"
    if amount > policy.step_up_above:
        return "step_up_required"
    return "allowed"


def evaluate_risk(
    draft: Draft,
    *,
    execution_id: str,
    policy: TransferPolicy | None,
    state: dict[str, Any],
    now: datetime,
) -> RiskDecision:
    """Assess a single SGD transfer using explicit policy and trusted committed state.

    now must come from the backend, never draft.created_at. Daily usage is per customer
    across accounts; the rolling interval is (now - window, now]. Exact committed
    retries report already_committed without adding usage, but grant no execution
    authority. Unsupported actions/currencies and invalid required inputs fail closed.
    Transfers needing step-up remain needs_extra_check: Phase 1 accepts no proof/OTP.
    The caller must supply a complete snapshot; this function cannot detect omitted
    or stale genuine records and provides no concurrency protection on its own.
    """
    if policy is None:
        return _decision("policy_missing")
    try:
        policy = TransferPolicy.model_validate(policy.model_dump(mode="python", warnings=False))
    except (ValueError, TypeError, AttributeError):
        return _decision("policy_invalid")
    try:
        if not isinstance(now, datetime):
            raise ValueError("Backend time must be a datetime")
        now = readonly._forecast_time(now).astimezone(UTC)
        # Rolling windows measure elapsed time, irrespective of the caller's timezone.
        now - timedelta(minutes=policy.velocity_window_minutes)
    except (ValueError, TypeError, OverflowError):
        return _decision("clock_invalid")
    try:
        if not isinstance(execution_id, str) or not re.fullmatch(
            r"ex_[A-Za-z0-9_-]{1,80}", execution_id
        ):
            raise ValueError("Invalid execution identity")
        draft = Draft.model_validate(draft.model_dump(mode="python", warnings=False))
        if len(draft.actions) != 1 or draft.actions[0].type != "transfer":
            return _decision("policy_unsupported")
        if draft.actions[0].currency != policy.currency:
            return _decision("policy_unsupported")
        fingerprint = _fingerprint(draft)
    except (ValueError, TypeError, AttributeError):
        return _decision("draft_invalid")
    try:
        _validated_state(state)
        user = state["users"].get(draft.user_id)
        if user is None:
            return _decision("unknown_user")
        if any(
            record["user_id"] == draft.user_id
            and readonly._forecast_time(record["completed_at"]) > now
            for record in state["transactions"]
        ):
            return _decision("clock_invalid")
        prior = state["executions"].get(execution_id)
        if prior is not None:
            if prior["fingerprint"] == fingerprint and any(
                record["user_id"] != draft.user_id
                or record["action"] != draft.actions[0].model_dump(mode="json")
                for record in state["transactions"]
                if record["execution_id"] == execution_id
            ):
                return _decision("state_invalid")
            return _decision(
                "already_committed" if prior["fingerprint"] == fingerprint else "execution_conflict"
            )
        failure = _eligibility(user, draft.actions[0])
        if failure is not None:
            return _decision(failure)
        spent, recent = _transfer_usage(state, draft.user_id, now, policy)
        return _decision(_limits(draft.actions[0].amount, spent, recent, policy))
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
        OverflowError,
        AuditError,
    ):
        return _decision("state_invalid")
