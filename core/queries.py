from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from .models import Account, Category, Counterparty, ImportBatch, Transaction


def categories_df(session: Session) -> pd.DataFrame:
    stmt = select(Category.id, Category.name, Category.kind, Category.sort_order).order_by(Category.kind, Category.sort_order, Category.id)
    rows = session.execute(stmt).all()
    return pd.DataFrame(rows, columns=["id", "name", "kind", "sort_order"])


def category_options(session: Session) -> list[str]:
    df = categories_df(session)
    return df["name"].tolist()


def category_id_by_name(session: Session) -> dict[str, int]:
    return {row.name: int(row.id) for row in session.execute(select(Category.id, Category.name)).all()}


def unreviewed_counterparties_df(session: Session, include_reviewed: bool = False) -> pd.DataFrame:
    stmt = select(
        Counterparty.id,
        Counterparty.display_name,
        Counterparty.kind,
        Counterparty.key,
        func.count(Transaction.id).label("txn_count"),
        func.sum(case((Transaction.direction == "out", Transaction.amount), else_=0)).label("total_out"),
        func.sum(case((Transaction.direction == "in", Transaction.amount), else_=0)).label("total_in"),
        func.max(Transaction.description).label("sample_description"),
        func.max(Category.name).label("current_category"),
        Counterparty.default_category_id,
    ).select_from(Counterparty).outerjoin(Transaction, Transaction.counterparty_id == Counterparty.id).outerjoin(Category, Category.id == Transaction.category_id)

    if not include_reviewed:
        stmt = stmt.where(Counterparty.reviewed.is_(False))
    stmt = stmt.where(Counterparty.kind.notin_(["bank_fee", "own_account"]))
    stmt = stmt.group_by(Counterparty.id, Counterparty.display_name, Counterparty.kind, Counterparty.key, Counterparty.default_category_id)
    stmt = stmt.order_by((func.coalesce(func.sum(case((Transaction.direction == "out", Transaction.amount), else_=0)), 0) + func.coalesce(func.sum(case((Transaction.direction == "in", Transaction.amount), else_=0)), 0)).desc())

    rows = session.execute(stmt).all()
    data = []
    for row in rows:
        default_name = session.execute(select(Category.name).where(Category.id == row.default_category_id)).scalar_one_or_none()
        data.append(
            {
                "id": row.id,
                "display_name": row.display_name,
                "kind": row.kind,
                "key": row.key,
                "txn_count": row.txn_count,
                "total_out": float(row.total_out or 0),
                "total_in": float(row.total_in or 0),
                "sample_description": row.sample_description,
                "current_category": row.current_category,
                "default_category": default_name,
            }
        )
    return pd.DataFrame(data)


def transactions_df(
    session: Session,
    date_from,
    date_to,
    account_ids=None,
    category_ids=None,
    include_uncategorized: bool = True,
    txn_types=None,
    search: str | None = None,
    hide_fees: bool = False,
) -> pd.DataFrame:
    stmt = select(
        Transaction.id,
        Transaction.txn_at,
        Transaction.posted_at,
        Account.account_no,
        Transaction.direction,
        Transaction.amount,
        case((Transaction.direction == "in", Transaction.amount), else_=(Transaction.amount * -1)).label("signed_amount"),
        Transaction.description,
        Transaction.merchant,
        Transaction.counterparty_id,
        Transaction.txn_type,
        Category.name.label("category"),
        Category.kind.label("category_kind"),
        Transaction.category_source,
        Transaction.note,
    ).join(Account, Account.id == Transaction.account_id).outerjoin(Category, Category.id == Transaction.category_id)

    stmt = stmt.where(Transaction.txn_at >= date_from, Transaction.txn_at <= date_to)

    if account_ids:
        stmt = stmt.where(Account.id.in_(account_ids))
    if category_ids:
        if include_uncategorized:
            stmt = stmt.where(or_(Transaction.category_id.in_(category_ids), Transaction.category_id.is_(None)))
        else:
            stmt = stmt.where(Transaction.category_id.in_(category_ids))
    elif not include_uncategorized:
        stmt = stmt.where(Transaction.category_id.is_not(None))
    if txn_types:
        stmt = stmt.where(Transaction.txn_type.in_(txn_types))
    if hide_fees:
        stmt = stmt.where(Transaction.txn_type != "fee")
    if search:
        s = f"%{search}%"
        stmt = stmt.where(or_(Transaction.description.ilike(s), Transaction.merchant.ilike(s), Account.account_no.ilike(s)))

    stmt = stmt.order_by(Transaction.txn_at.desc())
    rows = session.execute(stmt).all()
    data = []
    for row in rows:
        data.append(
            {
                "id": row.id,
                "txn_at": row.txn_at,
                "posted_at": row.posted_at,
                "account_no": row.account_no,
                "direction": row.direction,
                "amount": float(row.amount),
                "signed_amount": float(row.signed_amount),
                "description": row.description,
                "merchant": row.merchant,
                "counterparty": row.counterparty_id,
                "txn_type": row.txn_type,
                "category": row.category,
                "category_kind": row.category_kind,
                "category_source": row.category_source,
                "note": row.note,
            }
        )
    return pd.DataFrame(data)


def batches_df(session: Session) -> pd.DataFrame:
    stmt = select(
        ImportBatch.id,
        ImportBatch.imported_at,
        ImportBatch.file_name,
        Account.account_no,
        ImportBatch.period_from,
        ImportBatch.period_to,
        ImportBatch.total_rows,
        ImportBatch.new_rows,
        ImportBatch.duplicate_rows,
    ).join(Account, Account.id == ImportBatch.account_id)
    stmt = stmt.order_by(ImportBatch.imported_at.desc())
    rows = session.execute(stmt).all()
    return pd.DataFrame(rows, columns=["id", "imported_at", "file_name", "account_no", "period_from", "period_to", "total_rows", "new_rows", "duplicate_rows"])
