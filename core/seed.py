from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session, init_db
from .models import Bank, Category, Rule


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
    ("Savings", "savings", 10),
]

DEFAULT_RULES = [
    (100, "description", "startswith", "EASYINFO FEE FOR"),
    (110, "description", "startswith", "CHARGES FOR PORD"),
    (120, "txn_type", "equals", "fee"),
]


def seed(session: Session) -> None:
    categories_by_name = {
        category.name: category
        for category in session.execute(select(Category)).scalars().all()
    }
    for name, kind, sort_order in DEFAULT_CATEGORIES:
        if name not in categories_by_name:
            category = Category(
                name=name,
                kind=kind,
                sort_order=sort_order,
                is_default=True,
            )
            session.add(category)
            categories_by_name[name] = category
    session.flush()

    if session.execute(
        select(Bank.id).where(Bank.code == "GOLOMT")
    ).scalar_one_or_none() is None:
        session.add(Bank(code="GOLOMT", name="Golomt Bank"))

    bank_fees = categories_by_name["Bank fees"]
    legacy_rules = session.execute(
        select(Rule).where(
            Rule.priority == 100,
            Rule.field == "description",
            Rule.match_type == "contains",
            Rule.pattern == "FEE",
            Rule.category_id == bank_fees.id,
        )
    ).scalars()
    for rule in legacy_rules:
        # This exact signature was the old built-in catch-all fee rule.
        rule.active = False

    for priority, field, match_type, pattern in DEFAULT_RULES:
        existing_rule = session.execute(
            select(Rule.id).where(
                Rule.priority == priority,
                Rule.field == field,
                Rule.match_type == match_type,
                Rule.pattern == pattern,
                Rule.category_id == bank_fees.id,
            )
        ).scalar_one_or_none()
        if existing_rule is None:
            session.add(
                Rule(
                    priority=priority,
                    field=field,
                    match_type=match_type,
                    pattern=pattern,
                    category_id=bank_fees.id,
                    active=True,
                )
            )

    session.commit()


def main() -> None:
    init_db()
    with get_session() as session:
        seed(session)


if __name__ == "__main__":
    main()
