"""Option builder: builds the option lists Jev picks from, using the transcript and user data.

Owner: Winston
Diagram box: Option builder (Intent detection column)
Contract: none; user data comes from app/bank/readonly.py (CONTRACTS §3.3)
"""

import hashlib
import hmac
import re
from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import groupby

from app import config
from app.contracts import AccountsResponse, Draft, QuoteResponse
from app.intent import shield
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
# Spoken spellings an ASR transcript may contain, mapped to the number word meant.
_ALIASES = {
    "fiddy": "fifty",
    "fitty": "fifty",
    "tirty": "thirty",
    "tree": "three",
    "fourty": "forty",
    "tousand": "thousand",
    "hunnid": "hundred",
}
_FRACTIONS = {
    "half": Decimal("0.5"),
    "quarter": Decimal("0.25"),
    "all": Decimal(1),
    "everything": Decimal(1),
    "rest": Decimal(1),
    "left": Decimal(1),
    "remaining": Decimal(1),
    "leftover": Decimal(1),
}
# These mean what is left after the actions said before them (§10.4); draft.py works it out.
_REMAINING = {"rest", "left", "remaining", "leftover"}
_FRACTION = re.compile(r"\b(" + "|".join(_FRACTIONS) + r")\b")
# Matched on the original text (not text.lower()) so spans index the text mask() rewrites.
_TOKEN = re.compile(r"(\d[\d,]*(?:\.\d+)?)(k|m)?\b|[a-z]+", re.IGNORECASE)
_EMAIL = re.compile(r"\S+@\S+")
_USD_BEFORE = re.compile(r"(?:\bus\$|\busd)\s*$", re.IGNORECASE)
_USD_AFTER = re.compile(r"^\s*(?:usd\b|us\s*dollars?\b|us\$)", re.IGNORECASE)


@dataclass(frozen=True)
class Number:
    value: Decimal
    phrase: str
    span: tuple[int, int] | None = None  # where it was said; None when code computed it
    currency: str = "SGD"  # amounts are SGD unless the user names US dollars (§10.3)
    account_id: str | None = None  # the account a relative amount is a fraction of
    remaining: bool = False  # "the rest": what is left after the earlier actions (§10.4)


@dataclass(frozen=True)
class UserData:
    accounts: AccountsResponse
    quotes: list[QuoteResponse]


@dataclass(frozen=True)
class Options:
    """The questions for Jev, plus what each option means to the code and to the user."""

    text: str  # what Jev reads: the text with every number swapped for its stand-in
    questions: list[Question]
    labels: dict[str, dict[str, str]]  # question -> option_id -> what the user reads
    numbers: dict[str, Decimal]  # option_id ("n0", "n1", …) -> the number said
    said: dict[str, Number]  # option_id -> the full number (currency, account, "the rest")


def parse_numbers(text: str) -> list[Number]:
    """Find every amount or count said in the text, in order.

    Handles digits ("50", "1,250.50", "$500"), suffixes ("50k"), number words
    ("fifty", "one hundred and fifty", "fifty thousand"), slang ("a grand",
    "5 grand") and spoken spellings ("fiddy", "tirty"). "a" only counts before a
    scale word, so "send a friend" has no number.
    """
    tokens = [
        (m.group(1), (m.group(2) or "").lower(), _ALIASES.get(w, w), m.span())
        for m in _TOKEN.finditer(text)
        for w in [m.group(0).lower()]
    ]
    found: list[Number] = []
    words: list[tuple[str, tuple[int, int]]] = []

    def flush() -> None:
        while words and words[-1][0] in ("and", "a", "an"):
            words.pop()
        value = _words_value([w for w, _ in words])
        if value:
            span = (words[0][1][0], words[-1][1][1])
            found.append(Number(value, " ".join(w for w, _ in words), span))
        words.clear()

    skip = False
    for i, (digits, suffix, word, span) in enumerate(tokens):
        if skip:
            skip = False
            continue
        following = tokens[i + 1][2] if i + 1 < len(tokens) else ""
        if digits:
            flush()
            value = Decimal(digits.replace(",", "")) * _SUFFIXES.get(suffix, 1)
            if following in _SCALES:
                value, word, skip = value * _SCALES[following], f"{word} {following}", True
                span = (span[0], tokens[i + 1][3][1])
            found.append(Number(value, word, span))
        elif word in _SMALL or word in _TENS or word in _SCALES:
            words.append((word, span))
        elif (word in ("a", "an") and following in _SCALES) or (word == "and" and words):
            words.append((word, span))
        else:
            flush()
    flush()
    return [_with_currency(text, n) for n in found]


