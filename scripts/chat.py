"""Conversation CLI: talk to /api/understand turn by turn, against live Jev and mock bank data.

Owner: Winston
Diagram box: none; a developer tool around the Intent detection column
Contract: CONTRACTS §3.1 (/api/understand); needs MAKERS_MODELS_KEY; about one Jev call per turn
"""

import argparse
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so it runs from any folder

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.bank import readonly  # noqa: E402
from app.contracts import NONE_OPTION_ID, FxResponse  # noqa: E402
from app.intent import jev  # noqa: E402
from app.main import app  # noqa: E402
from tests.conftest import BANK, FX, QUOTES  # noqa: E402

HELP = (
    "Say a banking command. While a question is open, type a chip number (0 = none of these);\n"
    "anything else is a new turn in the same conversation. /new starts over, /quit leaves."
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a conversation with the intent API.")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show what Jev reads and its evidence"
    )
    args = parser.parse_args()
    if not config.MAKERS_MODELS_KEY:
        sys.exit("Set MAKERS_MODELS_KEY in .env first.")
    _use_mock_bank()
    if args.verbose:
        _show_what_jev_reads()
    client, session_id, question = TestClient(app), _new_session(), None
    print(HELP)
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line in ("/quit", "/q"):
            break
        if line == "/new":
            session_id, question = _new_session(), None
            print("(new conversation)")
            continue
        if line:
            body = {"user_id": "u_demo", "session_id": session_id, **_turn(line, question)}
            response = client.post("/api/understand", json=body)
            question = _show(response.json(), response.status_code, args.verbose)


def _use_mock_bank() -> None:
    # ponytail: Jayanth's readonly views (J-02) aren't in yet, so the test fixtures stand in;
    # once they land, the real views are used for user "u_demo".
    if not hasattr(readonly, "get_accounts"):
        readonly.get_accounts = lambda user_id: BANK
        readonly.list_quotes = lambda: QUOTES
        readonly.get_bill_reference = lambda biller_id: "4120-XXXX-XXXX-9012"
        readonly.get_fx_rate = _fx_rate


def _fx_rate(from_currency: str, to_currency: str) -> FxResponse:
    if (from_currency, to_currency) not in FX:
        raise LookupError(f"no rate for {from_currency} to {to_currency}")
    rate = FX[from_currency, to_currency]
    as_of = datetime.now().astimezone().isoformat()
    return FxResponse.model_validate(
        {"from": from_currency, "to": to_currency, "rate": rate, "as_of": as_of}
    )


def _show_what_jev_reads() -> None:
    real_ask = jev.ask

    async def ask(text, questions, context=None):
        print(f"  [jev reads] {text}")
        return await real_ask(text, questions, context)

    jev.ask = ask  # ask_all looks ask up in the module, so it goes through here too


def _new_session() -> str:
    return f"s_cli_{uuid.uuid4().hex[:6]}"


def _turn(line: str, question: dict | None) -> dict:
    """A chip number answers the open question; any other text is a new transcript."""
    options = question["options"] if question else []
    if line.isdigit() and int(line) <= len(options):
        pick = NONE_OPTION_ID if line == "0" else options[int(line) - 1]["option_id"]
        return {"answer": {"question_id": question["question_id"], "option_id": pick}}
    return {"transcript": line}


def _show(reply: dict, status: int, verbose: bool) -> dict | None:
    """Print one reply as text; return the open question, if any."""
    if status != 200:
        print(f"bank> error {status}: {reply.get('error', reply)}")
        return None
    meta = reply["meta"]
    if reply["status"] == "question":
        print(f"bank> {reply['question']['text']}")
        for i, chip in enumerate(reply["question"]["options"], start=1):
            print(f"      {i}. {chip['label']}")
        print("      0. None of these")
    elif reply["status"] == "draft":
        print(f"bank> draft ready (objective: {reply['objective']})")
        for action in reply["draft"]["actions"]:
            print(f"      - {_say(action)}")
        if reply["scenario"]:
            print(f"      what if: {reply['scenario']}")
    else:
        print(f"bank> [{reply['status']}] {reply.get('message', '')}")
    if verbose:
        evidence = reply.get("evidence") or (reply.get("draft") or {}).get("evidence") or []
        for e in evidence:
            print(f"  [evidence] {e['question']}: {e['pick']} (avg {e['avg_p']})")
    print(f"  ({meta['latency_ms']} ms, {meta['model'] or 'no model'}, source {meta['source']})")
    return reply["question"] if reply["status"] == "question" else None


def _say(action: dict) -> str:
    source = action["from_account_label"]
    if action["type"] == "transfer":
        money = f"{action['amount']} {action['currency']}"
        return f"transfer {money} to {action['to_payee_label']} from {source}"
    if action["type"] == "fx_conversion":
        sold = f"{action['sell_amount']} {action['sell_currency']}"
        got = f"{action['buy_amount']} {action['buy_currency']}"
        into, rate = action["to_account_label"], action["rate"]
        return f"convert {sold} from {source} to {got} into {into} at {rate}"
    if action["type"] == "bill_payment":
        money = f"{action['amount']} {action['currency']}"
        return f"pay {money} to {action['biller_label']} from {source}"
    price = "market price" if action["order_type"] == "market" else f"limit {action['limit_price']}"
    return f"{action['side']} {action['quantity']} {action['ticker']} at {price} from {source}"


if __name__ == "__main__":
    main()
