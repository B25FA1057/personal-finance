from __future__ import annotations

import hashlib
from decimal import Decimal

from .parsers.base import RawTxn


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def txn_hash(account_no: str, t: RawTxn) -> str:
    text = (
        f"{account_no}|{t.posted_at:%Y-%m-%dT%H:%M:%S}|{t.direction}|{Decimal(str(t.amount)).quantize(Decimal('0.01')):.2f}|"
        f"{t.balance_after or ''}|{(t.description or '').strip()}"
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