def _with_currency(text: str, n: Number) -> Number:
    # "us" alone isn't enough ("give us 50"); only US$, USD or US dollars name the currency.
    before, after = text[: n.span[0]], text[n.span[1] :]
    usd = _USD_BEFORE.search(before) or _USD_AFTER.search(after)
    return replace(n, currency="USD") if usd else n


def _stand_in(value: Decimal, scope: str, taken: set[int]) -> int:
    # Keyed, so a stand-in can't be worked back by hashing every amount; the counter only
    # moves on when two different values in one text would share a stand-in. It is two digits
    # and then zeros, with the value's digit count, because Jev reads "send 48213" or
    # "send 437" as less like a transfer than "send 72000" (live, 12 Oct): Jev learns the
    # order of magnitude, never the value.
    scale = 10 ** max(0, len(str(int(value))) - 2)
    # After STAND_IN_TRIES a clash is accepted: two numbers sharing a stand-in read the same to
    # Jev, so its pick splits between them and the gate asks the user, whose chips show the
    # real values.
    for attempt in range(config.STAND_IN_TRIES):
        message = f"{scope}|{value.normalize()}|{attempt}".encode()
        digest = hmac.new(config.MASK_KEY, message, hashlib.sha256).digest()
        number = (10 + int.from_bytes(digest[:4], "big") % 90) * scale
        if number not in taken:
            return number
    return number


def stand_ins(numbers: list[Number], scope: str) -> list[int]:
    """The stand-in for each number, in order: equal values share one, different values never do."""
    chosen: dict[Decimal, int] = {}
    for n in numbers:
        if n.value not in chosen:
            chosen[n.value] = _stand_in(n.value, scope, set(chosen.values()))
    return [chosen[n.value] for n in numbers]


def mask(text: str, scope: str) -> str:
    """Swap every number said for its stand-in, so Jev reads a natural sentence but no value.

    Stand-ins come from a keyed hash of the value and the scope (user and session): normally
    the same for a whole conversation, different across conversations, and impossible to work
    back without config.MASK_KEY. Digits the parser missed become "[number]", and email
    addresses become "[email]".
    """
    # ponytail: number words parse_numbers doesn't know ("dozen", "couple") pass through
    # unmasked; add them to _SMALL / _SCALES if they turn up in real transcripts.
    numbers = parse_numbers(text)
    parts, last = [], 0
    for n, stand_in in zip(numbers, stand_ins(numbers, scope), strict=True):
        start, end = n.span
        parts += [_hide_digits(text[last:start]), str(stand_in)]
        last = end
    parts.append(_hide_digits(text[last:]))
    return _EMAIL.sub("[email]", "".join(parts))


def _hide_digits(text: str) -> str:
    # Digits the parser missed, in any script ("⑨", "²", "九"), not just 0-9.
    return "".join("[number]" if n else "".join(run) for n, run in groupby(text, str.isnumeric))


# One yes/no pair per action type. Saying what does NOT count keeps Jev from hesitating
# between them, e.g. "pay 500 towards my DBS card" is a bill payment, not a transfer.
_ASKS_FOR = {
    "transfer": [
        (
            "yes",
            "It asks to send or give money to a person. Paying a bill or card is not this.",
            "Yes",
        ),
        (
            "no",
            "It does not ask to send money to a person; paying a bill or buying shares is 'no'.",
            "No",
        ),
    ],
    "bill_payment": [
        ("yes", "It asks to pay a bill or a card balance, such as a credit card bill.", "Yes"),
        ("no", "It does not ask to pay a bill or card.", "No"),
    ],
    "stock_order": [
        (
            "yes",
            "It asks to buy or sell shares, or to invest a sum in a company; "
            "e.g. 'sell 5 apple' or 'invest 2k in apple'.",
            "Yes",
        ),
        ("no", "It does not ask to buy or sell shares.", "No"),
    ],
}

