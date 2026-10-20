from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from .classify import classify_transactions
from .dedupe import file_sha256, txn_hash
from .enrich import enrich, link_fees
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
    sha = file_sha256(data)
    existing_batch = session.execute(select(ImportBatch).where(ImportBatch.file_sha256 == sha)).scalar_one_or_none()
    if existing_batch is not None:
        return ImportResult(
            batch_id=existing_batch.id,
            bank_code="",
            account_no="",
            total_rows=0,
            new_rows=0,
            duplicate_rows=0,
            warnings=[],
            new_counterparty_ids=[],
            already_imported=True,
        )

    workbook = load_workbook(BytesIO(data), data_only=True)
    parser = detect_parser(workbook)
    statement = parser.parse(workbook)

    bank = session.execute(select(Bank).where(Bank.code == statement.bank_code)).scalar_one_or_none()
    if bank is None:
        bank = Bank(code=statement.bank_code, name=statement.bank_name)
        session.add(bank)
        session.flush()

    account = session.execute(select(Account).where(Account.account_no == statement.account_no)).scalar_one_or_none()
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
        if statement.iban:
            account.iban = statement.iban
        if statement.currency:
            account.currency = statement.currency
        if statement.holder_name:
            account.holder_name = statement.holder_name

    own_account = session.execute(select(OwnAccount.account_no)).scalars().all()
    my_accounts = set(own_account)
    if statement.account_no not in my_accounts:
        existing_own = session.execute(select(OwnAccount).where(OwnAccount.account_no == statement.account_no)).scalar_one_or_none()
        if existing_own is None:
            session.add(OwnAccount(account_no=statement.account_no, label=f"Imported: {statement.bank_code}"))
            session.flush()
            my_accounts.add(statement.account_no)

    items = [enrich(item, my_accounts, bank_code=statement.bank_code) for item in statement.transactions]
    link_fees(items)

    batch = ImportBatch(
        account_id=account.id,
        file_name=file_name,
        file_sha256=sha,
        total_rows=len(statement.transactions),
        new_rows=0,
        duplicate_rows=0,
    )
    session.add(batch)
    session.flush()

    new_counterparty_ids: list[int] = []
    new_rows = 0
    duplicate_rows = 0

    for idx, item in enumerate(items):
        base_name = (item.merchant or item.description or f"T{idx}").upper().strip()
        kind = "merchant"
        if "fee" in item.txn_type.lower():
            kind = "bank_fee"
            key = f"FEE:{base_name}"
        elif item.txn_type == "own_transfer":
            kind = "own_account"
            key = f"OWN:{item.counterparty_account or base_name}"
        elif item.counterparty_account and item.counterparty_account in my_accounts:
            kind = "own_account"
            key = f"OWN:{item.counterparty_account}"
        else:
            key = f"M:{base_name}"

        dedupe_hash = txn_hash(statement.account_no, item)
        existing_txn = session.execute(select(Transaction).where(Transaction.dedupe_hash == dedupe_hash)).scalar_one_or_none()
        if existing_txn is not None:
            duplicate_rows += 1
            continue

        counterparty = session.execute(select(Counterparty).where(Counterparty.key == key)).scalar_one_or_none()
        if counterparty is None:
            counterparty = Counterparty(
                key=key,
                kind=kind,
                display_name=item.merchant or item.description[:200],
                reviewed=kind in {"bank_fee", "own_account"},
            )
            session.add(counterparty)
            session.flush()
            new_counterparty_ids.append(counterparty.id)

        transaction = Transaction(
            account_id=account.id,
            batch_id=batch.id,
            posted_at=item.posted_at,
            txn_at=item.txn_at,
            amount=item.amount,
            direction=item.direction,
            balance_before=item.balance_before,
            balance_after=item.balance_after,
            description=item.description,
            counterparty_account=item.counterparty_account,
            card_mask=item.card_mask,
            channel=item.channel,
            merchant=item.merchant,
            txn_type=item.txn_type,
            counterparty_id=counterparty.id,
            category_id=None,
            category_source="none",
            raw=item.raw,
            dedupe_hash=dedupe_hash,
        )
        session.add(transaction)
        session.flush()
        new_rows += 1

    batch.new_rows = new_rows
    batch.duplicate_rows = duplicate_rows
    batch.total_rows = len(statement.transactions)
    batch.period_from = min((t.posted_at.date() for t in items), default=None)
    batch.period_to = max((t.posted_at.date() for t in items), default=None)

    try:
        classify_transactions(session)
    except Exception:
        pass

    session.commit()
    return ImportResult(
        batch_id=batch.id,
        bank_code=statement.bank_code,
        account_no=statement.account_no,
        total_rows=batch.total_rows or 0,
        new_rows=new_rows,
        duplicate_rows=duplicate_rows,
        warnings=[],
        new_counterparty_ids=new_counterparty_ids,
        already_imported=False,
    )
