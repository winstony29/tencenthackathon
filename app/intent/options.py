"""Option builder: builds the option lists Jev picks from, using the transcript and user data.

Owner: Winston
Diagram box: Option builder (Intent detection column)
Contract: none; user data comes from app/bank/readonly.py (CONTRACTS §3.3)
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from app import config
from app.contracts import AccountsResponse, QuoteResponse
from app.intent.jev import Question

ACTION_TYPES = ("transfer", "bill_payment", "stock_order")

_SMALL = {
    w: i
    for i, w in enumerate(
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen".split(),
        start=1,
    )
}
_TENS = {
    w: 10 * i
    for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)
}
_SCALES = {"hundred": 100, "thousand": 1000, "grand": 1000, "million": 1_000_000}
_SUFFIXES = {"k": 1000, "m": 1_000_000}
_TOKEN = re.compile(r"(\d[\d,]*(?:\.\d+)?)(k|m)?\b|[a-z]+")


@dataclass(frozen=True)
class Number:
    value: Decimal
    phrase: str


@dataclass(frozen=True)
class UserData:
    accounts: AccountsResponse
    quotes: list[QuoteResponse]


@dataclass(frozen=True)
class Options:
    """The questions for Jev, plus what each option means to the code and to the user."""

    questions: list[Question]
    labels: dict[str, dict[str, str]]  # question -> option_id -> what the user reads
    numbers: dict[str, Decimal]  # option_id ("n0", "n1", …) -> the number said


def parse_numbers(text: str) -> list[Number]:
    """Find every amount or count said in the text, in order.

    Handles digits ("50", "1,250.50", "$500"), suffixes ("50k"), number words
    ("fifty", "one hundred and fifty", "fifty thousand") and slang ("a grand",
    "5 grand"). "a" only counts before a scale word, so "send a friend" has no number.
    """
    tokens = [(m.group(1), m.group(2), m.group(0)) for m in _TOKEN.finditer(text.lower())]
    found: list[Number] = []
    words: list[str] = []

    def flush() -> None:
        while words and words[-1] in ("and", "a", "an"):
            words.pop()
        value = _words_value(words)
        if value:
            found.append(Number(value, " ".join(words)))
        words.clear()

    skip = False
    for i, (digits, suffix, word) in enumerate(tokens):
        if skip:
            skip = False
            continue
        following = tokens[i + 1][2] if i + 1 < len(tokens) else ""
        if digits:
            flush()
            value = Decimal(digits.replace(",", "")) * _SUFFIXES.get(suffix or "", 1)
            if following in _SCALES:
                value, word, skip = value * _SCALES[following], f"{word} {following}", True
            found.append(Number(value, word))
        elif word in _SMALL or word in _TENS or word in _SCALES:
            words.append(word)
        elif (word in ("a", "an") and following in _SCALES) or (word == "and" and words):
            words.append(word)
        else:
            flush()
    flush()
    return found


def _words_value(words: list[str]) -> Decimal | None:
    total = current = 0
    for word in words:
        if word in _SMALL or word in _TENS:
            current += _SMALL.get(word, 0) + _TENS.get(word, 0)
        elif word == "hundred":
            current = (current or 1) * 100
        elif word in _SCALES:
            total, current = total + (current or 1) * _SCALES[word], 0
    return Decimal(total + current) or None


def build(transcript: str, data: UserData) -> Options:
    """Build every question Jev answers about one command, in one batch.

    Values only ever come from these lists: payees, accounts, billers and tickers
    from the user's data, and numbers parsed from the transcript by code.
    """
    said = {f"n{i}": n for i, n in enumerate(parse_numbers(transcript))}
    a = data.accounts
    yes_no = [("yes", "Yes, it asks for this.", "Yes"), ("no", "No, it does not.", "No")]
    amounts = [
        (oid, f'The number said as "{n.phrase}" ({n.value})', f"{n.value:,}")
        for oid, n in said.items()
    ]
    no_number = ("none", "No such number is said.", "None of these")
    accounts = [
        (x.account_id, f"Use the {x.label} account ({x.type}, {x.currency})", x.label)
        for x in a.accounts
    ]
    default = ("default", "No account is said.", "My usual account")

    spec = {
        "has_transfer": ("Does the text ask to send money to a person?", yes_no),
        "has_bill_payment": (
            "Does the text ask to pay a bill, such as a credit card bill?",
            yes_no,
        ),
        "has_stock_order": ("Does the text ask to buy or sell shares?", yes_no),
        "recipient": (
            "Who should receive the money?",
            [(p.payee_id, f"Send to {p.name}", p.label) for p in a.payees]
            + [
                (
                    "none",
                    "The person named is not in this list, or no person is named.",
                    "Someone else",
                )
            ],
        ),
        "transfer_amount": ("How much money should be sent?", [*amounts, no_number]),
        "transfer_from": ("Which account should the money be sent from?", [*accounts, default]),
        "biller": (
            "Which bill should be paid?",
            [(b.biller_id, f"Pay the {b.name} bill", b.label) for b in a.billers]
            + [("none", "No bill in this list is meant.", "None of these")],
        ),
        "bill_amount": (
            "How much of the bill should be paid?",
            [
                (
                    "due",
                    "The full amount due; no other amount is said for the bill.",
                    "Full amount due",
                ),
                *amounts,
            ],
        ),
        "bill_from": ("Which account should the bill be paid from?", [*accounts, default]),
        "ticker": (
            "Which company's shares are meant?",
            [
                (
                    q.ticker,
                    f"Shares of {q.company_name} ({q.ticker})",
                    f"{q.ticker} · {q.company_name}",
                )
                for q in data.quotes
            ]
            + [("none", "No company in this list is meant.", "None of these")],
        ),
        "side": (
            "Should shares be bought or sold?",
            [("buy", "Buy shares.", "Buy"), ("sell", "Sell shares.", "Sell")],
        ),
        "quantity": ("How many shares?", [*amounts, no_number]),
        "order_type": (
            "Should the order trade now or only at a stated price?",
            [
                (
                    "market",
                    "Trade now at the market price; no price limit is said.",
                    "Market price",
                ),
                (
                    "limit",
                    "Trade only at a price the user states (a limit order).",
                    "Only at my price",
                ),
            ],
        ),
        "limit_price": ("What price limit is said for the order?", [*amounts, no_number]),
    }
    return Options(
        questions=[
            Question(name, text, {o: c for o, c, _ in opts}) for name, (text, opts) in spec.items()
        ],
        labels={name: {o: label for o, _, label in opts} for name, (_, opts) in spec.items()},
        numbers={oid: n.value for oid, n in said.items()},
    )


_ASK = {
    "has_transfer": "Do you want to send money to someone?",
    "has_bill_payment": "Do you want to pay a bill?",
    "has_stock_order": "Do you want to buy or sell shares?",
    "recipient": "Who should get the money?",
    "transfer_amount": "How much should I send?",
    "transfer_from": "Which account should I send it from?",
    "biller": "Which bill should I pay?",
    "bill_amount": "How much should I pay?",
    "bill_from": "Which account should I pay from?",
    "ticker": "Which stock did you mean?",
    "side": "Do you want to buy or sell?",
    "quantity": "How many shares?",
    "order_type": "At the market price, or only at your price?",
    "limit_price": "At what price?",
}


def clarify(
    options: Options, question: str, probabilities: dict[str, float]
) -> tuple[str, list[tuple[str, str]]]:
    """Word a short question about one unsure pick and choose its chips, most likely first.

    "none" and "default" are left out so the user always picks a real value, and
    options Jev gave almost no chance are dropped.
    """
    ranked = sorted(
        (o for o in probabilities if o not in ("none", "default")),
        key=lambda o: probabilities[o],
        reverse=True,
    )
    likely = [o for o in ranked if probabilities[o] >= config.CLARIFY_MIN_PROBABILITY]
    # When Jev found no real candidates (say, a name not in the list), offer the top ones.
    ranked = (likely if len(likely) >= 2 else ranked)[: config.CLARIFY_MAX_OPTIONS]
    chips = [(o, options.labels[question][o]) for o in ranked]
    text = _ASK[question]
    first_names = {label.split()[0] for _, label in chips[:2]}
    if question == "recipient" and len(chips) > 1 and len(first_names) == 1:
        text = f"Which {first_names.pop()} did you mean?"
    return text, chips
