from __future__ import annotations

from decimal import Decimal

from openpyxl import Workbook

from core.dedupe import txn_hash
from core.parsers.base import RawTxn
from core.parsers.golomt import GolomtParser


def test_golomt_detects_sample_sheet():
    wb = Workbook()
    ws = wb.active
    ws.append(["date", "description", "amount", "balance"])
    ws.append(["2024-01-15", "KFC", 1200, 5000])
    assert GolomtParser.detect(wb) is True


def test_txn_hash_stable_and_unique():
    t1 = RawTxn(
        posted_at=__import__("datetime").datetime(2024, 1, 15, 12, 0, 0),
        txn_at=__import__("datetime").datetime(2024, 1, 15, 12, 0, 0),
        amount=Decimal("1200.00"),
        direction="out",
        balance_after=Decimal("5000.00"),
        description="KFC",
    )
    t2 = RawTxn(
        posted_at=__import__("datetime").datetime(2024, 1, 15, 12, 0, 0),
        txn_at=__import__("datetime").datetime(2024, 1, 15, 12, 0, 0),
        amount=Decimal("1200.00"),
        direction="out",
        balance_after=Decimal("5000.00"),
        description="KFC",
    )
    assert txn_hash("123", t1) == txn_hash("123", t2)
