from __future__ import annotations

import re

import pandas as pd
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.classify import reapply_all, rule_matches
from core.db import get_session
from core.models import (
    Category,
    Counterparty,
    ImportBatch,
    MonthlyBudget,
    OwnAccount,
    Rule,
    Transaction,
)
from core.queries import batches_df, categories_df
from core.seed import seed_default_categories


def _normalize_account(value: str | None) -> str | None:
    if not value:
        return None
    account_no = re.sub(r"\s+", "", value.strip())
    match = re.fullmatch(r"MN\d{2}(\d{4})(\d{12})", account_no)
    if match:
        return match.group(2).lstrip("0")
    return account_no


st.title("Settings")

session = get_session()
categories = session.execute(
    select(Category).order_by(Category.kind, Category.sort_order, Category.id)
).scalars().all()
category_names = [category.name for category in categories]
category_ids_by_name = {category.name: category.id for category in categories}

categories_tab, rules_tab, accounts_tab, imports_tab = st.tabs(
    ["Categories", "Rules", "My accounts", "Imports"]
)

with categories_tab:
    st.subheader("Categories")
    st.caption(
        "Add any missing built-in categories without changing existing categories "
        "or transaction classifications."
    )
    if st.button(
        "Initialize missing default categories",
        key="initialize_default_categories",
    ):
        try:
            added, skipped = seed_default_categories(session)
            session.commit()
            st.success(
                f"Default categories initialized: {added} added, {skipped} already present/skipped."
            )
            session.close()
            st.rerun()
        except IntegrityError:
            session.rollback()
            st.error(
                "Could not initialize default categories because another category "
                "was added at the same time. Refresh and retry."
            )

    categories_table = categories_df(session)
    edited_categories = st.data_editor(
        categories_table,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=["id"],
        column_config={
            "id": None,
            "name": st.column_config.TextColumn("Name", required=True),
            "kind": st.column_config.SelectboxColumn(
                "Kind", options=["income", "expense", "savings", "transfer"], required=True
            ),
            "sort_order": st.column_config.NumberColumn("Sort order", min_value=0, step=1),
        },
        key="settings_categories",
    )

    if st.button("Save categories", key="save_categories"):
        errors: list[str] = []
        rows_to_save: list[tuple[int | None, str, str, int]] = []
        seen_names: set[str] = set()
        persisted_ids = {category.id for category in categories}
        kept_ids: set[int] = set()
        for row_number, row in enumerate(edited_categories.to_dict("records"), start=1):
            raw_id = row.get("id")
            category_id = None if pd.isna(raw_id) else int(raw_id)
            name = "" if pd.isna(row.get("name")) else str(row["name"]).strip()
            kind = "" if pd.isna(row.get("kind")) else str(row["kind"]).strip()
            raw_order = row.get("sort_order")
            if not name:
                errors.append(f"Row {row_number}: category name is required.")
                continue
            if len(name) > 100:
                errors.append(f"Row {row_number}: category name must be 100 characters or fewer.")
            if kind not in {"income", "expense", "savings", "transfer"}:
                errors.append(f"Row {row_number}: choose a valid category kind.")
            try:
                sort_order = int(raw_order)
                if isinstance(raw_order, float) and not raw_order.is_integer():
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                errors.append(f"Row {row_number}: sort order must be a whole number.")
                sort_order = 100
            if name in seen_names:
                errors.append(f"Row {row_number}: category name {name!r} is duplicated.")
            seen_names.add(name)
            if category_id is not None:
                if category_id not in persisted_ids or category_id in kept_ids:
                    errors.append(f"Row {row_number}: invalid or duplicated category id.")
                kept_ids.add(category_id)
            rows_to_save.append((category_id, name, kind, sort_order))

        removed_ids = persisted_ids - kept_ids
        usage_counts: dict[int, int] = {}
        for category_id in removed_ids:
            usage_counts[category_id] = sum(
                (
                    session.scalar(
                        select(func.count(Transaction.id)).where(
                            Transaction.category_id == category_id
                        )
                    )
                    or 0,
                    session.scalar(
                        select(func.count(Rule.id)).where(Rule.category_id == category_id)
                    )
                    or 0,
                    session.scalar(
                        select(func.count(Counterparty.id)).where(
                            Counterparty.default_category_id == category_id
                        )
                    )
                    or 0,
                    session.scalar(
                        select(func.count(Category.id)).where(
                            Category.parent_id == category_id
                        )
                    )
                    or 0,
                    session.scalar(
                        select(func.count(MonthlyBudget.id)).where(
                            MonthlyBudget.category_id == category_id
                        )
                    )
                    or 0,
                )
            )
            if usage_counts[category_id]:
                category_name = next(
                    category.name for category in categories if category.id == category_id
                )
                errors.append(
                    f"Cannot delete {category_name!r}: it is referenced {usage_counts[category_id]} "
                    "time(s) by transactions, rules, counterparties, subcategories, or budgets."
                )

        if errors:
            for error in errors:
                st.error(error)
        else:
            try:
                for category_id in removed_ids:
                    session.delete(session.get(Category, category_id))
                for category_id, name, kind, sort_order in rows_to_save:
                    category = session.get(Category, category_id) if category_id is not None else None
                    if category is None:
                        category = Category(name=name, kind=kind, sort_order=sort_order)
                        session.add(category)
                    else:
                        category.name = name
                        category.kind = kind
                        category.sort_order = sort_order
                session.commit()
                st.success("Categories saved.")
                session.close()
                st.rerun()
            except IntegrityError as exc:
                session.rollback()
                st.error(f"Could not save categories because a category name is already in use: {exc.orig}")