# Wording tuned against the live Jev (12 Oct): a short "none" lets a first name shared by two
# payees split between them instead of falling to "none"; naming the kind of bill that is NOT
# in the list lifts "my credit card bill" from 0.63 to 0.93.
_NO_PAYEE = "Someone not in this list, or no one named."
_NO_BILL = (
    "A different kind of bill that is not in this list, such as electricity or phone, "
    "or no bill at all."
)
_NO_COMPANY = "A company that is not in this list, or no company at all."


def _payee(name: str) -> str:
    return f"Send to {name}; they may be called by part of their name or a nickname."


def _number_options(said: dict[str, Number], shown: list[int]) -> list[tuple[str, str, str]]:
    # Jev reads the stand-in or the fraction, never the value; only the user's chip shows it.
    # Parsed numbers come first in `said`, so the i-th one has the i-th stand-in.
    return [
        (
            oid,
            f'The number said as "{shown[i]}"' if n.span else f'The amount "{n.phrase}"',
            _label(n),
        )
        for i, (oid, n) in enumerate(said.items())
    ]


def _label(n: Number) -> str:
    # What the chip shows. "The rest" depends on the earlier actions, so it names the account
    # instead of a balance that would be wrong; other fractions show their value too.
    if n.span:
        return f"{n.value:,}"
    return n.phrase if n.remaining else f"{n.phrase} ({n.value:,})"


def _biller(name: str, label: str, only: bool) -> str:
    return f"The {name} bill ({label})" + (", the user's only saved bill." if only else ".")


def _relative(text: str, accounts: AccountsResponse) -> list[Number]:
    """Turn "half my savings" or "everything in current" into amounts, computed by code.

    Offers the fraction of every account's balance; Jev picks which account was meant.
    """
    found = []
    for word in dict.fromkeys(_FRACTION.findall(text.lower())):
        for x in accounts.accounts:
            value = (x.balance * _FRACTIONS[word]).quantize(Decimal("0.01"))
            found.append(
                Number(
                    value,
                    f"{word} of the {x.label} balance",
                    currency=x.currency,
                    account_id=x.account_id,
                    remaining=word in _REMAINING,
                )
            )
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


def build(transcript: str, data: UserData, scope: str) -> Options:
    """Build every question Jev answers about one command, in one batch.

    Values only ever come from these lists: payees, accounts, billers and tickers
    from the user's data, and numbers parsed from the transcript by code. The scope keys
    the stand-in numbers; pass the user and session.
    """
    a = data.accounts
    parsed = parse_numbers(transcript)
    said = {f"n{i}": n for i, n in enumerate(parsed + _relative(transcript, a))}
    amounts = _number_options(said, stand_ins(parsed, scope))
    # A share count or a price is always said, never a fraction of a balance: offering
    # "the rest of the Invest balance" (25,000) for "How many shares?" could draft 25,000 shares.
    said_aloud = amounts[: len(parsed)]
    # A sum of money for shares ("invest 2k in Apple"); draft.py turns it into whole shares.
    sums = [
        ("m" + oid[1:], f"A sum of money: {text[0].lower()}{text[1:]}", f"{label} worth")
        if said[oid].span
        else ("m" + oid[1:], f"A sum of money: {text[0].lower()}{text[1:]}", label)
        for oid, text, label in amounts
    ]
    no_number = ("none", "No such number is said.", "None of these")
    accounts = [
        (x.account_id, f"Use the {x.label} account ({x.type}, {x.currency})", x.label)
        for x in a.accounts
    ]
    default = ("default", "No account is said.", "My usual account")

    spec = {
        "has_transfer": ("Does the text ask to send money to a person?", _ASKS_FOR["transfer"]),
        "has_bill_payment": ("Does the text ask to pay a bill or card?", _ASKS_FOR["bill_payment"]),
        "has_stock_order": ("Does the text ask to buy or sell shares?", _ASKS_FOR["stock_order"]),
        "several_transfers": (
            "Does the text ask for two or more separate transfers?",
            [
                (
                    "yes",
                    "It asks to send money to two or more people, or two transfers to one "
                    "person. Numbers for shares or bills do not count.",
                    "Yes",
                ),
                ("no", "At most one transfer. Buying shares or paying a bill doesn't count.", "No"),
            ],
        ),
        "recipient": (
            "Who should receive the money?",
            [(p.payee_id, _payee(p.name), p.label) for p in a.payees]
            + [("none", _NO_PAYEE, "Someone else")],
        ),
        "transfer_amount": ("How much money should be sent?", [*amounts, no_number]),
        "transfer_from": ("Which account should the money be sent from?", [*accounts, default]),
        "biller": (
            "Which bill should be paid?",
            [
                (b.biller_id, _biller(b.name, b.label, len(a.billers) == 1), b.label)
                for b in a.billers
            ]
            + [("none", _NO_BILL, "None of these")],
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
            + [("none", _NO_COMPANY, "None of these")],
        ),
        "side": (
            "Should shares be bought or sold?",
            [("buy", "Buy shares.", "Buy"), ("sell", "Sell shares.", "Sell")],
        ),
        "quantity": (
            "How big is the share order: a number of shares, or a sum of money to spend on it"
            " (which can be all, half or the rest of an account)?",
            [*said_aloud, *sums, ("none", "The size of the order is not given.", "None of these")],
        ),
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
        "limit_price": ("What price limit is said for the order?", [*said_aloud, no_number]),
        "objective": ("What does the user want to do with the task?", OBJECTIVES),
    }
    return Options(
        text=mask(transcript, scope),
        # The shield question rides in the same call, so a command costs one Jev call.
        questions=[shield.QUESTION]
        + [Question(name, text, {o: c for o, c, _ in opts}) for name, (text, opts) in spec.items()],
        labels={name: {o: label for o, _, label in opts} for name, (_, opts) in spec.items()},
        numbers={oid: n.value for oid, n in said.items()},
        said=said,
    )


