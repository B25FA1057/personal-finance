from __future__ import annotations

from decimal import Decimal
from typing import Any

from .config import QPAY_FEE_ACCOUNTS, FEE_LINK_WINDOW_SECONDS
from .parsers.base import RawTxn


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().replace("\xa0", " ")


def enrich(txn: RawTxn, my_accounts: set[str], bank_code: str = "GOLOMT") -> RawTxn:
    description = _normalize_text(txn.description)
    txn.description = description
    txn.merchant = description

    if txn.counterparty_account and txn.counterparty_account in my_accounts:
        txn.txn_type = "own_transfer"
        txn.merchant = "Own account transfer"

    if "QPAY" in description.upper() or any(acct in description for acct in QPAY_FEE_ACCOUNTS):
        txn.txn_type = "qpay"

    if "FEE" in description.upper() or "BANK FEE" in description.upper():
        txn.txn_type = "fee"

    if txn.counterparty_account and txn.counterparty_account.startswith("500"):
        txn.txn_type = "p2p_out"

    return txn


def link_fees(items: list[RawTxn]) -> None:
    for idx, item in enumerate(items):
        if item.txn_type != "fee":
            continue
        desc = item.description.upper()
        if "PAYMENT" in desc or "CARD" in desc:
            for other in items[idx + 1 :]:
                if other.txn_type == "qpay":
                    if abs((other.posted_at - item.posted_at).total_seconds()) <= FEE_LINK_WINDOW_SECONDS:
                        other.txn_type = "fee"
                        break
