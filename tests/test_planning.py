from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from core.models import (
    Account,
    Bank,
    Base,
    Category,
    ImportBatch,
    Transaction,
)
from core.planning import (
    budget_metrics,
    parse_amount,
    savings_goal_metrics,
    validate_goal_values,
)
from core.queries import budget_actuals_df, currency_options


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _account_and_batch(session: Session) -> tuple[Account, ImportBatch]:
    bank = Bank(code="PLAN_TEST", name="Planning Test Bank")
    session.add(bank)
    session.flush()
    account = Account(bank_id=bank.id, account_no="PLAN1234", currency="USD")
    session.add(account)
    session.flush()
    batch = ImportBatch(
        account_id=account.id,
        file_name="planning-test.xlsx",
        file_sha256="b" * 64,
        total_rows=1,
        new_rows=1,
        duplicate_rows=0,
    )
    session.add(batch)
    session.flush()
    return account, batch


def _add_transaction(
    session: Session,
    *,
    category: Category,
    txn_at: datetime,
    amount: Decimal,
    dedupe_hash: str,
    direction: str = "out",
    txn_type: str = "card",
) -> None:
    account = session.query(Account).one_or_none()
    batch = session.query(ImportBatch).one_or_none()
    if account is None or batch is None:
        account, batch = _account_and_batch(session)
    session.add(
        Transaction(
            account_id=account.id,
            batch_id=batch.id,
            posted_at=txn_at,
            txn_at=txn_at,
            amount=amount,
            direction=direction,
            description="Planning test transaction",
            txn_type=txn_type,
            category_id=category.id,
            category_source="manual",
            raw={},
            dedupe_hash=dedupe_hash,
        )
    )


@pytest.mark.parametrize(
    ("amount", "allow_zero"),
    [
        ("", False),
        ("NaN", False),
        ("-1", False),
        ("0", False),
        ("1.234", False),
        ("1000000000000", False),
        ("-0.01", True),
    ],
)
def test_parse_amount_rejects_invalid_values(amount: str, allow_zero: bool) -> None:
    with pytest.raises(ValueError):
        parse_amount(amount, "Amount", allow_zero=allow_zero)


def test_parse_amount_accepts_zero_for_saved_amount() -> None:
    assert parse_amount("0.00", "Saved", allow_zero=True) == Decimal("0.00")


def test_budget_thresholds_and_remaining_amount() -> None:
    assert budget_metrics(Decimal("100"), Decimal("79.99")) == (
        Decimal("20.01"),
        Decimal("79.99"),
        "On track",
    )
    remaining, percent, status = budget_metrics(
        Decimal("100"), Decimal("80")
    )
    assert (remaining, percent, status) == (
        Decimal("20"),
        Decimal("80"),
        "Near limit",
    )
    assert budget_metrics(Decimal("100"), Decimal("100"))[2] == "Near limit"
    remaining, percent, status = budget_metrics(Decimal("100"), Decimal("125"))
    assert (remaining, percent, status) == (
        Decimal("-25"),
        Decimal("125"),
        "Over budget",
    )


def test_budget_actuals_use_month_boundaries_and_exclude_transfers(
    db_session: Session,
) -> None:
    expense = Category(name="Planning expense", kind="expense")
    empty_expense = Category(name="No transactions", kind="expense")
    transfer = Category(name="Planning transfer", kind="transfer")
    db_session.add_all([expense, empty_expense, transfer])
    db_session.flush()
    _add_transaction(
        db_session,
        category=expense,
        txn_at=datetime(2026, 9, 30, 23, 59, 59),
        amount=Decimal("100"),
        dedupe_hash="a" * 64,
    )
    _add_transaction(
        db_session,
        category=expense,
        txn_at=datetime(2026, 10, 1),
        amount=Decimal("10"),
        dedupe_hash="b" * 64,
    )
    _add_transaction(
        db_session,
        category=expense,
        txn_at=datetime(2026, 10, 31, 23, 59, 59),
        amount=Decimal("20"),
        dedupe_hash="c" * 64,
    )
    _add_transaction(
        db_session,
        category=expense,
        txn_at=datetime(2026, 10, 15),
        amount=Decimal("500"),
        dedupe_hash="d" * 64,
        txn_type="own_transfer",
    )
    _add_transaction(
        db_session,
        category=expense,
        txn_at=datetime(2026, 10, 16),
        amount=Decimal("50"),
        dedupe_hash="e" * 64,
        direction="in",
    )
    _add_transaction(
        db_session,
        category=transfer,
        txn_at=datetime(2026, 10, 17),
        amount=Decimal("75"),
        dedupe_hash="f" * 64,
    )
    _add_transaction(
        db_session,
        category=expense,
        txn_at=datetime(2026, 11, 1),
        amount=Decimal("200"),
        dedupe_hash="g" * 64,
    )
    db_session.commit()

    actuals = budget_actuals_df(db_session, 2026, 10)
    actual_by_category = {
        row.category: row.actual_spending for row in actuals.itertuples(index=False)
    }
    assert actual_by_category == {
        "Planning expense": Decimal("30.00"),
        "No transactions": Decimal("0.00"),
    }


def test_existing_dedupe_constraint_prevents_duplicate_budget_spend(
    db_session: Session,
) -> None:
    expense = Category(name="Unique expense", kind="expense")
    db_session.add(expense)
    db_session.flush()
    when = datetime(2026, 10, 15, 12)
    _add_transaction(
        db_session,
        category=expense,
        txn_at=when,
        amount=Decimal("12.34"),
        dedupe_hash="h" * 64,
    )
    db_session.commit()

    _add_transaction(
        db_session,
        category=expense,
        txn_at=when,
        amount=Decimal("12.34"),
        dedupe_hash="h" * 64,
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    actuals = budget_actuals_df(db_session, 2026, 10)
    assert actuals.iloc[0]["actual_spending"] == Decimal("12.34")


def test_savings_goal_progress_and_overfunding() -> None:
    assert savings_goal_metrics(Decimal("1000"), Decimal("250")) == (
        Decimal("750"),
        Decimal("25"),
    )
    assert savings_goal_metrics(Decimal("100"), Decimal("125")) == (
        Decimal("0.00"),
        Decimal("125"),
    )


def test_goal_validation_rejects_invalid_name_amount_and_date() -> None:
    with pytest.raises(ValueError, match="name is required"):
        validate_goal_values(" ", "100", "0", date(2027, 1, 1))
    with pytest.raises(ValueError, match="Target amount"):
        validate_goal_values("Emergency fund", "0", "0", date(2027, 1, 1))
    with pytest.raises(ValueError, match="Current saved amount"):
        validate_goal_values("Emergency fund", "100", "-1", date(2027, 1, 1))
    with pytest.raises(ValueError, match="target date"):
        validate_goal_values(
            "Emergency fund", "100", "0", datetime(2027, 1, 1)
        )


def test_currency_options_follow_accounts_and_default_when_empty(
    db_session: Session,
) -> None:
    assert currency_options(db_session) == ["MNT"]
    _account_and_batch(db_session)
    assert currency_options(db_session) == ["USD"]
