from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Bank(Base):
    __tablename__ = "banks"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_id: Mapped[int | None] = mapped_column(ForeignKey("banks.id"), nullable=True)
    account_no: Mapped[str] = mapped_column(String(34), unique=True, nullable=False)
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="MNT", nullable=False)
    holder_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank: Mapped[Bank | None] = relationship(back_populates="accounts")


class OwnAccount(Base):
    __tablename__ = "own_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_no: Mapped[str] = mapped_column(String(34), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    period_from: Mapped[Date | None] = mapped_column(Date, nullable=True)
    period_to: Mapped[Date | None] = mapped_column(Date, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    total_rows: Mapped[int | None] = mapped_column(nullable=True)
    new_rows: Mapped[int | None] = mapped_column(nullable=True)
    duplicate_rows: Mapped[int | None] = mapped_column(nullable=True)
    account: Mapped[Account | None] = relationship(back_populates="batches")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(default=100, nullable=False)
    children: Mapped[list["Category"]] = relationship(back_populates="parent")
    parent: Mapped["Category" | None] = relationship(back_populates="children", remote_side="Category.id")


class Counterparty(Base):
    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    default_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    default_category: Mapped[Category | None] = relationship()


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    priority: Mapped[int] = mapped_column(default=100, nullable=False)
    field: Mapped[str] = mapped_column(String(30), nullable=False)
    match_type: Mapped[str] = mapped_column(String(20), nullable=False)
    pattern: Mapped[str] = mapped_column(String(300), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    txn_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_before: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    counterparty_account: Mapped[str | None] = mapped_column(String(34), nullable=True)
    card_mask: Mapped[str | None] = mapped_column(String(30), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(10), nullable=True)
    merchant: Mapped[str | None] = mapped_column(String(100), nullable=True)
    txn_type: Mapped[str] = mapped_column(String(20), nullable=False)
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    parent_txn_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    category_source: Mapped[str] = mapped_column(String(15), default="none", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    dedupe_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    batch: Mapped[ImportBatch] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship()
    counterparty: Mapped[Counterparty | None] = relationship()


Index("ix_transactions_posted_at", Transaction.posted_at)
Index("ix_transactions_txn_at", Transaction.txn_at)
Index("ix_transactions_category_id", Transaction.category_id)
Index("ix_transactions_counterparty_id", Transaction.counterparty_id)

Bank.accounts = relationship("Account", back_populates="bank")
Account.batches = relationship("ImportBatch", back_populates="account")
