from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from core.classify import classify_one
from core.db import get_session
from core.models import (
    Account,
    Bank,
    Category,
    Counterparty,
    ImportBatch,
    OwnAccount,
    Rule,
    Transaction,
)
from core.seed import (
    DEFAULT_CATEGORIES,
    DEFAULT_RULES,
    main,
    seed,
    seed_default_categories,
)


@pytest.fixture
def db_session(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    database_url = f"sqlite:///{tmp_path.as_posix()}/seed.db"
    engine = create_engine(database_url)
    from core.db import Base

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    monkeypatch.setattr("core.config.DATABASE_URL", database_url)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_main_initializes_fresh_database_and_seeds_defaults(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{tmp_path.as_posix()}/fresh.db"
    monkeypatch.setattr("core.config.DATABASE_URL", database_url)

    main()

    with get_session(database_url) as session:
        categories = session.execute(select(Category)).scalars().all()
        banks = session.execute(select(Bank)).scalars().all()
        rules = session.execute(select(Rule)).scalars().all()
        assert len(categories) == len(DEFAULT_CATEGORIES)
        assert {category.name for category in categories} == {
            name for name, _, _ in DEFAULT_CATEGORIES
        }
        assert len(banks) == 1
        assert len(rules) == len(DEFAULT_RULES)
        assert session.scalar(select(func.count(OwnAccount.id))) == 0
        assert session.scalar(select(func.count(Counterparty.id))) == 0


def test_seeding_is_idempotent_and_preserves_user_data(db_session: Session) -> None:
    custom_category = Category(
        name="Groceries",
        kind="savings",
        sort_order=777,
        is_default=False,
    )
    db_session.add(custom_category)
    db_session.commit()
    seed(db_session)
    bank_fees = db_session.execute(
        select(Category).where(Category.name == "Bank fees")
    ).scalar_one()
    custom_rule = Rule(
        priority=5,
        field="description",
        match_type="contains",
        pattern="CUSTOM MATCH",
        category_id=bank_fees.id,
        active=True,
    )
    legacy_rule = Rule(
        priority=100,
        field="description",
        match_type="contains",
        pattern="FEE",
        category_id=bank_fees.id,
        active=True,
    )
    db_session.add_all([custom_category, custom_rule, legacy_rule])
    db_session.commit()

    seed(db_session)
    seed(db_session)

    assert db_session.scalar(
        select(func.count(Category.id)).where(Category.name == "Groceries")
    ) == 1
    assert custom_category.kind == "savings"
    assert custom_category.sort_order == 777
    assert custom_category.is_default is False
    assert custom_rule.active is True
    assert custom_rule.priority == 5
    assert legacy_rule.active is False
    assert db_session.scalar(
        select(func.count(Rule.id)).where(
            Rule.category_id == bank_fees.id,
            Rule.field == "txn_type",
            Rule.match_type == "equals",
            Rule.pattern == "fee",
        )
    ) == 1


def test_default_category_initialization_preserves_user_categories_and_transactions(
    db_session: Session,
) -> None:
    custom_category = Category(
        name="Groceries",
        kind="savings",
        sort_order=777,
        is_default=False,
    )
    bank = Bank(code="TEST", name="Test Bank")
    db_session.add_all([custom_category, bank])
    db_session.flush()
    account = Account(account_no="TEST-ACCOUNT", bank_id=bank.id)
    db_session.add(account)
    db_session.flush()
    batch = ImportBatch(file_sha256="a" * 64, account_id=account.id)
    db_session.add(batch)
    db_session.flush()
    transaction = Transaction(
        account_id=account.id,
        batch_id=batch.id,
        posted_at=datetime(2026, 1, 1),
        txn_at=datetime(2026, 1, 1),
        amount=Decimal("125.00"),
        direction="out",
        description="Existing manually categorized transaction",
        txn_type="purchase",
        category_id=custom_category.id,
        category_source="manual",
        raw={},
        dedupe_hash="b" * 64,
    )
    db_session.add(transaction)
    db_session.commit()
    original_transaction = tuple(
        getattr(transaction, column.key) for column in Transaction.__table__.columns
    )

    added, skipped = seed_default_categories(db_session)
    db_session.commit()
    assert (added, skipped) == (
        len(DEFAULT_CATEGORIES) - 1,
        1,
    )

    added_again, skipped_again = seed_default_categories(db_session)
    db_session.commit()
    assert (added_again, skipped_again) == (0, len(DEFAULT_CATEGORIES))
    assert db_session.scalar(
        select(func.count(Category.id)).where(Category.name.ilike("groceries"))
    ) == 1
    assert (
        custom_category.kind,
        custom_category.sort_order,
        custom_category.is_default,
    ) == ("savings", 777, False)
    assert tuple(
        getattr(transaction, column.key) for column in Transaction.__table__.columns
    ) == original_transaction
    assert db_session.scalar(select(func.count(Transaction.id))) == 1


def test_seeded_fee_rules_match_specific_descriptions_only(db_session: Session) -> None:
    seed(db_session)
    bank_fees = db_session.execute(
        select(Category).where(Category.name == "Bank fees")
    ).scalar_one()
    rules = db_session.execute(
        select(Rule).where(Rule.active.is_(True)).order_by(Rule.priority, Rule.id)
    ).scalars().all()

    def classify(description: str, txn_type: str = "other") -> tuple[int | None, str]:
        txn = Transaction(
            description=description,
            txn_type=txn_type,
            category_source="none",
            category_id=None,
        )
        return classify_one(txn, rules)

    assert classify("EASYINFO fee for REF-123") == (bank_fees.id, "rule")
    assert classify("Charges for PORD customer payment") == (bank_fees.id, "rule")
    assert classify("QPAY transfer fee", txn_type="fee") == (bank_fees.id, "rule")
    assert classify("COFFEE SHOP PURCHASE") == (None, "none")
    assert classify("Feedback refund") == (None, "none")

    manual = Category(name="Manual category", kind="expense")
    db_session.add(manual)
    db_session.flush()
    txn = Transaction(
        description="EASYINFO fee for REF-123",
        txn_type="fee",
        category_id=manual.id,
        category_source="manual",
    )
    assert classify_one(txn, rules) == (manual.id, "manual")