with rules_tab:
    st.subheader("Classification rules")
    persisted_rules = session.execute(
        select(Rule).order_by(Rule.priority, Rule.id)
    ).scalars().all()
    rules_table = pd.DataFrame(
        [
            {
                "id": rule.id,
                "priority": rule.priority,
                "field": rule.field,
                "match_type": rule.match_type,
                "pattern": rule.pattern,
                "category": (
                    session.get(Category, rule.category_id).name
                    if session.get(Category, rule.category_id) is not None
                    else ""
                ),
                "active": rule.active,
            }
            for rule in persisted_rules
        ],
        columns=["id", "priority", "field", "match_type", "pattern", "category", "active"],
    )
    edited_rules = st.data_editor(
        rules_table,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=["id"],
        column_config={
            "id": None,
            "priority": st.column_config.NumberColumn("Priority", min_value=0, step=1),
            "field": st.column_config.SelectboxColumn(
                "Field",
                options=["description", "merchant", "counterparty_account", "txn_type"],
                required=True,
            ),
            "match_type": st.column_config.SelectboxColumn(
                "Match type", options=["contains", "startswith", "equals", "regex"], required=True
            ),
            "pattern": st.column_config.TextColumn("Pattern", required=True),
            "category": st.column_config.SelectboxColumn(
                "Category", options=category_names, required=True
            ),
            "active": st.column_config.CheckboxColumn("Active"),
        },
        key="settings_rules",
    )

    if st.button("Save & re-apply rules", key="save_rules"):
        errors = []
        rows_to_save: list[tuple[int | None, int, str, str, str, int, bool]] = []
        persisted_ids = {rule.id for rule in persisted_rules}
        kept_ids: set[int] = set()
        for row_number, row in enumerate(edited_rules.to_dict("records"), start=1):
            raw_id = row.get("id")
            rule_id = None if pd.isna(raw_id) else int(raw_id)
            raw_priority = row.get("priority")
            field = "" if pd.isna(row.get("field")) else str(row["field"]).strip()
            match_type = "" if pd.isna(row.get("match_type")) else str(row["match_type"]).strip()
            pattern = "" if pd.isna(row.get("pattern")) else str(row["pattern"]).strip()
            category_name = "" if pd.isna(row.get("category")) else str(row["category"]).strip()
            active = False if pd.isna(row.get("active")) else bool(row["active"])

            try:
                priority = int(raw_priority)
                if isinstance(raw_priority, float) and not raw_priority.is_integer():
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                priority = 100
                errors.append(f"Row {row_number}: priority must be a whole number.")
            if field not in {"description", "merchant", "counterparty_account", "txn_type"}:
                errors.append(f"Row {row_number}: choose a valid rule field.")
            if match_type not in {"contains", "startswith", "equals", "regex"}:
                errors.append(f"Row {row_number}: choose a valid match type.")
            if not pattern:
                errors.append(f"Row {row_number}: pattern is required.")
            if len(pattern) > 300:
                errors.append(f"Row {row_number}: pattern must be 300 characters or fewer.")
            if match_type == "regex":
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(f"Row {row_number}: invalid regular expression: {exc}.")
            if category_name not in category_ids_by_name:
                errors.append(f"Row {row_number}: choose an existing category.")
            if rule_id is not None:
                if rule_id not in persisted_ids or rule_id in kept_ids:
                    errors.append(f"Row {row_number}: invalid or duplicated rule id.")
                kept_ids.add(rule_id)
            if category_name in category_ids_by_name:
                rows_to_save.append(
                    (
                        rule_id,
                        priority,
                        field,
                        match_type,
                        pattern,
                        category_ids_by_name[category_name],
                        active,
                    )
                )

        if errors:
            for error in errors:
                st.error(error)
        else:
            try:
                for rule in persisted_rules:
                    if rule.id not in kept_ids:
                        session.delete(rule)
                for rule_id, priority, field, match_type, pattern, category_id, active in rows_to_save:
                    rule = session.get(Rule, rule_id) if rule_id is not None else None
                    if rule is None:
                        rule = Rule(
                            priority=priority,
                            field=field,
                            match_type=match_type,
                            pattern=pattern,
                            category_id=category_id,
                            active=active,
                        )
                        session.add(rule)
                    else:
                        rule.priority = priority
                        rule.field = field
                        rule.match_type = match_type
                        rule.pattern = pattern
                        rule.category_id = category_id
                        rule.active = active
                session.flush()
                reclassified = reapply_all(session)
                st.success(f"Rules saved. Reclassified {reclassified} transaction(s).")
                session.close()
                st.rerun()
            except IntegrityError as exc:
                session.rollback()
                st.error(f"Could not save rules: {exc.orig}")

    st.subheader("Test a rule")
    test_field = st.selectbox(
        "Field",
        ["description", "merchant", "counterparty_account", "txn_type"],
        key="test_rule_field",
    )
    test_match_type = st.selectbox(
        "Match type", ["contains", "startswith", "equals", "regex"], key="test_rule_match_type"
    )
    test_pattern = st.text_input("Pattern", key="test_rule_pattern")
    if st.button("Test rule", key="test_rule"):
        if not test_pattern.strip():
            st.error("Enter a pattern to test.")
        else:
            valid_pattern = True
            if test_match_type == "regex":
                try:
                    re.compile(test_pattern)
                except re.error as exc:
                    st.error(f"Invalid regular expression: {exc}")
                    valid_pattern = False
            if valid_pattern:
                test_rule = Rule(
                    field=test_field,
                    match_type=test_match_type,
                    pattern=test_pattern,
                    category_id=next(iter(category_ids_by_name.values()), 0),
                )
                matches = [
                    txn
                    for txn in session.execute(select(Transaction).order_by(Transaction.id)).scalars()
                    if rule_matches(test_rule, txn)
                ]
                st.write(f"Matches {len(matches)} transaction(s).")
                if matches:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Date": txn.txn_at,
                                    "Amount": txn.amount,
                                    "Type": txn.txn_type,
                                    "Merchant": txn.merchant,
                                    "Description": txn.description,
                                }
                                for txn in matches[:10]
                            ]
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )

with accounts_tab:
    st.subheader("My accounts")
    st.caption(
        "Add all your own account numbers at any bank. Money moving between them is not counted as income/expense."
    )
    own_accounts = session.execute(
        select(OwnAccount).order_by(OwnAccount.account_no)
    ).scalars().all()
    accounts_table = pd.DataFrame(
        [{"id": account.id, "account_no": account.account_no, "label": account.label or ""} for account in own_accounts],
        columns=["id", "account_no", "label"],
    )
    edited_accounts = st.data_editor(
        accounts_table,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=["id"],
        column_config={
            "id": None,
            "account_no": st.column_config.TextColumn("Account number", required=True),
            "label": st.column_config.TextColumn("Label"),
        },
        key="settings_own_accounts",
    )
    if st.button("Save own accounts", key="save_own_accounts"):
        errors = []
        rows_to_save: list[tuple[int | None, str, str | None]] = []
        persisted_ids = {account.id for account in own_accounts}
        kept_ids: set[int] = set()
        seen_accounts: set[str] = set()
        for row_number, row in enumerate(edited_accounts.to_dict("records"), start=1):
            raw_id = row.get("id")
            account_id = None if pd.isna(raw_id) else int(raw_id)
            account_no = "" if pd.isna(row.get("account_no")) else str(row["account_no"]).strip()
            label = "" if pd.isna(row.get("label")) else str(row["label"]).strip()
            if not account_no:
                errors.append(f"Row {row_number}: account number is required.")
            if len(account_no) > 34:
                errors.append(f"Row {row_number}: account number must be 34 characters or fewer.")
            if account_no in seen_accounts:
                errors.append(f"Row {row_number}: account number {account_no!r} is duplicated.")
            seen_accounts.add(account_no)
            if account_id is not None:
                if account_id not in persisted_ids or account_id in kept_ids:
                    errors.append(f"Row {row_number}: invalid or duplicated account id.")
                kept_ids.add(account_id)
            rows_to_save.append((account_id, account_no, label or None))

        if errors:
            for error in errors:
                st.error(error)
        else:
            try:
                for account in own_accounts:
                    if account.id not in kept_ids:
                        session.delete(account)
                for account_id, account_no, label in rows_to_save:
                    account = session.get(OwnAccount, account_id) if account_id is not None else None
                    if account is None:
                        session.add(OwnAccount(account_no=account_no, label=label))
                    else:
                        account.account_no = account_no
                        account.label = label
                session.commit()
                st.success("Own accounts saved.")
                session.close()
                st.rerun()
            except IntegrityError as exc:
                session.rollback()
                st.error(f"Could not save own accounts: {exc.orig}")

    if st.button("Re-detect own transfers", key="redetect_own_transfers"):
        own_numbers = {
            _normalize_account(account.account_no)
            for account in session.execute(select(OwnAccount)).scalars()
        }
        candidates = session.execute(
            select(Transaction).where(Transaction.txn_type.in_(["p2p_in", "p2p_out", "other"]))
        ).scalars()
        changed = 0
        for transaction in candidates:
            if _normalize_account(transaction.counterparty_account) not in own_numbers:
                continue
            transaction.txn_type = "own_transfer"
            counterparty = session.get(Counterparty, transaction.counterparty_id)
            if counterparty is not None:
                counterparty.kind = "own_account"
                counterparty.reviewed = True
            changed += 1
        reclassified = reapply_all(session)
        st.success(
            f"Re-detected {changed} own transfer(s). Reclassified {reclassified} transaction(s)."
        )