OBJECTIVES = [
    ("prepare", "Set the task up to be done; the default.", "Set it up"),
    (
        "assess_affordability",
        "Check whether they can afford it, or what balance would remain.",
        "Check I can afford it",
    ),
    (
        "simulate",
        "Explore a hypothetical: what if the amount or quantity were different.",
        "Try another amount",
    ),
    ("check_recipient", "Check who the recipient is before sending.", "Check the recipient"),
    ("review", "Review the task before confirming it.", "Review it"),
]
NEW_TASK = (
    "new_task",
    "A different banking command, unrelated to the current tasks.",
    "Start a new request",
)
KEEP = ("keep", "The text does not change this.", "Keep as is")


def build_followup(text: str, draft: Draft, data: UserData, scope: str) -> Options:
    """Build the questions for a follow-up turn about an existing draft.

    Jev picks the objective and, for each current task, a new value or "keep".
    New values still only come from the user's data and numbers parsed by code. The scope
    keys the stand-in numbers; pass the user and session.
    """
    a = data.accounts
    parsed = parse_numbers(text)
    said = {f"n{i}": n for i, n in enumerate(parsed + _relative(text, a))}
    amounts = _number_options(said, stand_ins(parsed, scope))
    said_aloud = amounts[: len(parsed)]  # share counts are never balance fractions (see build)
    accounts = [
        (x.account_id, f"Use the {x.label} account ({x.type}, {x.currency})", x.label)
        for x in a.accounts
    ]
    spec = {"objective": ("What does the user want to do now?", [*OBJECTIVES, NEW_TASK])}
    for i, action in enumerate(draft.actions):
        if action.type == "fx_conversion":
            continue  # derived by code from the other actions, so there is nothing to ask
        task = f"For task {i + 1} ({describe(action)})"
        if action.type == "stock_order":
            spec[f"a{i}_quantity"] = (
                f"{task}, what new number of shares is said?",
                [*said_aloud, KEEP],
            )
            continue
        due = (
            [("due", "The full amount due on the bill.", "Full amount due")]
            if action.type == "bill_payment"
            else []
        )
        spec[f"a{i}_amount"] = (f"{task}, what new amount is said?", [*due, *amounts, KEEP])
        spec[f"a{i}_from"] = (f"{task}, which account should it now come from?", [*accounts, KEEP])
        if action.type == "transfer":
            payees = [(p.payee_id, _payee(p.name), p.label) for p in a.payees]
            spec[f"a{i}_recipient"] = (f"{task}, who should now receive it?", [*payees, KEEP])
    return Options(
        text=mask(text, scope),
        questions=[shield.QUESTION]
        + [Question(name, q, {o: c for o, c, _ in opts}) for name, (q, opts) in spec.items()],
        labels={name: {o: label for o, _, label in opts} for name, (_, opts) in spec.items()},
        numbers={oid: n.value for oid, n in said.items()},
        said=said,
    )


