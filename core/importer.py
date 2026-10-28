from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai_categorization import suggest_categories
from .classify import classify_transactions
from .dedupe import file_sha256, txn_hash
from .enrich import EnrichedTxn, enrich, link_fees, normalize_account
from .models import (
    Account,
    Bank,
    Counterparty,
    ImportBatch,
    ImportCategorizationSummary,
    OwnAccount,
    Transaction,
)
from .parsers.registry import detect_parser


@dataclass
class ImportResult:
    batch_id: int | None
    bank_code: str
    account_no: str
    total_rows: int
    new_rows: int
    duplicate_rows: int
    warnings: list[str] = field(default_factory=list)
    new_counterparty_ids: list[int] = field(default_factory=list)
    already_imported: bool = False
    rules_classified: int = 0
    counterparties_classified: int = 0
    ai_suggestions: int = 0
    pending_review: int = 0
    errors: list[str] = field(default_factory=list)
    ai_status: str = "disabled"


@dataclass(frozen=True)
class ImportPreview:
    bank_code: str
    bank_name: str
    period_from: str | None
    period_to: str | None
    total_rows: int
    duplicate_rows: int
    already_imported: bool
    detected_columns: tuple[str, ...] = ()
    sample_rows: tuple[dict[str, str], ...] = ()


