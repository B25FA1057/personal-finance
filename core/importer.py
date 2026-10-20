from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from .classify import classify_transactions
from .dedupe import file_sha256, txn_hash
from .enrich import EnrichedTxn, enrich, link_fees, normalize_account
from .models import Account, Bank, Counterparty, ImportBatch, OwnAccount, Transaction
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


def import_file(session: Session, data: bytes, file_name: str) -> ImportResult:
    try:
        return _import_file(session, data, file_name)
    except Exception:
        session.rollback()
        raise


def _import_file(session: Session, data: bytes, file_name: str) -> ImportResult:
    file_hash = file_sha256(data)
    existing_batch = session.execute(
        select(ImportBatch).where(ImportBatch.file_sha256 == file_hash)
    ).scalar_one_or_none()
    if existing_batch is not None:
        account = session.get(Account, existing_batch.account_id)
        bank = session.get(Bank, account.bank_id) if account and account.bank_id else None
        return ImportResult(
            batch_id=existing_batch.id,
            bank_code=bank.code if bank else "",
            account_no=account.account_no if account else "",
            total_rows=existing_batch.total_rows or 0,
            new_rows=0,
            duplicate_rows=0,
            already_imported=True,
        )

    workbook = load_workbook(BytesIO(data), data_only=True)
    parser = detect_parser(workbook)
    statement = parser.parse(workbook)
    if not statement.account_no.strip():
        raise ValueError("The statement does not contain an account number")

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
    if transaction_ids:
        classify_transactions(session, transaction_ids)

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
    )