def describe(action) -> str:
    """Say one draft action in a few words, for Jev's context and follow-up questions.

    Leaves out amounts and quantities: Jev only needs to tell the tasks apart.
    """
    if action.type == "transfer":
        return f"transfer to {action.to_payee_label} from {action.from_account_label}"
    if action.type == "bill_payment":
        return f"pay {action.biller_label} from {action.from_account_label}"
    if action.type == "fx_conversion":
        return (
            f"convert {action.sell_currency} from {action.from_account_label} "
            f"to {action.buy_currency}"
        )
    return f"{action.side} {action.ticker} shares"


_ASK = {
    "objective": "What would you like to do?",
    "amount": "Which amount did you mean?",
    "from": "Which account did you mean?",
    "has_transfer": "Do you want to send money to someone?",
    "has_bill_payment": "Do you want to pay a bill?",
    "has_stock_order": "Do you want to buy or sell shares?",
    "several_transfers": "Is this more than one transfer?",
    "recipient": "Who should get the money?",
    "transfer_amount": "How much should I send?",
    "transfer_from": "Which account should I send it from?",
    "biller": "Which bill should I pay?",
    "bill_amount": "How much should I pay?",
    "bill_from": "Which account should I pay from?",
    "ticker": "Which stock did you mean?",
    "side": "Do you want to buy or sell?",
    "quantity": "How many shares, or how much money?",
    "order_type": "At the market price, or only at your price?",
    "limit_price": "At what price?",
}


def clarify(
    options: Options, question: str, probabilities: dict[str, float]
) -> tuple[str, list[tuple[str, str]]]:
    """Word a short question about one unsure pick and choose its chips, most likely first.

    "none" and "default" are left out so the user always picks a real value, and
    options far less likely than the best one are dropped.
    """
    ranked = sorted(
        (o for o in probabilities if o not in ("none", "default")),
        key=lambda o: probabilities[o],
        reverse=True,
    )
    top = probabilities[ranked[0]] if ranked else 0.0
    likely = [o for o in ranked if probabilities[o] >= max(config.CLARIFY_MIN_SHARE * top, 0.01)]
    # One strong candidate becomes a confirmation chip; with no real candidate, offer the top few.
    enough = len(likely) >= 2 or (likely and top >= 0.5)
    ranked = (likely if enough else ranked)[: config.CLARIFY_MAX_OPTIONS]
    chips = [(o, options.labels[question][o]) for o in ranked]
    text = _ASK.get(question) or _ASK[question.split("_", 1)[1]]  # a0_amount -> amount
    first_names = {label.split()[0] for _, label in chips[:2]}
    # "Which John?" only when Jev really weighed two people sharing that name.
    if question == "recipient" and enough and len(chips) > 1 and len(first_names) == 1:
        text = f"Which {first_names.pop()} did you mean?"
    return text, chips


SPOKEN_REPLY = "spoken_reply"
NEW_REQUEST = "new_request"


def spoken_reply_question(built: Options, name: str, offered: set[str]) -> Question:
    """The question Jev answers about a spoken reply to an open clarify question (§10.2).

    Offers the chips' options worded as Jev first saw them, so a reply about an amount is
    matched by stand-in and no real value appears, plus "none of these" and "new request".
    """
    first = next(q for q in built.questions if q.name == name)
    asked = _ASK.get(name) or _ASK[name.split("_", 1)[1]]
    if set(first.criteria) == {"yes", "no"}:
        # No "none" here: it would split a spoken "no" with "no"; the router cancels on an
        # unoffered "no" anyway.
        criteria = {"yes": "The reply agrees, e.g. 'yes' or 'sure'.", "no": "The reply disagrees."}
    else:
        criteria = {o: first.criteria[o] for o in sorted(offered)}
        criteria["none"] = "The reply rejects every option above, e.g. 'neither' or 'no'."
    criteria[NEW_REQUEST] = (
        "The reply asks for a different banking task instead, e.g. 'actually, pay my bill'."
    )
    instructions = f'The user was asked "{asked}" and replied. Which option does the reply choose?'
    return Question(SPOKEN_REPLY, instructions, criteria)