with imports_tab:
    st.subheader("Import history")
    import_history = batches_df(session)
    if import_history.empty:
        st.info("No imports to manage.")
    else:
        batch_ids = [int(batch_id) for batch_id in import_history["id"].tolist()]
        selected_batch_id = st.selectbox(
            "Import batch",
            batch_ids,
            format_func=lambda batch_id: (
                f"{import_history.loc[import_history['id'] == batch_id, 'file_name'].iloc[0]} "
                f"— {import_history.loc[import_history['id'] == batch_id, 'bank_code'].iloc[0]} "
                f"— {import_history.loc[import_history['id'] == batch_id, 'imported_at'].iloc[0]}"
            ),
            key="delete_import_batch",
        )
        st.dataframe(import_history, use_container_width=True, hide_index=True)
        confirmed = st.checkbox(
            "I understand that deleting this import also deletes its transactions.",
            key=f"confirm_delete_batch_{selected_batch_id}",
        )
        if st.button("Delete this import", disabled=not confirmed, key="delete_import"):
            batch = session.get(ImportBatch, selected_batch_id)
            if batch is None:
                st.error("That import batch no longer exists. Refresh the page and try again.")
            else:
                try:
                    session.delete(batch)
                    session.commit()
                    st.success("Import and its transactions deleted.")
                    session.close()
                    st.rerun()
                except IntegrityError as exc:
                    session.rollback()
                    st.error(f"Could not delete this import: {exc.orig}")

session.close()
