from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .base import BankParser, RawTxn, StatementData


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace(" ", "").replace("\u00a0", "")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _date_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        return datetime.fromisoformat(_text(value)).replace(tzinfo=None)
    except ValueError:
        return None


class GolomtParser(BankParser):
    code = "GOLOMT"
    name = "Golomt Bank"

    @classmethod
    def detect(cls, workbook: Any) -> bool:
        try:
            sheet = workbook.active
            if _text(sheet.cell(row=2, column=1).value) != "Гүйлгээний огноо":
                return False
            for column in range(1, sheet.max_column):
                if _text(sheet.cell(row=1, column=column).value) != "IBAN":
                    continue
                iban = re.sub(r"\s+", "", _text(sheet.cell(row=1, column=column + 1).value))
                return len(iban) >= 8 and iban[4:8] == "0015"
        except (AttributeError, IndexError, TypeError):
            return False
        return False

    def parse(self, workbook: Any) -> StatementData:
        sheet = workbook.active
        if not self.detect(workbook):
            raise ValueError("Workbook is not a supported Golomt statement")

        metadata: dict[str, str] = {}
        for column in range(1, sheet.max_column):
            label = _text(sheet.cell(row=1, column=column).value)
            if label:
                metadata[label] = _text(sheet.cell(row=1, column=column + 1).value)

        account_value = metadata.get("Дансны дугаар", "")
        account_match = re.fullmatch(r"\s*(.*?)\s*(?:\[([^\]]+)\])?\s*", account_value)
        account_no = account_match.group(1).strip() if account_match else account_value.strip()
        currency = account_match.group(2).strip().upper() if account_match and account_match.group(2) else "MNT"
        iban = re.sub(r"\s+", "", metadata.get("IBAN", "")) or None
        holder_name = metadata.get("Харилцагчийн нэр", "").strip() or None
        period_from, period_to = self._period(metadata.get("Гүйлгээний огноо", ""))

        transactions: list[RawTxn] = []
        warnings: list[str] = []
        total_in = Decimal("0.00")
        total_out = Decimal("0.00")
        first_balance: Decimal | None = None
        last_balance: Decimal | None = None

        for row_number in range(3, sheet.max_row + 1):
            raw_values = [sheet.cell(row=row_number, column=column).value for column in range(1, 8)]
            if raw_values[0] is None or not _text(raw_values[0]):
                break

            posted_at = _date_time(raw_values[0])
            balance_before = _decimal(raw_values[1])
            amount_out = _decimal(raw_values[2])
            amount_in = _decimal(raw_values[3])
            balance_after = _decimal(raw_values[4])
            if posted_at is None:
                warnings.append(f"Row {row_number}: invalid transaction date.")
                continue
            if (
                (_text(raw_values[2]) and amount_out is None)
                or (_text(raw_values[3]) and amount_in is None)
            ):
                warnings.append(f"Row {row_number}: invalid debit or credit amount.")
                continue
            if (
                (_text(raw_values[1]) and balance_before is None)
                or (_text(raw_values[4]) and balance_after is None)
            ):
                warnings.append(f"Row {row_number}: invalid balance value.")
            if amount_out is None and amount_in is None:
                warnings.append(f"Row {row_number}: both debit and credit amounts are empty.")
                continue
            if amount_out is not None and amount_in is not None:
                warnings.append(f"Row {row_number}: both debit and credit amounts are present; debit used.")
            direction = "out" if amount_out is not None else "in"
            amount = amount_out if amount_out is not None else amount_in
            if amount is None or amount < 0:
                warnings.append(f"Row {row_number}: transaction amount must not be negative.")
                continue

            description = _text(raw_values[5])
            counterparty = _text(raw_values[6]) or None
            raw = {
                str(column): _text(value)
                for column, value in enumerate(raw_values, start=1)
            }
            txn = RawTxn(
                posted_at=posted_at,
                txn_at=posted_at,
                amount=amount,
                direction=direction,
                balance_before=balance_before,
                balance_after=balance_after,
                description=description,
                counterparty_account=counterparty,
                merchant=description,
                raw=raw,
            )
            transactions.append(txn)
            total_in += amount if direction == "in" else Decimal("0.00")
            total_out += amount if direction == "out" else Decimal("0.00")

            if first_balance is None:
                first_balance = balance_before
            last_balance = balance_after
            if balance_before is not None and balance_after is not None:
                expected = balance_before + amount if direction == "in" else balance_before - amount
                if expected != balance_after:
                    warnings.append(
                        f"Row {row_number}: balance does not reconcile "
                        f"({balance_before} {direction} {amount} != {balance_after})."
                    )

        if first_balance is not None and last_balance is not None:
            expected_last = first_balance + total_in - total_out
            if expected_last != last_balance:
                warnings.append(
                    f"Statement balance does not reconcile "
                    f"({first_balance} + {total_in} - {total_out} != {last_balance})."
                )

        if not account_no:
            raise ValueError("Golomt statement does not contain an account number")
        return StatementData(
            bank_code=self.code,
            bank_name=self.name,
            account_no=account_no,
            iban=iban,
            currency=currency,
            holder_name=holder_name,
            period_from=period_from,
            period_to=period_to,
            transactions=transactions,
            warnings=warnings,
            source_columns=tuple(
                _text(sheet.cell(row=2, column=column).value)
                for column in range(1, 8)
                if _text(sheet.cell(row=2, column=column).value)
            ),
        )

    @staticmethod
    def _period(value: str) -> tuple[date | None, date | None]:
        match = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})\s*", value)
        if match is None:
            return None, None
        try:
            return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
        except ValueError:
            return None, None
