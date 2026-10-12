"""Read-only views of accounts, payees, billers and quotes; the only bank file the AI may import.

Owner: Jayanth
Diagram box: User data
Contract: docs/CONTRACTS.md §3.3 (GET /api/accounts, GET /api/quote)
"""

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.contracts import Account, AccountsResponse, Biller, Holding, Payee, QuoteResponse


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
