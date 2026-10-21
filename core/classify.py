from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_categorization import refresh_import_summary
from .models import CategorizationSuggestion, Category, Counterparty, Rule, Transaction


def _supersede_pending_suggestion(session: Session, txn_id: int) -> None:
    suggestion = session.get(CategorizationSuggestion, txn_id)
    if suggestion is not None and suggestion.status == "pending":
        suggestion.status = "superseded"


def rule_matches(rule: Rule, txn: Transaction) -> bool:
    if rule.field == "description":
        value = txn.description
    elif rule.field == "merchant":
        value = txn.merchant
    elif rule.field == "counterparty_account":
        value = txn.counterparty_account
    elif rule.field == "txn_type":
        value = txn.txn_type
    else:
        value = None

    if value is None:
        return False

    target = str(value).strip()
    pattern = str(rule.pattern).strip()
    if rule.match_type == "contains":
        return pattern.lower() in target.lower()
    if rule.match_type == "startswith":
        return target.lower().startswith(pattern.lower())
    if rule.match_type == "equals":
        return target.lower() == pattern.lower()
    if rule.match_type == "regex":
        try:
            return re.search(pattern, target, re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def classify_one(txn: Transaction, rules: list[Rule]) -> tuple[int | None, str]:
    if txn.category_source == "manual":
        return txn.category_id, "manual"

    if txn.counterparty is not None and txn.counterparty.default_category_id is not None:
        return txn.counterparty.default_category_id, "counterparty"

    for rule in rules:
        if rule.active and rule_matches(rule, txn):
            return rule.category_id, "rule"

    return None, "none"


def classify_transactions(session: Session, txn_ids: list[int] | None = None) -> int:
    if txn_ids is None:
        stmt = select(Transaction)
    else:
        stmt = select(Transaction).where(Transaction.id.in_(txn_ids))

    txns = session.execute(stmt).scalars().all()
    active_rules = session.execute(
        select(Rule)
        .where(Rule.active.is_(True))
        .order_by(Rule.priority.asc(), Rule.id.asc())
    ).scalars().all()

    changed = 0
    batch_ids = {txn.batch_id for txn in txns}
    for txn in txns:
        if txn.txn_type == "own_transfer":
            _supersede_pending_suggestion(session, txn.id)
        if txn.category_source == "manual":
            continue
        new_category_id, new_source = classify_one(txn, active_rules)
        if txn.category_id != new_category_id or txn.category_source != new_source:
            txn.category_id = new_category_id
            txn.category_source = new_source
            if new_category_id is not None:
                _supersede_pending_suggestion(session, txn.id)
            changed += 1
    for batch_id in batch_ids:
        refresh_import_summary(session, batch_id)
    return changed


def set_manual_category(session: Session, txn_id: int, category_id: int | None) -> None:
    txn = session.get(Transaction, txn_id)
    if txn is None:
        return
    txn.category_id = category_id
    txn.category_source = "manual" if category_id is not None else "none"
    if category_id is not None:
        _supersede_pending_suggestion(session, txn.id)
    refresh_import_summary(session, txn.batch_id)
    session.commit()


def set_counterparty_category(session: Session, counterparty_id: int, category_id: int | None, apply_existing: bool = True) -> int:
    counterparty = session.get(Counterparty, counterparty_id)
    if counterparty is None:
        return 0
    counterparty.default_category_id = category_id
    counterparty.reviewed = True
    changed = 0
    batch_ids: set[int] = set()
    if apply_existing:
        rows = session.execute(
            select(Transaction).where(Transaction.counterparty_id == counterparty_id)
        ).scalars().all()
        active_rules = session.execute(
            select(Rule).where(Rule.active.is_(True)).order_by(Rule.priority.asc(), Rule.id.asc())
        ).scalars().all()
        for txn in rows:
            batch_ids.add(txn.batch_id)
            if txn.category_source == "manual":
                continue
            new_id, new_source = classify_one(txn, active_rules)
            if txn.category_id != new_id or txn.category_source != new_source:
                txn.category_id = new_id
                txn.category_source = new_source
                if new_id is not None:
                    _supersede_pending_suggestion(session, txn.id)
                changed += 1
    for batch_id in batch_ids:
        refresh_import_summary(session, batch_id)
    session.commit()
    return changed


def reapply_all(session: Session) -> int:
    count = classify_transactions(session)
    session.commit()
    return count
