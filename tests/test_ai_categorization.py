from __future__ import annotations

import json
from collections.abc import Iterator
from io import BytesIO
from types import SimpleNamespace

import pytest
from google.genai.errors import APIError
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from core import ai_categorization, config
from core.ai_categorization import resolve_suggestion, suggest_categories
from core.importer import import_file
from core.models import (
    Base,
    CategorizationSuggestion,
    Category,
    Counterparty,
    ImportCategorizationSummary,
    OwnAccount,
    Rule,
    Transaction,
)
from core.queries import batches_df


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _statement_bytes(
    *,
    report_date: str = "2026-01-14",
    posted_at: str = "2026-01-02T10:01:00",
    description: str = "400000******1111:02-01-2026 10:00:00:POS:TEST STORE",
    direction: str = "out",
    amount: float = 10.0,
    counterparty_account: str = "9999990001",
    include_fee: bool = False,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OperativeAccountStatementRaw"
    sheet.append(
        [
            "Хуулганы огноо",
            report_date,
            "ДАНСНЫ ХУУЛГА",
            "Харилцагчийн нэр",
            "TEST",
            "Гүйлгээний огноо",
            "2026-01-01 - 2026-01-31",
            "Дансны дугаар",
            "1800000001 [MNT]",
            "IBAN",
            "MN00 0015 0018 0000 0001",
        ]
    )
    sheet.append(
        [
            "Гүйлгээний огноо",
            "Эхний үлдэгдэл",
            "Зарлага",
            "Орлого",
            "Эцсийн үлдэгдэл",
            "Гүйлгээний утга",
            "Харьцсан данс",
        ]
    )
    outgoing = amount if direction == "out" else ""
    incoming = amount if direction == "in" else ""
    ending = 100 - amount if direction == "out" else 100 + amount
    sheet.append(
        [
            posted_at,
            100,
            outgoing,
            incoming,
            ending,
            description,
            counterparty_account,
        ]
    )
    if include_fee:
        sheet.append(
            [
                "2026-01-02T10:02:00",
                ending,
                1.0,
                "",
                ending - 1,
                "Charges for PORD Customer Payment :000658547340",
                counterparty_account,
            ]
        )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _add_categories(session: Session) -> dict[str, Category]:
    categories = {
        "Dining": Category(name="Dining", kind="expense"),
        "Groceries": Category(name="Groceries", kind="expense"),
        "Salary": Category(name="Salary", kind="income"),
        "Savings": Category(name="Savings", kind="savings"),
        "Transfers": Category(name="Transfers", kind="transfer"),
        "Own account transfer": Category(
            name="Own account transfer", kind="transfer"
        ),
        "Bank fees": Category(name="Bank fees", kind="expense"),
    }
    session.add_all(categories.values())
    session.flush()
    return categories


class _FakeModels:
    def __init__(
        self,
        category_id: int,
        *,
        output_text: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.category_id = category_id
        self.output_text = output_text
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.output_text is not None:
            return SimpleNamespace(output_text=self.output_text)
        request = json.loads(kwargs["contents"])
        suggestions = [
            {
                "transaction_index": index,
                "category_id": self.category_id,
                "confidence": 0.91,
                "rationale": "The transaction matches this category.",
            }
            for index, _ in enumerate(request["transactions"])
        ]
        return SimpleNamespace(
            output_text=json.dumps({"suggestions": suggestions})
        )


class _FakeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models


def _install_fake_gemini(
    monkeypatch: pytest.MonkeyPatch, models: _FakeModels
) -> None:
    monkeypatch.setattr(config, "GEMINI_API_KEY", "mock-api-key")
    monkeypatch.setattr(
        ai_categorization,
        "_load_gemini_client",
        lambda: (_FakeClient(models), (RuntimeError,)),
    )


def test_new_upload_suggests_only_after_existing_classification_and_saves_summary(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    db_session.add(
        Rule(
            priority=10,
            field="txn_type",
            match_type="equals",
            pattern="fee",
            category_id=categories["Bank fees"].id,
            active=True,
        )
    )
    responses = _FakeModels(categories["Dining"].id)
    _install_fake_gemini(monkeypatch, responses)

    result = import_file(
        db_session,
        _statement_bytes(include_fee=True),
        "synthetic.xlsx",
        allow_ai=True,
    )

    assert result.new_rows == 2
    assert result.duplicate_rows == 0
    assert result.rules_classified == 1
    assert result.ai_suggestions == 1
    assert result.pending_review == 1
    assert result.errors == []

    transaction = db_session.scalar(
        select(Transaction).where(Transaction.txn_type == "card")
    )
    suggestion = db_session.get(CategorizationSuggestion, transaction.id)
    summary = db_session.get(ImportCategorizationSummary, result.batch_id)
    assert transaction.description == (
        "400000******1111:02-01-2026 10:00:00:POS:TEST STORE"
    )
    assert transaction.merchant == "TEST STORE"
    assert transaction.category_id is None
    assert transaction.category_source == "none"
    assert suggestion is not None and suggestion.status == "pending"
    assert suggestion.category_id == categories["Dining"].id
    assert summary is not None
    assert summary.status == "completed"
    assert summary.ai_status == "completed"
    assert summary.ai_suggestions == 1
    assert summary.pending_review == 1
    history = batches_df(db_session)
    assert history.iloc[0]["ai_suggestions"] == 1
    assert history.iloc[0]["pending_review"] == 1
    assert "account_no" not in history.columns

    request = json.loads(responses.calls[0]["contents"])
    assert responses.calls[0]["model"] == config.GEMINI_MODEL
    assert responses.calls[0]["config"]["response_mime_type"] == "application/json"
    assert request["transactions"] == [
        {
            "transaction_index": 0,
            "direction": "out",
            "transaction_type": "card",
            "merchant": "TEST STORE",
            "description": "TEST STORE",
        }
    ]
    serialized_request = json.dumps(request)
    assert "1800000001" not in serialized_request
    assert "9999990001" not in serialized_request
    assert "10.0" not in serialized_request
    assert "2026-01-02" not in serialized_request


def test_confirmed_counterparty_mapping_prevents_ai_request(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    counterparty = Counterparty(
        key="M:TEST STORE",
        kind="merchant",
        display_name="TEST STORE",
        default_category_id=categories["Dining"].id,
        reviewed=True,
    )
    db_session.add(counterparty)
    db_session.flush()
    responses = _FakeModels(categories["Groceries"].id)
    _install_fake_gemini(monkeypatch, responses)

    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )
    transaction = db_session.scalar(select(Transaction))

    assert result.counterparties_classified == 1
    assert result.ai_suggestions == 0
    assert transaction.category_id == categories["Dining"].id
    assert transaction.category_source == "counterparty"
    assert responses.calls == []


def test_exact_duplicate_upload_does_not_call_ai_twice(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    responses = _FakeModels(categories["Dining"].id)
    _install_fake_gemini(monkeypatch, responses)
    data = _statement_bytes()

    first = import_file(db_session, data, "synthetic.xlsx", allow_ai=True)
    repeated = import_file(db_session, data, "synthetic.xlsx", allow_ai=True)

    assert first.ai_suggestions == 1
    assert repeated.already_imported is True
    assert repeated.new_rows == 0
    assert repeated.duplicate_rows == 1
    assert len(responses.calls) == 1
    assert len(db_session.scalars(select(Transaction)).all()) == 1


def test_confirmed_exact_merchant_rule_classifies_future_upload(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    responses = _FakeModels(categories["Dining"].id)
    _install_fake_gemini(monkeypatch, responses)

    first = import_file(
        db_session, _statement_bytes(), "first.xlsx", allow_ai=True
    )
    transaction = db_session.scalar(select(Transaction))
    resolve_suggestion(
        db_session,
        transaction.id,
        action="accept",
        category_id=categories["Dining"].id,
        create_rule=True,
        rule_field="merchant",
    )
    db_session.commit()

    second = import_file(
        db_session,
        _statement_bytes(
            report_date="2026-01-15",
            posted_at="2026-01-03T10:01:00",
            description="400000******1111:03-01-2026 10:00:00:POS:TEST STORE",
            amount=12.0,
        ),
        "second.xlsx",
        allow_ai=True,
    )
    future_transaction = db_session.scalar(
        select(Transaction).where(Transaction.batch_id == second.batch_id)
    )
    rule = db_session.scalar(
        select(Rule).where(
            Rule.field == "merchant",
            Rule.match_type == "equals",
            Rule.pattern == "TEST STORE",
        )
    )

    assert first.ai_suggestions == 1
    assert second.rules_classified == 1
    assert second.ai_suggestions == 0
    assert future_transaction.category_id == categories["Dining"].id
    assert future_transaction.category_source == "rule"
    assert rule is not None and rule.active is True
    assert len(responses.calls) == 1


def test_missing_api_key_keeps_imported_transaction_for_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_categories(db_session)
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)

    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    assert result.new_rows == 1
    assert result.ai_suggestions == 0
    assert result.pending_review == 1
    assert result.ai_status == "disabled"
    assert result.errors == []
    assert db_session.scalar(select(Transaction)).category_id is None


def test_malformed_model_response_is_reported_without_rolling_back_import(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_categories(db_session)
    responses = _FakeModels(1, output_text="{invalid json")
    _install_fake_gemini(monkeypatch, responses)

    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    assert result.new_rows == 1
    assert result.ai_suggestions == 0
    assert result.pending_review == 1
    assert result.ai_status == "failed"
    assert result.errors == ["Gemini returned malformed JSON."]
    assert db_session.scalar(select(Transaction)) is not None
    assert db_session.get(ImportCategorizationSummary, result.batch_id).status == (
        "completed_with_errors"
    )


def test_gemini_failure_is_reported_and_import_still_completes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    responses = _FakeModels(categories["Dining"].id, error=RuntimeError("offline"))
    _install_fake_gemini(monkeypatch, responses)

    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    assert result.new_rows == 1
    assert result.ai_suggestions == 0
    assert result.pending_review == 1
    assert result.ai_status == "failed"
    assert result.errors == [
        "Gemini could not complete categorization. Review the pending transactions and retry."
    ]
    assert db_session.scalar(select(Transaction)) is not None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            APIError(429, {}),
            "Gemini rate limit reached. Retry categorization later.",
        ),
        (
            APIError(503, {}),
            "Gemini could not complete categorization. Review the pending transactions and retry.",
        ),
    ],
)
def test_gemini_api_status_errors_are_reported_without_sensitive_details(
    error: APIError, expected: str
) -> None:
    assert ai_categorization._friendly_api_error(error) == expected


def test_verified_own_transfer_is_not_sent_to_ai_or_counted_for_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    db_session.add(OwnAccount(account_no="5000000001", label="Verified account"))
    db_session.flush()
    responses = _FakeModels(categories["Dining"].id)
    _install_fake_gemini(monkeypatch, responses)

    result = import_file(
        db_session,
        _statement_bytes(
            description="SocialPay гүйлгээ,PRIVATE PERSON,TEST",
            counterparty_account="5000000001",
        ),
        "synthetic-transfer.xlsx",
        allow_ai=True,
    )
    transaction = db_session.scalar(select(Transaction))

    assert transaction.txn_type == "own_transfer"
    assert result.ai_suggestions == 0
    assert result.pending_review == 0
    assert responses.calls == []


def test_confirmed_category_is_never_resuggested_or_overwritten(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    import_file(db_session, _statement_bytes(), "synthetic.xlsx")
    transaction = db_session.scalar(select(Transaction))
    transaction.category_id = categories["Groceries"].id
    transaction.category_source = "manual"
    db_session.commit()

    responses = _FakeModels(categories["Dining"].id)
    _install_fake_gemini(monkeypatch, responses)
    result = suggest_categories(db_session, include_pending=True)

    db_session.refresh(transaction)
    assert result.eligible == 0
    assert result.suggestions == 0
    assert responses.calls == []
    assert transaction.category_id == categories["Groceries"].id
    assert transaction.category_source == "manual"


def test_reviewed_suggestion_is_not_requested_again(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    responses = _FakeModels(categories["Dining"].id)
    _install_fake_gemini(monkeypatch, responses)
    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )
    transaction = db_session.scalar(select(Transaction))

    resolve_suggestion(
        db_session,
        transaction.id,
        action="accept",
        category_id=categories["Dining"].id,
    )
    db_session.commit()
    repeated = suggest_categories(db_session, include_pending=True)

    summary = db_session.get(ImportCategorizationSummary, result.batch_id)
    assert result.ai_suggestions == 1
    assert repeated.eligible == 0
    assert len(responses.calls) == 1
    assert transaction.category_source == "manual"
    assert summary.status == "reviewed"
    assert summary.pending_review == 0


def test_rejected_suggestion_is_not_requested_again(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    responses = _FakeModels(categories["Dining"].id)
    _install_fake_gemini(monkeypatch, responses)
    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )
    transaction = db_session.scalar(select(Transaction))

    resolve_suggestion(db_session, transaction.id, action="reject")
    db_session.commit()
    repeated = suggest_categories(db_session, include_pending=True)

    suggestion = db_session.get(CategorizationSuggestion, transaction.id)
    assert result.ai_suggestions == 1
    assert repeated.eligible == 0
    assert suggestion.status == "rejected"
    assert len(responses.calls) == 1


def test_unreviewed_pending_suggestions_can_be_refreshed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    responses = _FakeModels(categories["Dining"].id)
    _install_fake_gemini(monkeypatch, responses)
    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    refreshed = suggest_categories(db_session, include_pending=True)

    assert result.ai_suggestions == 1
    assert refreshed.eligible == 1
    assert refreshed.suggestions == 1
    assert len(responses.calls) == 2
    assert len(db_session.scalars(select(CategorizationSuggestion)).all()) == 1
