from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.models import Account, Bank, Base, Category, Counterparty, ImportBatch, Transaction
from core.queries import transactions_df, unreviewed_counterparties_df


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _add_txn(
    session: Session,
    *,
    posted_at: datetime,
    category: Category | None,
    counterparty: Counterparty,
    description: str,
) -> None:
    bank = session.query(Bank).filter_by(code="TEST").one_or_none()
    if bank is None:
        bank = Bank(code="TEST", name="Test Bank")
        session.add(bank)
        session.flush()
    account = session.query(Account).filter_by(account_no="1234").one_or_none()
    if account is None:
        account = Account(bank_id=bank.id, account_no="1234", currency="MNT")
        session.add(account)
        session.flush()
    batch = session.query(ImportBatch).filter_by(file_sha256="a" * 64).one_or_none()
    if batch is None:
        batch = ImportBatch(
            account_id=account.id,
            file_name="test.xlsx",
            file_sha256="a" * 64,
            total_rows=1,
            new_rows=1,
            duplicate_rows=0,
        )
        session.add(batch)
        session.flush()
    session.add(
        Transaction(
            account_id=account.id,
            batch_id=batch.id,
            posted_at=posted_at,
            txn_at=posted_at,
            amount=Decimal("12.34"),
            direction="out",
            description=description,
            merchant="Test Shop",
            txn_type="card",
            counterparty_id=counterparty.id,
            category_id=category.id if category else None,
            category_source="none",
            raw={},
            dedupe_hash=f"{posted_at.isoformat():<64}"[:64],
        )
    )


def test_transaction_query_includes_entire_end_date_and_counterparty(db_session: Session) -> None:
    category = Category(name="Groceries", kind="expense")
    counterparty = Counterparty(
        key="M:TEST SHOP", kind="merchant", display_name="Test Shop"
    )
    db_session.add_all([category, counterparty])
    db_session.flush()
    _add_txn(
        db_session,
        posted_at=datetime(2026, 10, 20, 23, 59, 59),
        category=category,
        counterparty=counterparty,
        description="Late transaction",
    )
    db_session.commit()

    rows = transactions_df(db_session, date(2026, 10, 20), date(2026, 10, 20))

    assert len(rows) == 1
    assert rows.iloc[0]["counterparty"] == "Test Shop"
    assert rows.iloc[0]["amount"] == Decimal("12.34")
    assert rows.iloc[0]["signed_amount"] == Decimal("-12.34")
    assert len(
        transactions_df(
            db_session,
            datetime(2026, 10, 20, 23, 59, 59),
            datetime(2026, 10, 20, 23, 59, 59),
        )
    ) == 1


def test_transaction_query_empty_filters_return_no_results(db_session: Session) -> None:
    category = Category(name="Groceries", kind="expense")
    counterparty = Counterparty(
        key="M:TEST SHOP", kind="merchant", display_name="Test Shop"
    )
    db_session.add_all([category, counterparty])
    db_session.flush()
    _add_txn(
        db_session,
        posted_at=datetime(2026, 10, 20, 12),
        category=category,
        counterparty=counterparty,
        description="A 100% special transaction",
    )
    db_session.commit()

    assert transactions_df(db_session, account_ids=[]).empty
    assert transactions_df(db_session, category_ids=[], include_uncategorized=False).empty
    assert transactions_df(db_session, txn_types=[]).empty
    assert transactions_df(db_session, search="100%").shape[0] == 1
    assert transactions_df(db_session, search="100X").empty


def test_review_query_uses_most_common_category_and_decimal_totals(
    db_session: Session,
) -> None:
    groceries = Category(name="Groceries", kind="expense")
    dining = Category(name="Dining", kind="expense")
    counterparty = Counterparty(
        key="M:TEST SHOP",
        kind="merchant",
        display_name="Test Shop",
        reviewed=False,
    )
    db_session.add_all([groceries, dining, counterparty])
    db_session.flush()
    _add_txn(
        db_session,
        posted_at=datetime(2026, 10, 19, 10),
        category=groceries,
        counterparty=counterparty,
        description="Food one",
    )
    _add_txn(
        db_session,
        posted_at=datetime(2026, 10, 18, 10),
        category=groceries,
        counterparty=counterparty,
        description="Food two",
    )
    _add_txn(
        db_session,
        posted_at=datetime(2026, 10, 17, 10),
        category=dining,
        counterparty=counterparty,
        description="Cafe",
    )
    db_session.commit()

    rows = unreviewed_counterparties_df(db_session)

    assert len(rows) == 1
    assert rows.iloc[0]["current_category"] == "Groceries"
    assert rows.iloc[0]["total_out"] == Decimal("37.02")
