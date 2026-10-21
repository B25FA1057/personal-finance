from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy import and_, case, extract, func, or_, select
from sqlalchemy.orm import Session

from .models import (
    Account,
    Bank,
    Category,
    Counterparty,
    ImportBatch,
    ImportCategorizationSummary,
    MonthlyBudget,
    SavingsGoal,
    Transaction,
)


TRANSACTION_COLUMNS = [
    "id",
    "txn_at",
    "posted_at",
    "account_no",
    "direction",
    "amount",
    "signed_amount",
    "category_id",
    "description",
    "merchant",
    "counterparty",
    "txn_type",
    "category",
    "category_kind",
    "category_source",
    "note",
]


def categories_df(session: Session) -> pd.DataFrame:
    statement = select(
        Category.id, Category.name, Category.kind, Category.sort_order
    ).order_by(Category.kind, Category.sort_order, Category.id)
    return pd.DataFrame(
        session.execute(statement).all(),
        columns=["id", "name", "kind", "sort_order"],
    )


def category_options(session: Session) -> list[str]:
    return categories_df(session)["name"].tolist()


def category_id_by_name(session: Session) -> dict[str, int]:
    return {
        row.name: int(row.id)
        for row in session.execute(select(Category.id, Category.name)).all()
    }


def currency_options(session: Session) -> list[str]:
    currencies = set(session.scalars(select(Account.currency).distinct()).all())
    currencies.update(session.scalars(select(MonthlyBudget.currency).distinct()).all())
    currencies.update(session.scalars(select(SavingsGoal.currency).distinct()).all())
    if not currencies:
        currencies.add("MNT")
    return sorted(currencies)


def budget_actuals_df(session: Session, year: int, month: int) -> pd.DataFrame:
    month_start = date(year, month, 1)
    next_month = (
        date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    )
    actual_spending = func.coalesce(func.sum(Transaction.amount), 0).label(
        "actual_spending"
    )
    statement = (
        select(Category.id.label("category_id"), Category.name, actual_spending)
        .select_from(Category)
        .outerjoin(
            Transaction,
            and_(
                Transaction.category_id == Category.id,
                Transaction.txn_at >= month_start,
                Transaction.txn_at < next_month,
                Transaction.direction == "out",
                Transaction.txn_type != "own_transfer",
            ),
        )
        .where(Category.kind == "expense")
        .group_by(Category.id, Category.name, Category.sort_order)
        .order_by(Category.sort_order, Category.name, Category.id)
    )
    rows = session.execute(statement).all()
    return pd.DataFrame(
        [
            {
                "category_id": row.category_id,
                "category": row.name,
                "actual_spending": Decimal(row.actual_spending),
            }
            for row in rows
        ],
        columns=["category_id", "category", "actual_spending"],
    )


def unreviewed_counterparties_df(
    session: Session, include_reviewed: bool = False
) -> pd.DataFrame:
    total_out = func.coalesce(
        func.sum(case((Transaction.direction == "out", Transaction.amount), else_=0)), 0
    )
    total_in = func.coalesce(
        func.sum(case((Transaction.direction == "in", Transaction.amount), else_=0)), 0
    )
    statement = (
        select(
            Counterparty.id,
            Counterparty.display_name,
            Counterparty.kind,
            Counterparty.key,
            func.count(Transaction.id).label("txn_count"),
            total_out.label("total_out"),
            total_in.label("total_in"),
            func.max(Transaction.description).label("sample_description"),
            Counterparty.default_category_id,
        )
        .select_from(Counterparty)
        .outerjoin(Transaction, Transaction.counterparty_id == Counterparty.id)
        .where(Counterparty.kind.notin_(["bank_fee", "own_account"]))
        .group_by(Counterparty.id)
        .order_by((total_out + total_in).desc(), Counterparty.display_name, Counterparty.id)
    )
    if not include_reviewed:
        statement = statement.where(Counterparty.reviewed.is_(False))

    rows = session.execute(statement).all()
    if not rows:
        return pd.DataFrame(
            columns=[
                "id",
                "display_name",
                "kind",
                "key",
                "txn_count",
                "total_out",
                "total_in",
                "sample_description",
                "current_category",
                "default_category",
            ]
        )

    counterparty_ids = [row.id for row in rows]
    category_usage = session.execute(
        select(
            Transaction.counterparty_id,
            Category.name,
            func.count(Transaction.id).label("category_count"),
            Category.id,
        )
        .join(Category, Category.id == Transaction.category_id)
        .where(Transaction.counterparty_id.in_(counterparty_ids))
        .group_by(Transaction.counterparty_id, Category.id, Category.name)
        .order_by(
            Transaction.counterparty_id,
            func.count(Transaction.id).desc(),
            Category.id,
        )
    ).all()
    most_common_category: dict[int, str] = {}
    for usage in category_usage:
        most_common_category.setdefault(usage.counterparty_id, usage.name)

    default_ids = {row.default_category_id for row in rows if row.default_category_id is not None}
    category_names = (
        dict(session.execute(select(Category.id, Category.name).where(Category.id.in_(default_ids))).all())
        if default_ids
        else {}
    )

    return pd.DataFrame(
        [
            {
                "id": row.id,
                "display_name": row.display_name,
                "kind": row.kind,
                "key": row.key,
                "txn_count": row.txn_count,
                "total_out": row.total_out or Decimal("0.00"),
                "total_in": row.total_in or Decimal("0.00"),
                "sample_description": row.sample_description,
                "current_category": most_common_category.get(row.id),
                "default_category": category_names.get(row.default_category_id),
            }
            for row in rows
        ]
    )


