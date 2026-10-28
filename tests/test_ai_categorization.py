from __future__ import annotations

import json
from collections.abc import Iterator
from io import BytesIO
import time
from threading import Event
from urllib.error import HTTPError, URLError

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from core import ai_categorization, config
from core.ai_categorization import resolve_suggestion, suggest_categories
from core import categorization_jobs
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


class _FakeOllama:
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

    def __call__(self, payload: dict[str, object]) -> str:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        if self.output_text is not None:
            return self.output_text
        request = json.loads(payload["messages"][1]["content"])
        suggestions = [
            {
                "transaction_ref": transaction["transaction_ref"],
                "category_id": self.category_id,
                "confidence": 0.91,
                "rationale": "The transaction matches this category.",
            }
            for transaction in request["transactions"]
        ]
        return json.dumps({"suggestions": suggestions})


def _install_fake_ollama(
    monkeypatch: pytest.MonkeyPatch, ollama: _FakeOllama
) -> None:
    monkeypatch.setattr(config, "OLLAMA_MODEL", "test-model")
    monkeypatch.setattr(ai_categorization, "_request_ollama", ollama)


def _install_failing_ollama(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    class _FailingOpener:
        def open(self, request: object, timeout: int) -> None:
            raise error

    monkeypatch.setattr(config, "OLLAMA_MODEL", "test-model")
    monkeypatch.setattr(
        config, "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
    )
    monkeypatch.setattr(
        ai_categorization, "build_opener", lambda *args: _FailingOpener()
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
    responses = _FakeOllama(categories["Dining"].id)
    _install_fake_ollama(monkeypatch, responses)

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

    request = json.loads(responses.calls[0]["messages"][1]["content"])
    assert responses.calls[0]["model"] == config.OLLAMA_MODEL
    assert responses.calls[0]["format"]["type"] == "object"
    assert request["transactions"] == [
        {
            "transaction_ref": request["transactions"][0]["transaction_ref"],
            "direction": "out",
            "transaction_type": "card",
            "eligible_category_ids": [
                categories["Dining"].id,
                categories["Groceries"].id,
                categories["Savings"].id,
                categories["Transfers"].id,
                categories["Bank fees"].id,
            ],
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
    responses = _FakeOllama(categories["Groceries"].id)
    _install_fake_ollama(monkeypatch, responses)

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
    responses = _FakeOllama(categories["Dining"].id)
    _install_fake_ollama(monkeypatch, responses)
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
    responses = _FakeOllama(categories["Dining"].id)
    _install_fake_ollama(monkeypatch, responses)

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


def test_missing_ollama_keeps_imported_transaction_for_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_categories(db_session)
    _install_failing_ollama(
        monkeypatch, URLError(ConnectionRefusedError("private connection details"))
    )

    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    assert result.new_rows == 1
    assert result.ai_suggestions == 0
    assert result.pending_review == 1
    assert result.ai_status == "failed"
    assert result.errors == [
        "Could not connect to local Ollama. Check the local service at "
        "http://127.0.0.1:11434 and retry."
    ]
    assert db_session.scalar(select(Transaction)).category_id is None


def test_missing_ollama_model_keeps_imported_transaction_for_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_categories(db_session)
    _install_failing_ollama(
        monkeypatch,
        HTTPError(
            "http://127.0.0.1:11434/api/chat",
            404,
            "Not Found",
            {},
            None,
        ),
    )

    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    assert result.ai_status == "failed"
    assert result.ai_suggestions == 0
    assert result.pending_review == 1
    assert result.errors == [
        "The configured Ollama model is unavailable. Pull the model locally and retry."
    ]
    assert db_session.scalar(select(Transaction)) is not None


def test_ollama_timeout_keeps_imported_transaction_for_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_categories(db_session)
    _install_failing_ollama(
        monkeypatch, TimeoutError("private timeout details")
    )

    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    assert result.ai_status == "failed"
    assert result.pending_review == 1
    assert result.errors == [
        "Local Ollama inference exceeded the 180-second timeout. Check that the model "
        "is loaded, or increase OLLAMA_TIMEOUT_SECONDS and retry."
    ]


def test_malformed_model_response_is_reported_without_rolling_back_import(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_categories(db_session)
    responses = _FakeOllama(1, output_text="{invalid json")
    _install_fake_ollama(monkeypatch, responses)

    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    assert result.new_rows == 1
    assert result.ai_suggestions == 0
    assert result.pending_review == 1
    assert result.ai_status == "failed"
    assert result.errors == ["Ollama returned malformed JSON."]
    assert db_session.scalar(select(Transaction)) is not None
    assert db_session.get(ImportCategorizationSummary, result.batch_id).status == (
        "completed_with_errors"
    )


def test_suggestions_use_batches_of_ten_and_report_progress(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    import_file(db_session, _statement_bytes(), "batched.xlsx", allow_ai=False)
    first = db_session.scalar(select(Transaction))
    for index in range(1, 11):
        db_session.add(
            Transaction(
                account_id=first.account_id,
                batch_id=first.batch_id,
                posted_at=first.posted_at,
                txn_at=first.txn_at,
                amount=first.amount,
                direction="out",
                description=f"Synthetic purchase {index}",
                merchant=f"TEST MERCHANT {index}",
                txn_type="card",
                raw={},
                dedupe_hash=f"{index:064x}",
            )
        )
    db_session.flush()
    responses = _FakeOllama(categories["Dining"].id)
    _install_fake_ollama(monkeypatch, responses)
    progress: list[tuple[int, int]] = []

    result = suggest_categories(
        db_session,
        progress_callback=lambda completed, total: progress.append(
            (completed, total)
        ),
    )

    assert result.error is None
    assert result.eligible == 11
    assert result.suggestions == 11
    assert [
        len(json.loads(call["messages"][1]["content"])["transactions"])
        for call in responses.calls
    ] == [10, 1]
    assert progress == [(0, 11), (10, 11), (11, 11)]
    assert all(
        transaction.category_id is None
        and transaction.category_source == "none"
        for transaction in db_session.scalars(select(Transaction))
    )
    assert len(db_session.scalars(select(CategorizationSuggestion)).all()) == 11


def test_invalid_response_gets_one_corrective_request(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    import_file(db_session, _statement_bytes(), "corrective.xlsx", allow_ai=False)
    calls: list[dict[str, object]] = []

    def request_ollama(payload: dict[str, object]) -> str:
        calls.append(payload)
        if len(calls) == 1:
            return "{malformed"
        input_data = json.loads(payload["messages"][1]["content"])
        return json.dumps(
            {
                "suggestions": [
                    {
                        "transaction_ref": transaction["transaction_ref"],
                        "category_id": categories["Dining"].id,
                        "confidence": 0.9,
                        "rationale": "Matches the dining category.",
                    }
                    for transaction in input_data["transactions"]
                ]
            }
        )

    monkeypatch.setattr(config, "OLLAMA_MODEL", "test-model")
    monkeypatch.setattr(ai_categorization, "_request_ollama", request_ollama)

    result = suggest_categories(db_session)

    assert result.error is None
    assert result.batch_errors == []
    assert result.suggestions == 1
    assert len(calls) == 2
    assert "previous response failed validation" not in calls[0]["messages"][0]["content"]
    assert "previous response failed validation" in calls[1]["messages"][0]["content"]
    transaction = db_session.scalar(select(Transaction))
    suggestion = db_session.get(CategorizationSuggestion, transaction.id)
    assert transaction.category_id is None
    assert transaction.category_source == "none"
    assert suggestion.status == "pending"


def test_failed_batch_continues_to_later_independent_batches(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    import_file(db_session, _statement_bytes(), "independent-batches.xlsx", allow_ai=False)
    first = db_session.scalar(select(Transaction))
    for index in range(1, 21):
        db_session.add(
            Transaction(
                account_id=first.account_id,
                batch_id=first.batch_id,
                posted_at=first.posted_at,
                txn_at=first.txn_at,
                amount=first.amount,
                direction="out",
                description=f"Synthetic purchase {index}",
                merchant=f"TEST MERCHANT {index}",
                txn_type="card",
                raw={},
                dedupe_hash=f"{index + 100:064x}",
            )
        )
    db_session.flush()
    calls: list[dict[str, object]] = []

    def request_ollama(payload: dict[str, object]) -> str:
        calls.append(payload)
        if len(calls) <= 2:
            return "{malformed"
        input_data = json.loads(payload["messages"][1]["content"])
        return json.dumps(
            {
                "suggestions": [
                    {
                        "transaction_ref": transaction["transaction_ref"],
                        "category_id": categories["Dining"].id,
                        "confidence": 0.9,
                        "rationale": "Matches the dining category.",
                    }
                    for transaction in input_data["transactions"]
                ]
            }
        )

    monkeypatch.setattr(config, "OLLAMA_MODEL", "test-model")
    monkeypatch.setattr(ai_categorization, "_request_ollama", request_ollama)
    progress: list[tuple[int, int]] = []

    result = suggest_categories(
        db_session,
        progress_callback=lambda completed, total: progress.append(
            (completed, total)
        ),
    )

    assert len(calls) == 4
    assert "previous response failed validation" in calls[1]["messages"][0]["content"]
    assert result.eligible == 21
    assert result.suggestions == 11
    assert result.error == "Ollama returned malformed JSON."
    assert result.batch_errors == [
        "Ollama returned malformed JSON."
    ]
    assert progress == [(0, 21), (10, 21), (20, 21), (21, 21)]
    assert len(db_session.scalars(select(CategorizationSuggestion)).all()) == 11
    assert all(
        transaction.category_id is None and transaction.category_source == "none"
        for transaction in db_session.scalars(select(Transaction))
    )


def test_suggestion_parser_accepts_fenced_json_and_rejects_invalid_entries(
    db_session: Session,
) -> None:
    categories = _add_categories(db_session)
    import_file(db_session, _statement_bytes(), "parser-validation.xlsx", allow_ai=False)
    transaction = db_session.scalar(select(Transaction))
    transaction_ref = "test-transaction-reference"
    transactions_by_reference = {transaction_ref: transaction}
    valid_suggestion = {
        "transaction_ref": transaction_ref,
        "category_id": categories["Dining"].id,
        "confidence": 0.9,
        "rationale": "Matches the dining category.",
    }

    parsed = ai_categorization._parse_suggestions(
        f"```json\n{json.dumps({'suggestions': [valid_suggestion]})}\n```",
        transactions_by_reference,
        {category.id: category for category in categories.values()},
    )
    assert len(parsed.suggestions) == 1
    assert parsed.suggestions[0].status == "pending"
    assert parsed.diagnostics == []

    unknown = ai_categorization._parse_suggestions(
        json.dumps(
            {
                "suggestions": [
                    {**valid_suggestion, "transaction_ref": "not-submitted"}
                ]
            }
        ),
        transactions_by_reference,
        {category.id: category for category in categories.values()},
    )
    assert unknown.suggestions == []
    assert unknown.diagnostics == [
        "Ollama returned an unknown transaction reference; that item was ignored.",
        "Ollama omitted one or more submitted transaction references; those transactions were not suggested.",
    ]

    missing = ai_categorization._parse_suggestions(
        json.dumps({"suggestions": []}),
        transactions_by_reference,
        {category.id: category for category in categories.values()},
    )
    assert missing.suggestions == []
    assert missing.diagnostics == [
        "Ollama omitted one or more submitted transaction references; those transactions were not suggested."
    ]

    duplicate = ai_categorization._parse_suggestions(
        json.dumps({"suggestions": [valid_suggestion, valid_suggestion]}),
        transactions_by_reference,
        {category.id: category for category in categories.values()},
    )
    assert len(duplicate.suggestions) == 1
    assert duplicate.diagnostics == [
        "Ollama returned a duplicate transaction reference; the duplicate item was ignored."
    ]

    with pytest.raises(
        ai_categorization.InvalidModelResponse,
        match="Ollama returned malformed JSON",
    ):
        ai_categorization._parse_suggestions(
            "{malformed",
            transactions_by_reference,
            {category.id: category for category in categories.values()},
        )

    invalid_cases = [
        {**valid_suggestion, "transaction_ref": 123},
        {**valid_suggestion, "category_id": 999},
        {**valid_suggestion, "category_id": categories["Salary"].id},
    ]
    for suggestion in invalid_cases:
        with pytest.raises(ai_categorization.InvalidModelResponse):
            ai_categorization._parse_suggestions(
                json.dumps({"suggestions": [suggestion]}),
                transactions_by_reference,
                {category.id: category for category in categories.values()},
            )


def test_invalid_ollama_suggestion_is_reported_and_import_still_completes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    responses = _FakeOllama(
        categories["Dining"].id,
        output_text=json.dumps({"suggestions": []}),
    )
    _install_fake_ollama(monkeypatch, responses)

    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    assert result.new_rows == 1
    assert result.ai_suggestions == 0
    assert result.pending_review == 1
    assert result.ai_status == "failed"
    assert result.errors == [
        "Ollama omitted one or more submitted transaction references; those transactions were not suggested."
    ]
    assert db_session.scalar(select(Transaction)) is not None


def test_non_loopback_ollama_url_is_rejected_before_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://example.invalid:11434")
    with pytest.raises(
        ai_categorization.OllamaRequestError,
        match="Ollama must be configured with a local loopback URL",
    ):
        ai_categorization._request_ollama({"model": "test-model"})


def test_ollama_retries_one_transient_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            return b'{"message":{"content":"{\\"suggestions\\":[]}"}}'

    class _Opener:
        attempts = 0

        def open(self, request: object, timeout: int) -> _Response:
            self.attempts += 1
            if self.attempts == 1:
                raise URLError(ConnectionRefusedError("private connection detail"))
            return _Response()

    opener = _Opener()
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(ai_categorization, "build_opener", lambda *args: opener)

    response = ai_categorization._request_ollama({"model": "test-model"})

    assert response == '{"suggestions":[]}'
    assert opener.attempts == 2


def test_ollama_uses_chat_endpoint_and_configured_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            return b'{"message":{"content":"OK"}}'

    class _Opener:
        def open(self, request: object, timeout: int) -> _Response:
            assert request.full_url == "http://localhost:11434/api/chat"
            assert json.loads(request.data) == {
                "model": "llama3.2",
                "stream": False,
                "messages": [{"role": "user", "content": "Reply only OK"}],
            }
            assert timeout == 180
            return _Response()

    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(config, "OLLAMA_TIMEOUT_SECONDS", 180)
    monkeypatch.setattr(ai_categorization, "build_opener", lambda *args: _Opener())

    assert ai_categorization._request_ollama(
        {
            "model": "llama3.2",
            "stream": False,
            "messages": [{"role": "user", "content": "Reply only OK"}],
        }
    ) == "OK"


def test_ollama_timeout_retries_once_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            return b'{"message":{"content":"OK"}}'

    class _Opener:
        attempts = 0

        def open(self, request: object, timeout: int) -> _Response:
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("private timeout detail")
            return _Response()

    opener = _Opener()
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(config, "OLLAMA_TIMEOUT_SECONDS", 180)
    monkeypatch.setattr(ai_categorization, "build_opener", lambda *args: opener)
    monkeypatch.setattr(ai_categorization.time, "sleep", lambda _seconds: None)

    assert ai_categorization._request_ollama({"model": "llama3.2"}) == "OK"
    assert opener.attempts == 2


def test_repeated_ollama_timeouts_stop_after_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Opener:
        attempts = 0

        def open(self, request: object, timeout: int) -> None:
            self.attempts += 1
            raise TimeoutError("private timeout detail")

    opener = _Opener()
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(config, "OLLAMA_TIMEOUT_SECONDS", 180)
    monkeypatch.setattr(ai_categorization, "build_opener", lambda *args: opener)
    monkeypatch.setattr(ai_categorization.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        ai_categorization.OllamaRequestError,
        match="inference exceeded the 180-second timeout",
    ):
        ai_categorization._request_ollama({"model": "llama3.2"})
    assert opener.attempts == 2


def test_non_transient_http_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Opener:
        attempts = 0

        def open(self, request: object, timeout: int) -> None:
            self.attempts += 1
            raise HTTPError(
                "http://127.0.0.1:11434/api/chat",
                400,
                "Bad Request",
                {},
                None,
            )

    opener = _Opener()
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(ai_categorization, "build_opener", lambda *args: opener)

    with pytest.raises(
        ai_categorization.OllamaRequestError,
        match="rejected the categorization request \\(HTTP 400\\)",
    ):
        ai_categorization._request_ollama({"model": "llama3.2"})
    assert opener.attempts == 1


def test_transient_server_http_error_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Opener:
        attempts = 0

        def open(self, request: object, timeout: int) -> None:
            self.attempts += 1
            raise HTTPError(
                "http://127.0.0.1:11434/api/chat",
                500,
                "Internal Server Error",
                {},
                None,
            )

    opener = _Opener()
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(ai_categorization, "build_opener", lambda *args: opener)
    monkeypatch.setattr(ai_categorization.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        ai_categorization.OllamaRequestError,
        match="returned HTTP 500 after one retry",
    ):
        ai_categorization._request_ollama({"model": "llama3.2"})
    assert opener.attempts == 2


def test_malformed_ollama_http_response_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            return b"{invalid"

    class _Opener:
        def open(self, request: object, timeout: int) -> _Response:
            return _Response()

    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(ai_categorization, "build_opener", lambda *args: _Opener())

    with pytest.raises(
        ai_categorization.InvalidModelResponse,
        match="Ollama returned malformed JSON",
    ):
        ai_categorization._request_ollama({"model": "llama3.2"})


def test_background_job_creates_pending_suggestions_once(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'background.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    _add_categories_session = factory()
    try:
        categories = _add_categories(_add_categories_session)
        imported = import_file(
            _add_categories_session,
            _statement_bytes(),
            "background.xlsx",
            allow_ai=False,
        )
        _add_categories_session.commit()
        response = _FakeOllama(categories["Dining"].id)
        _install_fake_ollama(monkeypatch, response)
        monkeypatch.setattr(
            categorization_jobs, "get_session", lambda: factory()
        )

        job_id = categorization_jobs.enqueue_categorization(imported.batch_id)
        deadline = time.monotonic() + 5
        job = categorization_jobs.get_categorization_job(job_id)
        while job and job["status"] in {"queued", "running"}:
            if time.monotonic() >= deadline:
                pytest.fail("Background categorization did not finish in time.")
            time.sleep(0.01)
            job = categorization_jobs.get_categorization_job(job_id)

        assert job is not None
        assert job["status"] == "completed"
        assert job["completed"] == 1
        assert job["total"] == 1
        assert categorization_jobs.enqueue_categorization(imported.batch_id) == job_id
        assert len(response.calls) == 1

        with factory() as verify_session:
            transaction = verify_session.scalar(select(Transaction))
            suggestion = verify_session.get(
                CategorizationSuggestion, transaction.id
            )
            summary = verify_session.get(
                ImportCategorizationSummary, imported.batch_id
            )
            assert transaction.category_id is None
            assert transaction.category_source == "none"
            assert suggestion.status == "pending"
            assert suggestion.category_id == categories["Dining"].id
            assert summary.ai_status == "completed"
    finally:
        _add_categories_session.close()
        engine.dispose()


def test_background_job_reports_partial_failure_and_keeps_successful_suggestions(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'background-partial.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    import_session = factory()
    try:
        categories = _add_categories(import_session)
        imported = import_file(
            import_session,
            _statement_bytes(),
            "background-partial.xlsx",
            allow_ai=False,
        )
        first = import_session.scalar(select(Transaction))
        for index in range(1, 11):
            import_session.add(
                Transaction(
                    account_id=first.account_id,
                    batch_id=first.batch_id,
                    posted_at=first.posted_at,
                    txn_at=first.txn_at,
                    amount=first.amount,
                    direction="out",
                    description=f"Synthetic purchase {index}",
                    merchant=f"TEST MERCHANT {index}",
                    txn_type="card",
                    raw={},
                    dedupe_hash=f"{index + 200:064x}",
                )
            )
        import_session.commit()
        monkeypatch.setattr(config, "OLLAMA_MODEL", "test-model")
        monkeypatch.setattr(categorization_jobs, "get_session", lambda: factory())
        calls = 0

        def request_ollama(payload: dict[str, object]) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ai_categorization.OllamaRequestError(
                    "Could not connect to local Ollama. Check the local service and retry."
                )
            input_data = json.loads(payload["messages"][1]["content"])
            return json.dumps(
                {
                    "suggestions": [
                        {
                            "transaction_index": index,
                            "category_id": categories["Dining"].id,
                            "confidence": 0.9,
                            "rationale": "Matches the dining category.",
                        }
                        for index, _ in enumerate(input_data["transactions"])
                    ]
                }
            )

        monkeypatch.setattr(ai_categorization, "_request_ollama", request_ollama)
        job_id = categorization_jobs.enqueue_categorization(
            imported.batch_id, retry=True
        )
        deadline = time.monotonic() + 5
        job = categorization_jobs.get_categorization_job(job_id)
        while job and job["status"] in {"queued", "running"}:
            if time.monotonic() >= deadline:
                pytest.fail("Partially failed background categorization did not finish.")
            time.sleep(0.01)
            job = categorization_jobs.get_categorization_job(job_id)

        assert job is not None
        assert job["status"] == "partial_failure"
        assert job["completed"] == 11
        assert job["total"] == 11
        assert "Could not connect to local Ollama" in job["error"]
        assert calls == 2

        with factory() as verify_session:
            transactions = verify_session.scalars(select(Transaction)).all()
            suggestions = verify_session.scalars(
                select(CategorizationSuggestion)
            ).all()
            summary = verify_session.get(
                ImportCategorizationSummary, imported.batch_id
            )
            assert len(suggestions) == 1
            assert suggestions[0].status == "pending"
            assert all(
                transaction.category_id is None
                and transaction.category_source == "none"
                for transaction in transactions
            )
            assert summary.ai_status == "partial"
            assert len(summary.errors) == 1
    finally:
        import_session.close()
        engine.dispose()


def test_queued_job_cancellation_updates_import_summary(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'background-cancel.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _add_categories(session)
        imported = import_file(
            session,
            _statement_bytes(),
            "background-cancel.xlsx",
            allow_ai=False,
        )

    class _CancelledFuture:
        def cancel(self) -> bool:
            return True

    class _QueuedExecutor:
        def submit(self, *_args: object) -> _CancelledFuture:
            return _CancelledFuture()

    monkeypatch.setattr(categorization_jobs, "get_session", lambda: factory())
    monkeypatch.setattr(categorization_jobs, "_executor", _QueuedExecutor())
    try:
        job_id = categorization_jobs.enqueue_categorization(
            imported.batch_id, retry=True
        )
        assert categorization_jobs.get_categorization_job(job_id)["status"] == "queued"
        assert categorization_jobs.cancel_categorization_job(job_id) is True
        job = categorization_jobs.get_categorization_job(job_id)
        assert job is not None and job["status"] == "cancelled"
        with factory() as session:
            summary = session.get(ImportCategorizationSummary, imported.batch_id)
            assert summary.ai_status == "cancelled"
            assert summary.errors == []
    finally:
        engine.dispose()


def test_unexpected_background_exception_marks_import_failed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'background-exception.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _add_categories(session)
        imported = import_file(
            session,
            _statement_bytes(),
            "background-exception.xlsx",
            allow_ai=False,
        )

    monkeypatch.setattr(categorization_jobs, "get_session", lambda: factory())

    def fail_suggestion(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("private synthetic failure detail")

    monkeypatch.setattr(categorization_jobs, "suggest_categories", fail_suggestion)
    job_id = f"test-{time.monotonic_ns()}"
    with categorization_jobs._lock:
        categorization_jobs._jobs[job_id] = categorization_jobs.CategorizationJob(
            job_id=job_id, batch_id=imported.batch_id
        )
    try:
        categorization_jobs._run_job(job_id, include_pending=False)
        job = categorization_jobs.get_categorization_job(job_id)
        assert job is not None and job["status"] == "failed"
        assert "private synthetic failure detail" not in job["error"]
        with factory() as session:
            summary = session.get(ImportCategorizationSummary, imported.batch_id)
            assert summary.ai_status == "failed"
            assert summary.errors == [job["error"]]
    finally:
        with categorization_jobs._lock:
            del categorization_jobs._jobs[job_id]
        engine.dispose()


def test_background_failure_reports_progress_and_retry_creates_one_pending_suggestion(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'background-retry.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    import_session = factory()
    request_started = Event()
    allow_failure = Event()
    request_count = 0
    try:
        categories = _add_categories(import_session)
        imported = import_file(
            import_session,
            _statement_bytes(),
            "background-retry.xlsx",
            allow_ai=False,
        )
        import_session.commit()
        monkeypatch.setattr(config, "OLLAMA_MODEL", "test-model")
        monkeypatch.setattr(categorization_jobs, "get_session", lambda: factory())

        def request_ollama(payload: dict[str, object]) -> str:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                request_started.set()
                assert allow_failure.wait(5)
                raise ai_categorization.OllamaRequestError(
                    "Local Ollama inference exceeded its configured timeout."
                )
            input_data = json.loads(payload["messages"][1]["content"])
            return json.dumps(
                {
                    "suggestions": [
                        {
                            "transaction_index": index,
                            "category_id": categories["Dining"].id,
                            "confidence": 0.9,
                            "rationale": "Matches the dining category.",
                        }
                        for index, _ in enumerate(input_data["transactions"])
                    ]
                }
            )

        monkeypatch.setattr(ai_categorization, "_request_ollama", request_ollama)
        failed_job_id = categorization_jobs.enqueue_categorization(
            imported.batch_id, retry=True
        )
        assert request_started.wait(5)
        running_job = categorization_jobs.get_categorization_job(failed_job_id)
        assert running_job is not None
        assert running_job["status"] == "running"
        assert running_job["completed"] == 0
        assert running_job["total"] == 1
        assert categorization_jobs.enqueue_categorization(
            imported.batch_id
        ) == failed_job_id

        allow_failure.set()
        deadline = time.monotonic() + 5
        failed_job = categorization_jobs.get_categorization_job(failed_job_id)
        while failed_job and failed_job["status"] in {"queued", "running"}:
            if time.monotonic() >= deadline:
                pytest.fail("Failed background categorization did not finish in time.")
            time.sleep(0.01)
            failed_job = categorization_jobs.get_categorization_job(failed_job_id)
        assert failed_job is not None
        assert failed_job["status"] == "failed"
        assert failed_job["completed"] == 1
        assert failed_job["total"] == 1

        with factory() as verify_session:
            transaction = verify_session.scalar(select(Transaction))
            assert transaction.category_id is None
            assert transaction.category_source == "none"
            assert verify_session.scalar(
                select(CategorizationSuggestion).where(
                    CategorizationSuggestion.transaction_id == transaction.id
                )
            ) is None

        retry_job_id = categorization_jobs.enqueue_categorization(
            imported.batch_id, retry=True
        )
        deadline = time.monotonic() + 5
        retry_job = categorization_jobs.get_categorization_job(retry_job_id)
        while retry_job and retry_job["status"] in {"queued", "running"}:
            if time.monotonic() >= deadline:
                pytest.fail("Background categorization retry did not finish in time.")
            time.sleep(0.01)
            retry_job = categorization_jobs.get_categorization_job(retry_job_id)
        assert retry_job is not None
        assert retry_job["status"] == "completed"
        assert retry_job["completed"] == 1
        assert retry_job["total"] == 1
        assert request_count == 2

        with factory() as verify_session:
            transaction = verify_session.scalar(select(Transaction))
            suggestions = verify_session.scalars(
                select(CategorizationSuggestion).where(
                    CategorizationSuggestion.transaction_id == transaction.id
                )
            ).all()
            assert transaction.category_id is None
            assert transaction.category_source == "none"
            assert len(suggestions) == 1
            assert suggestions[0].status == "pending"
            assert suggestions[0].category_id == categories["Dining"].id
    finally:
        allow_failure.set()
        import_session.close()
        engine.dispose()


def test_suggestion_cancellation_does_not_send_ai_request(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_categories(db_session)
    import_file(db_session, _statement_bytes(), "cancel.xlsx", allow_ai=False)
    response = _FakeOllama(1)
    _install_fake_ollama(monkeypatch, response)

    result = suggest_categories(
        db_session,
        cancel_check=lambda: True,
    )

    assert result.cancelled is True
    assert result.suggestions == 0
    assert result.error is not None
    assert response.calls == []


def test_verified_own_transfer_is_not_sent_to_ai_or_counted_for_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories = _add_categories(db_session)
    db_session.add(OwnAccount(account_no="5000000001", label="Verified account"))
    db_session.flush()
    responses = _FakeOllama(categories["Dining"].id)
    _install_fake_ollama(monkeypatch, responses)

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

    responses = _FakeOllama(categories["Dining"].id)
    _install_fake_ollama(monkeypatch, responses)
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
    responses = _FakeOllama(categories["Dining"].id)
    _install_fake_ollama(monkeypatch, responses)
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
    responses = _FakeOllama(categories["Dining"].id)
    _install_fake_ollama(monkeypatch, responses)
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
    responses = _FakeOllama(categories["Dining"].id)
    _install_fake_ollama(monkeypatch, responses)
    result = import_file(
        db_session, _statement_bytes(), "synthetic.xlsx", allow_ai=True
    )

    refreshed = suggest_categories(db_session, include_pending=True)

    assert result.ai_suggestions == 1
    assert refreshed.eligible == 1
    assert refreshed.suggestions == 1
    assert len(responses.calls) == 2
    assert len(db_session.scalars(select(CategorizationSuggestion)).all()) == 1
