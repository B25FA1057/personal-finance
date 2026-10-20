from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from .models import Account, Category, Counterparty, ImportBatch, Transaction


TRANSACTION_COLUMNS = [
    "id",
    "txn_at",
    "posted_at",
    "account_no",
    "direction",
    "amount",
    "signed_amount",
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
) -> pd.DataFrame:
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

    lower_bound = _as_datetime(date_from)
    upper_bound = _as_datetime(date_to, end=True)
    if lower_bound is not None:
        statement = statement.where(Transaction.txn_at >= lower_bound)
    if upper_bound is not None:
        if isinstance(date_to, datetime):
            statement = statement.where(Transaction.txn_at <= upper_bound)
        else:
            statement = statement.where(Transaction.txn_at < upper_bound)
    if account_ids is not None:
        statement = statement.where(
            Account.id.in_(account_ids) if account_ids else False
        )
    if category_ids is not None:
        selected_categories = Transaction.category_id.in_(category_ids) if category_ids else False
        if include_uncategorized:
            statement = statement.where(
                or_(selected_categories, Transaction.category_id.is_(None))
            )
        else:
            statement = statement.where(selected_categories)
    elif not include_uncategorized:
        statement = statement.where(Transaction.category_id.is_not(None))
    if txn_types is not None:
        statement = statement.where(Transaction.txn_type.in_(txn_types) if txn_types else False)
    if hide_fees:
        statement = statement.where(Transaction.txn_type != "fee")
    if search and search.strip():
        escaped_search = (
            search.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        term = f"%{escaped_search}%"
        statement = statement.where(
            or_(
                Transaction.description.ilike(term, escape="\\"),
                Transaction.merchant.ilike(term, escape="\\"),
                Account.account_no.ilike(term, escape="\\"),
                Counterparty.display_name.ilike(term, escape="\\"),
            )
        )

    statement = statement.order_by(Transaction.txn_at.desc(), Transaction.id.desc())
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


def batches_df(session: Session) -> pd.DataFrame:
    statement = (
        select(
            ImportBatch.id,
            ImportBatch.imported_at,
            ImportBatch.file_name,
            Account.account_no,
            ImportBatch.period_from,
            ImportBatch.period_to,
            ImportBatch.total_rows,
            ImportBatch.new_rows,
            ImportBatch.duplicate_rows,
        )
        .outerjoin(Account, Account.id == ImportBatch.account_id)
        .order_by(ImportBatch.imported_at.desc(), ImportBatch.id.desc())
    )
    return pd.DataFrame(
        session.execute(statement).all(),
        columns=[
            "id",
            "imported_at",
            "file_name",
            "account_no",
            "period_from",
            "period_to",
            "total_rows",
            "new_rows",
            "duplicate_rows",
        ],
    )
