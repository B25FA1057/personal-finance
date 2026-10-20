from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Bank, Category, Counterparty, OwnAccount, Rule


DEFAULT_CATEGORIES = [
    ("Income", "income", 10),
    ("Salary", "income", 20),
    ("Bank fees", "expense", 10),
    ("Dining & cafés", "expense", 20),
    ("Groceries", "expense", 30),
    ("Shopping", "expense", 40),
    ("Entertainment", "expense", 50),
    ("Own account transfer", "transfer", 10),
    ("Transfers", "transfer", 20),
    ("Other income", "income", 30),
    ("Cash withdrawals", "expense", 60),
]


def seed(session: Session) -> None:
    existing = session.execute(select(Category.name)).scalars().all()
    if not existing:
        for name, kind, sort_order in DEFAULT_CATEGORIES:
            session.add(Category(name=name, kind=kind, sort_order=sort_order, is_default=True))

    if not session.execute(select(Bank.id).where(Bank.code == "GOLOMT")).scalar_one_or_none():
        session.add(Bank(code="GOLOMT", name="Golomt Bank"))

    if not session.execute(select(OwnAccount.id).where(OwnAccount.account_no == "5000000001")).scalar_one_or_none():
        session.add(OwnAccount(account_no="5000000001", label="Main account"))

    if not session.execute(select(Counterparty.id).where(Counterparty.key == "M:SEZIS BUF")).scalar_one_or_none():
        session.add(Counterparty(key="M:SEZIS BUF", kind="merchant", display_name="SEZIS BUF"))

    for category in session.execute(select(Category)).scalars().all():
        if category.name == "Bank fees" and category.id:
            fee_rule = session.execute(select(Rule.id).where(Rule.pattern == "FEE")).scalar_one_or_none()
            if not fee_rule:
                session.add(Rule(priority=100, field="description", match_type="contains", pattern="FEE", category_id=category.id, active=True))
                break

    session.commit()