def _as_datetime(value: date | datetime | None, *, end: bool = False) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if end:
        return datetime.combine(value + timedelta(days=1), time.min)
    return datetime.combine(value, time.min)


def merchant_options(session: Session) -> list[str]:
    merchant = func.coalesce(
        func.nullif(func.trim(Transaction.merchant), ""),
        Counterparty.display_name,
    )
    statement = (
        select(merchant.label("merchant"))
        .select_from(Transaction)
        .outerjoin(Counterparty, Counterparty.id == Transaction.counterparty_id)
        .where(merchant.is_not(None))
        .distinct()
        .order_by(merchant)
    )
    return list(session.execute(statement).scalars().all())


def _transaction_filters(
    date_from: date | datetime | None,
    date_to: date | datetime | None,
    account_ids: list[int] | None,
    category_ids: list[int] | None,
    include_uncategorized: bool,
    txn_types: list[str] | None,
    search: str | None,
    merchant_names: list[str] | None,
    hide_fees: bool,
) -> list:
    conditions = []
    lower_bound = _as_datetime(date_from)
    upper_bound = _as_datetime(date_to, end=True)
    if lower_bound is not None:
        conditions.append(Transaction.txn_at >= lower_bound)
    if upper_bound is not None:
        if isinstance(date_to, datetime):
            conditions.append(Transaction.txn_at <= upper_bound)
        else:
            conditions.append(Transaction.txn_at < upper_bound)
    if account_ids is not None:
        conditions.append(Account.id.in_(account_ids) if account_ids else False)
    if category_ids is not None:
        selected_categories = (
            Transaction.category_id.in_(category_ids) if category_ids else False
        )
        if include_uncategorized:
            conditions.append(
                or_(selected_categories, Transaction.category_id.is_(None))
            )
        else:
            conditions.append(selected_categories)
    elif not include_uncategorized:
        conditions.append(Transaction.category_id.is_not(None))
    if txn_types is not None:
        conditions.append(Transaction.txn_type.in_(txn_types) if txn_types else False)
    if merchant_names is not None:
        merchant = func.coalesce(
            func.nullif(func.trim(Transaction.merchant), ""),
            Counterparty.display_name,
        )
        conditions.append(merchant.in_(merchant_names) if merchant_names else False)
    if hide_fees:
        conditions.append(Transaction.txn_type != "fee")
    if search and search.strip():
        escaped_search = (
            search.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        term = f"%{escaped_search}%"
        conditions.append(
            or_(
                Transaction.description.ilike(term, escape="\\"),
                Transaction.merchant.ilike(term, escape="\\"),
                Account.account_no.ilike(term, escape="\\"),
                Counterparty.display_name.ilike(term, escape="\\"),
            )
        )
    return conditions


def transactions_df(
    session: Session,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
    account_ids: list[int] | None = None,
    category_ids: list[int] | None = None,
    include_uncategorized: bool = True,
    txn_types: list[str] | None = None,
    search: str | None = None,
    hide_fees: bool = False,
    merchant_names: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> pd.DataFrame:
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive integer")
    if offset < 0:
        raise ValueError("offset must not be negative")

    signed_amount = case(
        (Transaction.direction == "in", Transaction.amount),
        else_=-Transaction.amount,
    )
    statement = (
        select(
            Transaction.id,
            Transaction.txn_at,
            Transaction.posted_at,
            Account.account_no,
            Transaction.direction,
            Transaction.amount,
            signed_amount.label("signed_amount"),
            Transaction.category_id,
            Transaction.description,
            Transaction.merchant,
            Counterparty.display_name.label("counterparty"),
            Transaction.txn_type,
            Category.name.label("category"),
            Category.kind.label("category_kind"),
            Transaction.category_source,
            Transaction.note,
        )
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(Counterparty, Counterparty.id == Transaction.counterparty_id)
        .outerjoin(Category, Category.id == Transaction.category_id)
    )
    conditions = _transaction_filters(
        date_from,
        date_to,
        account_ids,
        category_ids,
        include_uncategorized,
        txn_types,
        search,
        merchant_names,
        hide_fees,
    )
    if conditions:
        statement = statement.where(*conditions)
    statement = statement.order_by(Transaction.txn_at.desc(), Transaction.id.desc())
    if limit is not None:
        statement = statement.limit(limit)
    if offset:
        statement = statement.offset(offset)
    rows = session.execute(statement).mappings().all()
    if not rows:
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)
    return pd.DataFrame(
        [
            {
                **row,
                "amount": Decimal(row["amount"]),
                "signed_amount": Decimal(row["signed_amount"]),
            }
            for row in rows
        ],
        columns=TRANSACTION_COLUMNS,
    )


def transaction_count(
    session: Session,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
    account_ids: list[int] | None = None,
    category_ids: list[int] | None = None,
    include_uncategorized: bool = True,
    txn_types: list[str] | None = None,
    search: str | None = None,
    hide_fees: bool = False,
    merchant_names: list[str] | None = None,
) -> int:
    statement = (
        select(func.count(Transaction.id))
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(Counterparty, Counterparty.id == Transaction.counterparty_id)
    )
    conditions = _transaction_filters(
        date_from,
        date_to,
        account_ids,
        category_ids,
        include_uncategorized,
        txn_types,
        search,
        merchant_names,
        hide_fees,
    )
    if conditions:
        statement = statement.where(*conditions)
    return int(session.scalar(statement) or 0)


def _dashboard_date_filters(
    date_from: date | datetime | None, date_to: date | datetime | None
) -> list:
    conditions = []
    lower_bound = _as_datetime(date_from)
    upper_bound = _as_datetime(date_to, end=True)
    if lower_bound is not None:
        conditions.append(Transaction.txn_at >= lower_bound)
    if upper_bound is not None:
        if isinstance(date_to, datetime):
            conditions.append(Transaction.txn_at <= upper_bound)
        else:
            conditions.append(Transaction.txn_at < upper_bound)
    conditions.extend(
        [
            Transaction.txn_type != "own_transfer",
            or_(Category.kind != "transfer", Category.kind.is_(None)),
        ]
    )
    return conditions


def monthly_cashflow_df(
    session: Session,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
) -> pd.DataFrame:
    year = extract("year", Transaction.txn_at).label("year")
    month = extract("month", Transaction.txn_at).label("month")
    income = func.coalesce(
        func.sum(
            case(
                (
                    (Transaction.direction == "in") & (Category.kind == "income"),
                    Transaction.amount,
                ),
                else_=0,
            )
        ),
        0,
    ).label("income")
    expenses = func.coalesce(
        func.sum(
            case(
                (
                    (Transaction.direction == "out") & (Category.kind == "expense"),
                    Transaction.amount,
                ),
                (
                    (Transaction.direction == "in") & (Category.kind == "expense"),
                    -Transaction.amount,
                ),
                else_=0,
            )
        ),
        0,
    ).label("expenses")
    savings = func.coalesce(
        func.sum(
            case(
                (
                    (Transaction.direction == "out") & (Category.kind == "savings"),
                    Transaction.amount,
                ),
                else_=0,
            )
        ),
        0,
    ).label("savings")
    uncategorized_income = func.coalesce(
        func.sum(
            case(
                (
                    (Transaction.direction == "in")
                    & Transaction.category_id.is_(None),
                    Transaction.amount,
                ),
                else_=0,
            )
        ),
        0,
    ).label("uncategorized_income")
    statement = (
        select(year, month, income, expenses, savings, uncategorized_income)
        .select_from(Transaction)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(*_dashboard_date_filters(date_from, date_to))
        .group_by(year, month)
        .order_by(year, month)
    )
    rows = session.execute(statement).all()
    return pd.DataFrame(
        [
            {
                "month": f"{int(row.year):04d}-{int(row.month):02d}",
                "income": Decimal(row.income),
                "expenses": Decimal(row.expenses),
                "savings": Decimal(row.savings),
                "uncategorized_income": Decimal(row.uncategorized_income),
                "net_cash_flow": (
                    Decimal(row.income)
                    + Decimal(row.uncategorized_income)
                    - Decimal(row.expenses)
                    - Decimal(row.savings)
                ),
            }
            for row in rows
        ],
        columns=[
            "month",
            "income",
            "expenses",
            "savings",
            "uncategorized_income",
            "net_cash_flow",
        ],
    )


def uncategorized_cashflow_totals(
    session: Session,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
) -> tuple[Decimal, Decimal]:
    incoming = func.coalesce(
        func.sum(
            case(
                (Transaction.direction == "in", Transaction.amount),
                else_=0,
            )
        ),
        0,
    ).label("incoming")
    outgoing = func.coalesce(
        func.sum(
            case(
                (Transaction.direction == "out", Transaction.amount),
                else_=0,
            )
        ),
        0,
    ).label("outgoing")
    statement = (
        select(incoming, outgoing)
        .select_from(Transaction)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(
            *_dashboard_date_filters(date_from, date_to),
            Transaction.category_id.is_(None),
        )
    )
    row = session.execute(statement).one()
    return Decimal(row.incoming), Decimal(row.outgoing)


def internal_transfer_totals(
    session: Session,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
) -> tuple[Decimal, Decimal]:
    incoming = func.coalesce(
        func.sum(
            case(
                (Transaction.direction == "in", Transaction.amount),
                else_=0,
            )
        ),
        0,
    ).label("incoming")
    outgoing = func.coalesce(
        func.sum(
            case(
                (Transaction.direction == "out", Transaction.amount),
                else_=0,
            )
        ),
        0,
    ).label("outgoing")
    conditions = [
        or_(Transaction.txn_type == "own_transfer", Category.kind == "transfer")
    ]
    lower_bound = _as_datetime(date_from)
    upper_bound = _as_datetime(date_to, end=True)
    if lower_bound is not None:
        conditions.append(Transaction.txn_at >= lower_bound)
    if upper_bound is not None:
        if isinstance(date_to, datetime):
            conditions.append(Transaction.txn_at <= upper_bound)
        else:
            conditions.append(Transaction.txn_at < upper_bound)
    statement = (
        select(incoming, outgoing)
        .select_from(Transaction)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(*conditions)
    )
    row = session.execute(statement).one()
    return Decimal(row.incoming), Decimal(row.outgoing)


def expense_categories_df(
    session: Session,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
) -> pd.DataFrame:
    statement = (
        select(
            Category.id.label("category_id"),
            Category.name.label("category"),
            func.sum(
                case(
                    (Transaction.direction == "out", Transaction.amount),
                    else_=-Transaction.amount,
                )
            ).label("amount"),
        )
        .select_from(Transaction)
        .join(Category, Category.id == Transaction.category_id)
        .where(
            *_dashboard_date_filters(date_from, date_to),
            Transaction.direction.in_(["in", "out"]),
            Category.kind == "expense",
        )
        .group_by(Category.id, Category.name)
        .order_by(func.sum(
            case(
                (Transaction.direction == "out", Transaction.amount),
                else_=-Transaction.amount,
            )
        ).desc(), Category.name)
    )
    return pd.DataFrame(
        [
            {
                "category_id": row.category_id,
                "category": row.category,
                "amount": Decimal(row.amount),
            }
            for row in session.execute(statement).all()
        ],
        columns=["category_id", "category", "amount"],
    )


def expense_trend_df(
    session: Session,
    category_id: int | None = None,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
) -> pd.DataFrame:
    year = extract("year", Transaction.txn_at).label("year")
    month = extract("month", Transaction.txn_at).label("month")
    net_expenses = func.sum(
        case(
            (Transaction.direction == "out", Transaction.amount),
            else_=-Transaction.amount,
        )
    )
    statement = (
        select(year, month, net_expenses.label("amount"))
        .select_from(Transaction)
        .join(Category, Category.id == Transaction.category_id)
        .where(
            *_dashboard_date_filters(date_from, date_to),
            Transaction.direction.in_(["in", "out"]),
            Category.kind == "expense",
        )
    )
    if category_id is not None:
        statement = statement.where(Category.id == category_id)
    statement = statement.group_by(year, month).order_by(year, month)
    return pd.DataFrame(
        [
            {
                "month": f"{int(row.year):04d}-{int(row.month):02d}",
                "amount": Decimal(row.amount),
            }
            for row in session.execute(statement).all()
        ],
        columns=["month", "amount"],
    )


def top_expenses_df(
    session: Session,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
    limit: int = 10,
) -> pd.DataFrame:
    if limit < 1:
        raise ValueError("limit must be a positive integer")
    statement = (
        select(
            Transaction.txn_at,
            Transaction.amount,
            Transaction.merchant,
            Counterparty.display_name.label("counterparty"),
            Transaction.description,
            Category.name.label("category"),
        )
        .select_from(Transaction)
        .join(Category, Category.id == Transaction.category_id)
        .outerjoin(Counterparty, Counterparty.id == Transaction.counterparty_id)
        .where(
            *_dashboard_date_filters(date_from, date_to),
            Transaction.direction == "out",
            Category.kind == "expense",
        )
        .order_by(Transaction.amount.desc(), Transaction.txn_at.desc())
        .limit(limit)
    )
    return pd.DataFrame(
        session.execute(statement).all(),
        columns=["txn_at", "amount", "merchant", "counterparty", "description", "category"],
    )


def top_merchant_expenses_df(
    session: Session,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
    limit: int = 10,
) -> pd.DataFrame:
    if limit < 1:
        raise ValueError("limit must be a positive integer")
    merchant = func.coalesce(
        func.nullif(func.trim(Transaction.merchant), ""),
        Counterparty.display_name,
        "Unspecified",
    ).label("payee")
    net_expenses = func.sum(
        case(
            (Transaction.direction == "out", Transaction.amount),
            else_=-Transaction.amount,
        )
    )
    statement = (
        select(merchant, net_expenses.label("amount"))
        .select_from(Transaction)
        .join(Category, Category.id == Transaction.category_id)
        .outerjoin(Counterparty, Counterparty.id == Transaction.counterparty_id)
        .where(
            *_dashboard_date_filters(date_from, date_to),
            Transaction.direction.in_(["in", "out"]),
            Transaction.txn_type != "fee",
            Category.kind == "expense",
        )
        .group_by(merchant)
        .having(net_expenses > 0)
        .order_by(net_expenses.desc(), merchant)
        .limit(limit)
    )
    return pd.DataFrame(
        [
            {"payee": row.payee, "amount": Decimal(row.amount)}
            for row in session.execute(statement).all()
        ],
        columns=["payee", "amount"],
    )


def batches_df(session: Session) -> pd.DataFrame:
    statement = (
        select(
            ImportBatch.id,
            ImportBatch.imported_at,
            ImportBatch.file_name,
            Bank.code.label("bank_code"),
            ImportBatch.period_from,
            ImportBatch.period_to,
            ImportBatch.total_rows,
            ImportBatch.new_rows,
            ImportBatch.duplicate_rows,
            func.coalesce(ImportCategorizationSummary.status, "legacy").label("status"),
            func.coalesce(ImportCategorizationSummary.ai_status, "disabled").label(
                "ai_status"
            ),
            func.coalesce(ImportCategorizationSummary.rules_classified, 0).label(
                "rules_classified"
            ),
            func.coalesce(ImportCategorizationSummary.counterparties_classified, 0).label(
                "counterparties_classified"
            ),
            func.coalesce(ImportCategorizationSummary.ai_suggestions, 0).label(
                "ai_suggestions"
            ),
            func.coalesce(ImportCategorizationSummary.pending_review, 0).label(
                "pending_review"
            ),
        )
        .outerjoin(Account, Account.id == ImportBatch.account_id)
        .outerjoin(Bank, Bank.id == Account.bank_id)
        .outerjoin(
            ImportCategorizationSummary,
            ImportCategorizationSummary.batch_id == ImportBatch.id,
        )
        .order_by(ImportBatch.imported_at.desc(), ImportBatch.id.desc())
    )
    return pd.DataFrame(
        session.execute(statement).all(),
        columns=[
            "id",
            "imported_at",
            "file_name",
            "bank_code",
            "period_from",
            "period_to",
            "total_rows",
            "new_rows",
            "duplicate_rows",
            "status",
            "ai_status",
            "rules_classified",
            "counterparties_classified",
            "ai_suggestions",
            "pending_review",
        ],
    )
