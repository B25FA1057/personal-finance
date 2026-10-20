from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .config import CARD_REF_TOLERANCE_SECONDS, FEE_LINK_WINDOW_SECONDS, QPAY_FEE_ACCOUNTS
from .parsers.base import RawTxn

CARD_RE = re.compile(
    r"^(\d{6}\*+\d{4}):(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}):([A-Z]+):(.+)$",
    re.IGNORECASE,
)
IBAN_ACCOUNT_RE = re.compile(r"^MN\d{2}(\d{4})(\d{12})$")


@dataclass
class EnrichedTxn:
    raw: RawTxn
    txn_at: datetime
    txn_type: str
    card_mask: str | None
    channel: str | None
    merchant: str | None
    counterparty_account: str | None
    counterparty_key: str
    counterparty_kind: str
    counterparty_name: str
    fee_ref: str | None = None
    parent_index: int | None = None


def normalize_account(value: str | None) -> str | None:
    if value is None:
        return None
    account = value.strip()
    if not account:
        return None
    compact = re.sub(r"\s+", "", account)
    match = IBAN_ACCOUNT_RE.fullmatch(compact)
    if match:
        return match.group(2).lstrip("0")
    return account


def normalize_merchant(value: str) -> str:
    return " ".join(value.upper().split())


def enrich(txn: RawTxn, my_accounts: set[str], bank_code: str = "GOLOMT") -> EnrichedTxn:
    del bank_code
    description = txn.description.strip()
    upper_description = description.upper()
    account = normalize_account(txn.counterparty_account)
    normalized_my_accounts = {
        normalized
        for value in my_accounts
        if (normalized := normalize_account(value)) is not None
    }
    txn_at = txn.posted_at
    txn_type = "other"
    card_mask = None
    channel = None
    merchant = None
    fee_ref = None
    kind = "other"
    name = description[:60] or "Unknown"
    counterparty_key: str

    if upper_description.startswith("EASYINFO FEE FOR"):
        txn_type = "fee"
        kind = "bank_fee"
        name = "SMS alert fee"
        fee_ref = description[len("EASYINFO fee for") :].split(",", 1)[0].strip()
        counterparty_key = "FEE:EASYINFO"
    elif upper_description.startswith("CHARGES FOR PORD"):
        txn_type = "fee"
        kind = "bank_fee"
        name = "Transfer fee"
        counterparty_key = "FEE:PORD"
    elif upper_description.startswith("QPAY") and account in QPAY_FEE_ACCOUNTS:
        txn_type = "fee"
        kind = "bank_fee"
        name = "QPay fee"
        counterparty_key = "FEE:QPAY"
    else:
        card_match = CARD_RE.fullmatch(description)
        if card_match:
            card_mask = card_match.group(1)
            txn_at = datetime.strptime(card_match.group(2), "%d-%m-%Y %H:%M:%S")
            channel = card_match.group(3).upper()
            merchant = normalize_merchant(card_match.group(4))
            txn_type = "card"
            kind = "merchant"
            name = merchant
            counterparty_key = f"M:{merchant}"
        elif upper_description.startswith("SOCIALPAY ГҮЙЛГЭЭ,") and txn.direction == "out":
            parts = description.split(",")
            txn_type = "p2p_out"
            kind = "person"
            name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else description[:60]
            counterparty_key = f"A:{account}" if account else f"OTHER:{name}"
        elif upper_description.startswith("QPAY"):
            txn_type = "qpay"
            kind = "biller"
            name = f"QPay {account}" if account else "QPay"
            counterparty_key = f"A:{account}" if account else "QPAY"
        elif description.endswith("!F"):
            txn_type = "topup"
            kind = "biller"
            name = "Mobile top-up"
            counterparty_key = f"A:{account}" if account else f"OTHER:{name}"
        elif txn.direction == "in" and account:
            txn_type = "p2p_in"
            kind = "person"
            name = description.rsplit("-", 1)[-1].strip() or description[:60]
            counterparty_key = f"A:{account}"
        else:
            counterparty_key = f"A:{account}" if account else f"OTHER:{name}"

        if account in normalized_my_accounts and txn_type in {"p2p_in", "p2p_out", "other"}:
            txn_type = "own_transfer"
            kind = "own_account"
            name = "Own account transfer"
            counterparty_key = f"OWN:{account}"

    return EnrichedTxn(
        raw=txn,
        txn_at=txn_at,
        txn_type=txn_type,
        card_mask=card_mask,
        channel=channel,
        merchant=merchant,
        counterparty_account=account,
        counterparty_key=counterparty_key,
        counterparty_kind=kind,
        counterparty_name=name,
        fee_ref=fee_ref,
    )


def link_fees(items: list[EnrichedTxn]) -> None:
    for fee_index, fee in enumerate(items):
        if fee.txn_type != "fee":
            continue

        if fee.counterparty_key == "FEE:QPAY":
            fee.parent_index = next(
                (
                    index
                    for index, candidate in enumerate(items)
                    if candidate.txn_type == "qpay"
                    and candidate.raw.posted_at == fee.raw.posted_at
                ),
                None,
            )
            continue

        if fee.counterparty_key == "FEE:PORD":
            fee.parent_index = next(
                (
                    index
                    for index, candidate in enumerate(items)
                    if candidate.txn_type != "fee"
                    and candidate.raw.posted_at == fee.raw.posted_at
                    and candidate.counterparty_account == fee.counterparty_account
                ),
                None,
            )
            continue

        reference = fee.fee_ref or ""
        if reference.startswith(("POS", "BOM", "MCI")):
            try:
                ref_time = datetime.strptime(reference[3:17], "%Y%m%d%H%M%S")
            except ValueError:
                continue
            fee.parent_index = next(
                (
                    index
                    for index, candidate in enumerate(items)
                    if candidate.txn_type == "card"
                    and candidate.channel == reference[:3]
                    and abs((candidate.txn_at - ref_time).total_seconds())
                    <= CARD_REF_TOLERANCE_SECONDS
                ),
                None,
            )
            continue

        candidates = [
            (index, candidate)
            for index, candidate in enumerate(items)
            if candidate.txn_type != "fee"
            and candidate.raw.posted_at <= fee.raw.posted_at
            and (fee.raw.posted_at - candidate.raw.posted_at).total_seconds()
            <= FEE_LINK_WINDOW_SECONDS
        ]
        if candidates:
            fee.parent_index = max(candidates, key=lambda pair: pair[1].raw.posted_at)[0]
