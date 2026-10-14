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

from app.config import MAX_ACTIONS_PER_DRAFT, MAX_TEXT_CHARS

NONE_OPTION_ID = "none"  # option_id the UI sends when the user picks "None of these"


def _require_money_string(value: object) -> object:
    # A float has already lost precision, so money and rates must arrive as strings like "50.00".
    if not isinstance(value, str | Decimal):
        raise ValueError("money must be a string like '50.00'")
    return value


Money = Annotated[
    Decimal,
    BeforeValidator(_require_money_string),
    Field(ge=0, decimal_places=2),
    PlainSerializer(lambda value: f"{value:.2f}", return_type=str, when_used="json"),
]
Rate = Annotated[
    Decimal,
    BeforeValidator(_require_money_string),
    Field(gt=0, decimal_places=6),
    PlainSerializer(lambda value: f"{value:.6f}", return_type=str, when_used="json"),
]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Text = Annotated[str, Field(max_length=MAX_TEXT_CHARS)]
Probability = Annotated[float, Field(ge=0, le=1)]


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


class FxConversionAction(ContractModel):
    # Added by code, never said by the user; see docs/CONTRACTS.md §10.5.
    type: Literal["fx_conversion"]
    from_account_id: str
    from_account_label: str
    to_account_id: str
    to_account_label: str
    sell_amount: Money
    sell_currency: Currency
    buy_amount: Money
    buy_currency: Currency
    rate: Rate
    rate_as_of: AwareDatetime


Action = Annotated[
    TransferAction | BillPaymentAction | StockOrderAction | FxConversionAction,
    Field(discriminator="type"),
]


class Evidence(ContractModel):
    question: str
    pick: str
    avg_p: Probability
    spread: Probability
    probabilities: dict[str, Probability]


class Draft(ContractModel):
    draft_id: str
    user_id: str
    session_id: str
    transcript: str
    created_at: AwareDatetime
    actions: list[Action] = Field(min_length=1, max_length=MAX_ACTIONS_PER_DRAFT)
    evidence: list[Evidence]


# §3.1 AI side


class Meta(ContractModel):
    latency_ms: int = Field(ge=0)
    model: str
    source: Literal["jev", "fallback", "fake", "none"]
    phrasings: int = Field(ge=0)
    cached: bool


class PreviewRequest(ContractModel):
    text: Text


PreviewIntent = Literal["transfer", "bill_payment", "stock_order", "none"]


class IntentGuess(ContractModel):
    value: PreviewIntent
    confidence: Probability
    probabilities: dict[PreviewIntent, Probability]


class PreviewResponse(ContractModel):
    intent: IntentGuess
    meta: Meta


class TranscribeResponse(ContractModel):
    transcript: str
    confidence: float | None


class Answer(ContractModel):
    question_id: str
    option_id: str


class UnderstandRequest(ContractModel):
    user_id: str
    session_id: str
    transcript: Text | None = None
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
    evidence: list[Evidence]
    meta: Meta


Objective = Literal["prepare", "assess_affordability", "simulate", "check_recipient", "review"]
# Hypothetical values for `simulate`, keyed by action index: never part of the signed draft.
Scenario = dict[int, dict[Literal["amount", "quantity"], str]]


class DraftResponse(ContractModel):
    status: Literal["draft"]
    draft: Draft
    objective: Objective
    scenario: Scenario | None
    meta: Meta


class EnquiryResponse(ContractModel):
    status: Literal["enquiry"]
    topic: Literal["balances", "bills", "transactions", "spending", "overview"]
    user_id: str
    meta: Meta


class BlockedResponse(ContractModel):
    status: Literal["blocked"]
    reason: Literal["possible_injection"]
    message: str
    meta: Meta


class UnsupportedResponse(ContractModel):
    status: Literal["unsupported"]
    message: str
    meta: Meta


class CancelledResponse(ContractModel):
    status: Literal["cancelled"]
    reason: Literal["user_declined", "too_many_questions"]
    message: str
    meta: Meta


UnderstandResponse = Annotated[
    QuestionResponse
    | DraftResponse
    | EnquiryResponse
    | BlockedResponse
    | UnsupportedResponse
    | CancelledResponse,
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


class FxResponse(ContractModel):
    # `from` is a Python keyword, so the field is `from_` on the wire as "from" (§10.6).
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: Currency = Field(alias="from")
    to: Currency
    rate: Rate
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
    "rate_changed",
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


class EnquiryPresentationRequest(ContractModel):
    user_id: str = Field(min_length=1, max_length=128)
    topic: Literal["balances", "bills", "transactions", "spending", "overview"]
