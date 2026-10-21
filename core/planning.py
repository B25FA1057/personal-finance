from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999999.99")
EIGHTY_PERCENT = Decimal("80")
ONE_HUNDRED_PERCENT = Decimal("100")


def parse_amount(value: str, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        amount = Decimal(value.strip())
    except (AttributeError, InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a valid amount.") from None

    if not amount.is_finite():
        raise ValueError(f"{label} must be a finite amount.")
    if amount < 0 or (amount == 0 and not allow_zero):
        minimum = "zero or greater" if allow_zero else "greater than zero"
        raise ValueError(f"{label} must be {minimum}.")
    if amount > MAX_AMOUNT:
        raise ValueError(f"{label} exceeds the maximum supported amount.")
    try:
        rounded_amount = amount.quantize(CENT)
    except InvalidOperation:
        raise ValueError(f"{label} must have no more than two decimal places.") from None
    if rounded_amount != amount:
        raise ValueError(f"{label} must have no more than two decimal places.")
    return rounded_amount


def budget_metrics(
    budget_amount: Decimal, actual_spending: Decimal
) -> tuple[Decimal, Decimal, str]:
    if budget_amount <= 0:
        raise ValueError("Budget amount must be greater than zero.")
    if actual_spending < 0:
        raise ValueError("Actual spending cannot be negative.")

    used_percent = actual_spending * ONE_HUNDRED_PERCENT / budget_amount
    if actual_spending > budget_amount:
        status = "Over budget"
    elif used_percent >= EIGHTY_PERCENT:
        status = "Near limit"
    else:
        status = "On track"
    return budget_amount - actual_spending, used_percent, status


def validate_goal_values(
    name: str,
    target_amount: str,
    current_saved: str,
    target_date: date,
) -> tuple[str, Decimal, Decimal]:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Goal name is required.")
    if len(normalized_name) > 120:
        raise ValueError("Goal name must be 120 characters or fewer.")
    if not isinstance(target_date, date) or isinstance(target_date, datetime):
        raise ValueError("Choose a valid target date.")
    target = parse_amount(target_amount, "Target amount")
    saved = parse_amount(current_saved, "Current saved amount", allow_zero=True)
    return normalized_name, target, saved


def savings_goal_metrics(
    target_amount: Decimal, current_saved: Decimal
) -> tuple[Decimal, Decimal]:
    if target_amount <= 0:
        raise ValueError("Target amount must be greater than zero.")
    if current_saved < 0:
        raise ValueError("Current saved amount cannot be negative.")
    remaining = max(Decimal("0.00"), target_amount - current_saved)
    progress_percent = current_saved * ONE_HUNDRED_PERCENT / target_amount
    return remaining, progress_percent
