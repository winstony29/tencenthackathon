"""Contracts: Pydantic models for every shape in docs/CONTRACTS.md.

Owner: Winston (shared; all three agree before any change)
Diagram box: none; the shapes that cross every arrow
Contract: docs/CONTRACTS.md v0.1, which wins if the two disagree
"""

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from app.config import MAX_ACTIONS_PER_DRAFT

NONE_OPTION_ID = "none"  # option_id the UI sends when the user picks "None of these"


def _require_money_string(value: object) -> object:
    # A float has already lost precision, so money must arrive as a string like "50.00".
    if not isinstance(value, str | Decimal):
        raise ValueError("money must be a string like '50.00'")
    return value


Money = Annotated[
    Decimal,
    BeforeValidator(_require_money_string),
    Field(ge=0, decimal_places=2),
    PlainSerializer(lambda value: f"{value:.2f}", return_type=str, when_used="json"),
]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class ContractModel(BaseModel):
    # Unknown fields are refused so nothing extra can ride along inside a signed draft.
    model_config = ConfigDict(extra="forbid")


# §2 The draft


class TransferAction(ContractModel):
    type: Literal["transfer"]
    from_account_id: str
    from_account_label: str
    to_payee_id: str
    to_payee_label: str
    amount: Money
    currency: Currency


class BillPaymentAction(ContractModel):
    type: Literal["bill_payment"]
    from_account_id: str
    from_account_label: str
    biller_id: str
    biller_label: str
    bill_reference: str
    amount: Money
    currency: Currency


class StockOrderAction(ContractModel):
    type: Literal["stock_order"]
    from_account_id: str
    from_account_label: str
    side: Literal["buy", "sell"]
    ticker: str
    company_name: str
    quantity: int = Field(gt=0)
    order_type: Literal["market", "limit"]
    limit_price: Money | None
    currency: Currency

    @model_validator(mode="after")
    def _limit_price_only_for_limit_orders(self) -> "StockOrderAction":
        if (self.order_type == "limit") != (self.limit_price is not None):
            raise ValueError("limit_price is required for limit orders and null otherwise")
        return self


Action = Annotated[
    TransferAction | BillPaymentAction | StockOrderAction, Field(discriminator="type")
]


class Evidence(ContractModel):
    question: str
    pick: str
    avg_p: float = Field(ge=0, le=1)
    spread: float = Field(ge=0, le=1)


class Draft(ContractModel):
    draft_id: str
    user_id: str
    session_id: str
    transcript: str
    created_at: AwareDatetime
    actions: list[Action] = Field(min_length=1, max_length=MAX_ACTIONS_PER_DRAFT)
    evidence: list[Evidence]


# §3.1 AI side


class TranscribeResponse(ContractModel):
    transcript: str
    confidence: float | None


class Answer(ContractModel):
    question_id: str
    option_id: str


class UnderstandRequest(ContractModel):
    user_id: str
    session_id: str
    transcript: str | None = None
    answer: Answer | None = None

    @model_validator(mode="after")
    def _exactly_one_input(self) -> "UnderstandRequest":
        if (self.transcript is None) == (self.answer is None):
            raise ValueError("send exactly one of transcript or answer")
        return self


class Option(ContractModel):
    option_id: str
    label: str


class Question(ContractModel):
    question_id: str
    text: str
    options: list[Option]
    allow_none: bool


class QuestionResponse(ContractModel):
    status: Literal["question"]
    question: Question


class DraftResponse(ContractModel):
    status: Literal["draft"]
    draft: Draft


class BlockedResponse(ContractModel):
    status: Literal["blocked"]
    reason: Literal["possible_injection"]
    message: str


class UnsupportedResponse(ContractModel):
    status: Literal["unsupported"]
    message: str


class CancelledResponse(ContractModel):
    status: Literal["cancelled"]
    message: str


UnderstandResponse = Annotated[
    QuestionResponse | DraftResponse | BlockedResponse | UnsupportedResponse | CancelledResponse,
    Field(discriminator="status"),
]


# §3.2 Validation


class ValidateRequest(ContractModel):
    draft: Draft


class MatchResponse(ContractModel):
    status: Literal["match"]
    receipt: str


class FieldMismatch(ContractModel):
    field: str
    draft_value: str
    transcript_says: str
    note: str


class MismatchResponse(ContractModel):
    status: Literal["mismatch"]
    fields: list[FieldMismatch]


ValidateResponse = Annotated[MatchResponse | MismatchResponse, Field(discriminator="status")]


# §3.3 Bank side


class Account(ContractModel):
    account_id: str
    label: str
    type: str
    currency: Currency
    balance: Money


class Payee(ContractModel):
    payee_id: str
    name: str
    label: str


class Biller(ContractModel):
    biller_id: str
    name: str
    label: str
    amount_due: Money


class Holding(ContractModel):
    ticker: str
    quantity: int = Field(ge=0)


class AccountsResponse(ContractModel):
    accounts: list[Account]
    payees: list[Payee]
    billers: list[Biller]
    holdings: list[Holding]


class QuoteResponse(ContractModel):
    ticker: str
    company_name: str
    price: Money
    currency: Currency
    as_of: AwareDatetime


class DeviceRegisterRequest(ContractModel):
    user_id: str
    public_key_jwk: dict[str, Any]


class DeviceRegisterResponse(ContractModel):
    status: Literal["registered"]


class ChallengeRequest(ContractModel):
    draft: Draft
    receipt: str


class ChallengeResponse(ContractModel):
    nonce: str
    signing_payload: str
    expires_at: AwareDatetime


class ExecuteRequest(ContractModel):
    nonce: str
    signature: str
    otp: str | None = None


class ActionResult(ContractModel):
    action_index: int = Field(ge=0)
    reference: str


class ExecutedResponse(ContractModel):
    status: Literal["executed"]
    results: list[ActionResult]


class NeedsExtraCheckResponse(ContractModel):
    status: Literal["needs_extra_check"]
    method: Literal["otp"]
    message: str


RejectReason = Literal[
    "bad_signature",
    "unknown_nonce",
    "nonce_used",
    "nonce_expired",
    "limit_exceeded",
    "velocity_exceeded",
    "kyc_incomplete",
    "insufficient_funds",
    "bad_otp",
]


class RejectedResponse(ContractModel):
    status: Literal["rejected"]
    reason: RejectReason
    message: str


ExecuteResponse = Annotated[
    ExecutedResponse | NeedsExtraCheckResponse | RejectedResponse, Field(discriminator="status")
]


class AuditEntry(ContractModel):
    index: int = Field(ge=0)
    time: AwareDatetime
    actor: str
    event: str
    data: dict[str, Any]
    prev_hash: str
    hash: str


class AuditResponse(ContractModel):
    chain_valid: bool
    entries: list[AuditEntry]


# §6 Errors


class ErrorDetail(ContractModel):
    code: Literal["bad_request", "not_found", "upstream_error"]
    message: str


class ErrorResponse(ContractModel):
    error: ErrorDetail