def _parse_statement(data: bytes):
    try:
        workbook = load_workbook(BytesIO(data), data_only=True, read_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
        raise ValueError("The uploaded file is not a valid Excel workbook.") from exc
    try:
        parser = detect_parser(workbook)
        statement = parser.parse(workbook)
    finally:
        workbook.close()
    if not statement.account_no.strip():
        raise ValueError("The statement does not contain an account number.")
    return statement


def preview_file(session: Session, data: bytes) -> ImportPreview:
    file_hash = file_sha256(data)
    existing_batch = session.scalar(
        select(ImportBatch).where(ImportBatch.file_sha256 == file_hash)
    )
    if existing_batch is not None:
        return ImportPreview(
            bank_code="",
            bank_name="",
            period_from=(
                existing_batch.period_from.isoformat()
                if existing_batch.period_from
                else None
            ),
            period_to=(
                existing_batch.period_to.isoformat()
                if existing_batch.period_to
                else None
            ),
            total_rows=existing_batch.total_rows or 0,
            duplicate_rows=existing_batch.total_rows or 0,
            already_imported=True,
        )

    statement = _parse_statement(data)
    duplicate_rows = 0
    seen_hashes: set[str] = set()
    for transaction in statement.transactions:
        dedupe_hash = txn_hash(statement.account_no, transaction)
        if dedupe_hash in seen_hashes or session.scalar(
            select(Transaction.id).where(Transaction.dedupe_hash == dedupe_hash)
        ) is not None:
            duplicate_rows += 1
        seen_hashes.add(dedupe_hash)
    return ImportPreview(
        bank_code=statement.bank_code,
        bank_name=statement.bank_name,
        period_from=statement.period_from.isoformat() if statement.period_from else None,
        period_to=statement.period_to.isoformat() if statement.period_to else None,
        total_rows=len(statement.transactions),
        duplicate_rows=duplicate_rows,
        already_imported=False,
        detected_columns=statement.source_columns,
        sample_rows=tuple(
            {
                "Date": transaction.posted_at.strftime("%Y-%m-%d %H:%M"),
                "Direction": transaction.direction,
                "Amount": f"{transaction.amount:,.2f}",
                "Description": transaction.description,
            }
            for transaction in statement.transactions[:5]
        ),
    )


def import_file(
    session: Session,
    data: bytes,
    file_name: str,
    *,
    allow_ai: bool = False,
) -> ImportResult:
    try:
        return _import_file(session, data, file_name, allow_ai=allow_ai)
    except Exception:
        session.rollback()
        raise


def _import_file(
    session: Session,
    data: bytes,
    file_name: str,
    *,
    allow_ai: bool,
) -> ImportResult:
    file_hash = file_sha256(data)
    existing_batch = session.execute(
        select(ImportBatch).where(ImportBatch.file_sha256 == file_hash)
    ).scalar_one_or_none()
    if existing_batch is not None:
        account = session.get(Account, existing_batch.account_id)
        bank = session.get(Bank, account.bank_id) if account and account.bank_id else None
        summary = session.get(ImportCategorizationSummary, existing_batch.id)
        return ImportResult(
            batch_id=existing_batch.id,
            bank_code=bank.code if bank else "",
            account_no=account.account_no if account else "",
            total_rows=existing_batch.total_rows or 0,
            new_rows=0,
            duplicate_rows=existing_batch.total_rows or 0,
            already_imported=True,
            rules_classified=summary.rules_classified if summary else 0,
            counterparties_classified=summary.counterparties_classified if summary else 0,
            ai_suggestions=summary.ai_suggestions if summary else 0,
            pending_review=summary.pending_review if summary else 0,
            errors=summary.errors if summary else [],
            ai_status=summary.ai_status if summary else "disabled",
        )

    statement = _parse_statement(data)

    bank = session.execute(select(Bank).where(Bank.code == statement.bank_code)).scalar_one_or_none()
    if bank is None:
        bank = Bank(code=statement.bank_code, name=statement.bank_name)
        session.add(bank)
        session.flush()

    account = session.execute(
        select(Account).where(Account.account_no == statement.account_no)
    ).scalar_one_or_none()
    if account is None:
        account = Account(
            bank_id=bank.id,
            account_no=statement.account_no,
            iban=statement.iban,
            currency=statement.currency,
            holder_name=statement.holder_name,
        )
        session.add(account)
        session.flush()
    else:
        account.bank_id = bank.id
        if statement.iban:
            account.iban = statement.iban
        if statement.currency:
            account.currency = statement.currency
        if statement.holder_name:
            account.holder_name = statement.holder_name

    own_account = session.execute(
        select(OwnAccount).where(OwnAccount.account_no == statement.account_no)
    ).scalar_one_or_none()
    if own_account is None:
        session.add(
            OwnAccount(
                account_no=statement.account_no,
                label=f"Imported: {statement.bank_code}",
            )
        )
        session.flush()

    own_accounts = session.execute(select(OwnAccount.account_no)).scalars().all()
    my_accounts = {
        normalized
        for value in own_accounts
        if (normalized := normalize_account(value)) is not None
    }

    items = [enrich(txn, my_accounts, bank_code=statement.bank_code) for txn in statement.transactions]
    link_fees(items)

    batch = ImportBatch(
        account_id=account.id,
        file_name=file_name,
        file_sha256=file_hash,
        period_from=statement.period_from,
        period_to=statement.period_to,
        total_rows=len(items),
        new_rows=0,
        duplicate_rows=0,
    )
    session.add(batch)
    session.flush()

    imported_by_index: dict[int, Transaction] = {}
    transaction_ids: list[int] = []
    new_counterparty_ids: list[int] = []
    new_rows = 0
    duplicate_rows = 0

    for index, item in enumerate(items):
        transaction_hash = txn_hash(statement.account_no, item.raw)
        existing_txn = session.execute(
            select(Transaction).where(Transaction.dedupe_hash == transaction_hash)
        ).scalar_one_or_none()
        if existing_txn is not None:
            duplicate_rows += 1
            imported_by_index[index] = existing_txn
            continue

        counterparty = session.execute(
            select(Counterparty).where(Counterparty.key == item.counterparty_key)
        ).scalar_one_or_none()
        if counterparty is None:
            counterparty = Counterparty(
                key=item.counterparty_key,
                kind=item.counterparty_kind,
                display_name=item.counterparty_name,
                reviewed=item.counterparty_kind in {"bank_fee", "own_account"},
            )
            session.add(counterparty)
            session.flush()
            if counterparty.kind not in {"bank_fee", "own_account"}:
                new_counterparty_ids.append(counterparty.id)
        else:
            counterparty.kind = item.counterparty_kind
            if not counterparty.display_name:
                counterparty.display_name = item.counterparty_name

        raw = item.raw
        transaction = Transaction(
            account_id=account.id,
            batch_id=batch.id,
            posted_at=raw.posted_at,
            txn_at=item.txn_at,
            amount=raw.amount,
            direction=raw.direction,
            balance_before=raw.balance_before,
            balance_after=raw.balance_after,
            description=raw.description,
            counterparty_account=item.counterparty_account,
            card_mask=item.card_mask,
            channel=item.channel,
            merchant=item.merchant or item.counterparty_name,
            txn_type=item.txn_type,
            counterparty_id=counterparty.id,
            category_source="none",
            raw=raw.raw,
            dedupe_hash=transaction_hash,
        )
        session.add(transaction)
        session.flush()
        imported_by_index[index] = transaction
        transaction_ids.append(transaction.id)
        new_rows += 1

    for index, item in enumerate(items):
        transaction = imported_by_index[index]
        if transaction.batch_id != batch.id or item.parent_index is None:
            continue
        parent = imported_by_index.get(item.parent_index)
        if parent is not None and parent.id != transaction.id:
            transaction.parent_txn_id = parent.id

    batch.new_rows = new_rows
    batch.duplicate_rows = duplicate_rows
    rules_classified = 0
    counterparties_classified = 0
    ai_suggestions = 0
    errors: list[str] = []
    ai_status = "disabled"
    if transaction_ids:
        classify_transactions(session, transaction_ids)
        classified = session.execute(
            select(Transaction.category_source).where(Transaction.id.in_(transaction_ids))
        ).scalars().all()
        rules_classified = sum(source == "rule" for source in classified)
        counterparties_classified = sum(
            source == "counterparty" for source in classified
        )

        uncategorized_ids = session.execute(
            select(Transaction.id).where(
                Transaction.id.in_(transaction_ids),
                Transaction.category_id.is_(None),
                Transaction.category_source == "none",
            )
        ).scalars().all()
        if allow_ai:
            ai_result = suggest_categories(session, list(uncategorized_ids))
            ai_suggestions = ai_result.suggestions
            if ai_result.error:
                errors.extend(ai_result.batch_errors or [ai_result.error])
                ai_status = "partial" if ai_suggestions else "failed"
            elif ai_result.disabled:
                ai_status = "disabled"
            else:
                ai_status = "completed"
        else:
            ai_status = "not_consented"

    pending_review = (
        session.scalar(
            select(func.count(Transaction.id)).where(
                Transaction.id.in_(transaction_ids),
                Transaction.category_id.is_(None),
                Transaction.txn_type != "own_transfer",
            )
        )
        or 0
        if transaction_ids
        else 0
    )
    batch_status = "completed_with_errors" if errors else "completed"
    if batch.id is not None:
        session.add(
            ImportCategorizationSummary(
                batch_id=batch.id,
                status=batch_status,
                ai_status=ai_status,
                rules_classified=rules_classified,
                counterparties_classified=counterparties_classified,
                ai_suggestions=ai_suggestions,
                pending_review=pending_review,
                errors=errors,
            )
        )

    session.commit()
    return ImportResult(
        batch_id=batch.id,
        bank_code=statement.bank_code,
        account_no=statement.account_no,
        total_rows=len(items),
        new_rows=new_rows,
        duplicate_rows=duplicate_rows,
        warnings=statement.warnings,
        new_counterparty_ids=new_counterparty_ids,
        rules_classified=rules_classified,
        counterparties_classified=counterparties_classified,
        ai_suggestions=ai_suggestions,
        pending_review=pending_review,
        errors=errors,
        ai_status=ai_status,
    )
