from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal


@dataclass
class RawTxn:
    posted_at: datetime
    txn_at: datetime
    amount: Decimal
    direction: Literal["in", "out"]
    balance_before: Decimal | None = None
    balance_after: Decimal | None = None
    description: str = ""
    counterparty_account: str | None = None
    card_mask: str | None = None
    channel: str | None = None
    merchant: str | None = None
    txn_type: str = "other"
    fee_ref: str | None = None
    parent_index: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StatementData:
    bank_code: str
    bank_name: str
    account_no: str
    iban: str | None = None
    currency: str = "MNT"
    holder_name: str | None = None
    period_from: date | None = None
    period_to: date | None = None
    transactions: list[RawTxn] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_columns: tuple[str, ...] = ()


class BankParser(ABC):
    code: str
    name: str

    @classmethod
    @abstractmethod
    def detect(cls, workbook: Any) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse(self, workbook: Any) -> StatementData:
        raise NotImplementedError
