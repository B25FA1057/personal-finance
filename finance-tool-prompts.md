# Personal Finance Tool — Build Prompts for Free AI Coding Agents

16 prompts, used in order. Each prompt builds one small piece and ends with a check you run yourself.
Expected build time: 1–3 evenings.

---

## How to use these prompts (read once)

1. **One prompt = one new chat.** Fresh chats use far less of your free quota than long ones, and agents make fewer mistakes.
2. **Always paste the CONTEXT block first**, then the step prompt, in the same message.
3. **Agent choice**
   - Best: **GitHub Copilot Free in VS Code, Agent mode** — it can see and write your project files directly.
   - Chat-only agents (ChatGPT, Gemini, Claude free web): also paste the files listed under "Files the agent needs" (copy their contents in). They reply with code; you save it into the files yourself.
   - Spread steps across agents when you hit a daily limit — the prompts are self-contained, so switching is safe.
4. **After every step:** run the CHECK command. If it passes → `git add . && git commit -m "step N"`. If it fails → use the **FIX prompt** (bottom of this file) in a new chat.
5. **Privacy:** never paste your real bank statement, real account numbers or real names into a free AI. The prompts use a fake test statement. Your real accounts are entered later in the app's Settings page, on your own PC.
6. If an answer is cut off, say: `Continue exactly where you stopped. Output only the remaining files.`

### One-time setup on your PC (do this yourself, no AI needed)
- Install **Python 3.12**, **PostgreSQL 16** (remember the password you set for user `postgres`), **Git**, **VS Code**.
- In pgAdmin or `psql`, run: `CREATE DATABASE finance;` and `CREATE DATABASE finance_test;`
- Create an empty folder `finance`, open it in VS Code, run `git init`.

---

## CONTEXT block (paste at the top of EVERY prompt)

```text
PROJECT CONTEXT (do not restate it back to me):
Local, single-user personal finance app. Python 3.12, PostgreSQL 16, SQLAlchemy 2.x (typed Mapped[] style), psycopg 3 driver, pandas + openpyxl, Streamlit UI, Plotly charts, pytest. Runs only on my Windows PC, no server, no auth.
Purpose: I upload bank statement .xlsx files periodically; app parses them, stores transactions in Postgres without duplicates, auto-classifies them (rules + per-merchant defaults), lists new merchants for me to classify, and lets me classify manually. First bank: Golomt Bank (Mongolia). Others added later via parser plugins.
Folder layout:
finance/ app.py, start.bat, requirements.txt, .env.example, README.md
finance/core/ config.py db.py models.py seed.py enrich.py dedupe.py classify.py importer.py queries.py
finance/core/parsers/ __init__.py base.py golomt.py registry.py
finance/pages/ 1_Upload.py 2_Review.py 3_Transactions.py 4_Dashboard.py 5_Settings.py
finance/scripts/ backup.py
finance/tests/ conftest.py fixtures/make_golomt_fixture.py test_*.py
Currency MNT. Money = Decimal, NUMERIC(14,2) in DB. Datetimes are naive local time.
OUTPUT RULES: Give complete file contents only for the files I ask for, each under a header with its path. No explanations, no placeholders, no "..." omissions, no TODOs. Do not modify files I did not list. Keep code simple and readable; type hints on all functions.
```

---

## STEP 1 — Project skeleton

```text
[paste CONTEXT block]

TASK: Create the project skeleton.
Files to create:
1. requirements.txt — pinned compatible versions: sqlalchemy>=2.0,<2.1; psycopg[binary]>=3.1; pandas>=2.2; openpyxl>=3.1; streamlit>=1.38; plotly>=5.22; python-dotenv>=1.0; pytest>=8.
2. .env.example:
   DATABASE_URL=postgresql+psycopg://postgres:CHANGE_ME@localhost:5432/finance
   TEST_DATABASE_URL=postgresql+psycopg://postgres:CHANGE_ME@localhost:5432/finance_test
   PG_DUMP_PATH=C:\Program Files\PostgreSQL\16\bin\pg_dump.exe
3. .gitignore — Python defaults plus .env, backups/, *.xlsx except tests/fixtures/*.xlsx is generated so ignore it too, .venv/.
4. core/__init__.py, core/parsers/__init__.py, tests/__init__.py — empty.
5. core/config.py:
   - load .env with python-dotenv (from project root, path-independent of cwd).
   - DATABASE_URL: str, TEST_DATABASE_URL: str | None, PG_DUMP_PATH: str | None.
   - QPAY_FEE_ACCOUNTS: frozenset[str] = frozenset({"2015113727"})
   - FEE_LINK_WINDOW_SECONDS = 15 ; CARD_REF_TOLERANCE_SECONDS = 5
   - Raise a clear RuntimeError if DATABASE_URL missing.
6. start.bat — cd to its own folder, activate .venv\Scripts\activate.bat, run: streamlit run app.py
7. README.md — short: setup (python -m venv .venv, pip install -r requirements.txt, copy .env.example to .env and edit, python -m core.seed, start.bat), run tests (pytest -q).

CHECK I will run: python -m venv .venv ; .venv\Scripts\activate ; pip install -r requirements.txt ; python -c "import core.config as c; print(c.DATABASE_URL)"
```

---

## STEP 2 — Database models

```text
[paste CONTEXT block]

TASK: Create core/db.py and core/models.py.

core/db.py:
- Base = DeclarativeBase subclass.
- get_engine(url: str | None = None) -> Engine (default config.DATABASE_URL, future style, pool_pre_ping=True). Cache engines per URL in a module dict.
- get_session(url: str | None = None) -> Session (sessionmaker, expire_on_commit=False).
- init_db(url: str | None = None) -> None: imports core.models then Base.metadata.create_all.

core/models.py — exactly these tables (SQLAlchemy 2 Mapped/mapped_column, server defaults where noted):
banks: id PK, code str(20) unique not null, name str(100).
accounts: id PK, bank_id FK banks.id, account_no str(34) unique not null, iban str(34) null, currency str(3) default "MNT", holder_name str(200) null, label str(100) null.
own_accounts: id PK, account_no str(34) unique not null, label str(100) null.   # my other accounts (any bank) used to detect transfers between my own accounts
import_batches: id PK, account_id FK accounts.id, file_name str(255), file_sha256 str(64) unique not null, period_from date null, period_to date null, imported_at datetime default now, total_rows int, new_rows int, duplicate_rows int.
categories: id PK, name str(100) unique not null, kind str(10) not null CHECK kind IN ('income','expense','savings','transfer'), parent_id FK categories.id null, is_default bool default false, sort_order int default 100.
counterparties: id PK, key str(200) unique not null, kind str(20) not null CHECK kind IN ('merchant','person','own_account','biller','bank_fee','other'), display_name str(200), default_category_id FK categories.id null, reviewed bool default false, created_at datetime default now.
rules: id PK, priority int default 100, field str(30) CHECK IN ('description','merchant','counterparty_account','txn_type'), match_type str(20) CHECK IN ('contains','startswith','equals','regex'), pattern str(300) not null, category_id FK categories.id not null, active bool default true.
transactions: id PK, account_id FK accounts.id not null, batch_id FK import_batches.id ON DELETE CASCADE not null,
  posted_at datetime not null, txn_at datetime not null, amount Numeric(14,2) not null (always positive), direction str(3) CHECK IN ('in','out'),
  balance_before Numeric(14,2) null, balance_after Numeric(14,2) null, description text not null, counterparty_account str(34) null,
  card_mask str(30) null, channel str(10) null, merchant str(100) null, txn_type str(20) not null CHECK IN ('card','fee','p2p_out','p2p_in','qpay','topup','own_transfer','other'),
  counterparty_id FK counterparties.id null, parent_txn_id FK transactions.id ON DELETE SET NULL null,
  category_id FK categories.id null, category_source str(15) default 'none' CHECK IN ('manual','counterparty','rule','none'),
  note text null, raw JSONB not null, dedupe_hash str(64) unique not null.
  Indexes on posted_at, txn_at, category_id, counterparty_id.
Add relationships only where useful (transaction.category, transaction.counterparty, counterparty.default_category, batch.transactions with cascade delete-orphan).

CHECK: python -c "from core.db import init_db; init_db(); print('ok')"  then confirm tables exist in pgAdmin.
```

---

## STEP 3 — Default categories and starter rules

```text
[paste CONTEXT block]

Files the agent needs: core/models.py, core/db.py (paste if your agent can't read the repo).

TASK: Create core/seed.py, runnable as `python -m core.seed`. Idempotent (running twice changes nothing; match by name / by (field,match_type,pattern)).
1. init_db().
2. Insert bank: code "GOLOMT", name "Golomt Bank".
3. Categories (name, kind), all is_default=True, sort_order in listed order:
   income: Self-earned, Gift / donation received, Reimbursement from others, Refund, Interest, Other income
   expense: Groceries, Dining & cafés, Personal care, Healthcare, Transport, Phone & internet, Subscriptions & online, Fitness, Shopping, Education & printing, Housing & utilities, Entertainment, Gifts given, Transfers to people, Bank fees, Other expense
   savings: Savings
   transfer: Own account transfer
4. Rules (priority, field, match_type, pattern, category name):
   (10,'txn_type','equals','fee','Bank fees')
   (10,'txn_type','equals','own_transfer','Own account transfer')
   (50,'txn_type','equals','topup','Phone & internet')
   (60,'merchant','startswith','UNITEL','Phone & internet')
   (60,'merchant','contains','UBCARD','Transport')
   (60,'merchant','startswith','CU-','Groceries')
   (60,'merchant','contains','APPLE','Subscriptions & online')
   (60,'merchant','contains','GYM','Fitness')
   (60,'merchant','contains','KFC','Dining & cafés')
   (60,'merchant','contains','COFF','Dining & cafés')
5. Print counts inserted.

CHECK: python -m core.seed  (run twice; second run inserts 0)
```

---

## STEP 4 — Fake test statement (fixture)

```text
[paste CONTEXT block]

TASK: Create tests/fixtures/make_golomt_fixture.py with function build(path: Path) -> Path that writes a fake Golomt statement .xlsx using openpyxl, and a __main__ that writes tests/fixtures/golomt_sample.xlsx. Also create tests/conftest.py with a session fixture `golomt_xlsx` that calls build() into tmp_path_factory and returns the path.

Exact layout (single sheet titled "OperativeAccountStatementRaw"). All cell values are STRINGS except numeric amounts/balances which are floats; empty amount cells are "" (empty string). Dates are ISO strings exactly as given.
Row 1 (11 cells): "Хуулганы огноо","2026-10-14","ДАНСНЫ ХУУЛГА","Харилцагчийн нэр","ТЕСТ ","Гүйлгээний огноо","2026-09-14 - 2026-10-14","Дансны дугаар","1800000001 [MNT]","IBAN","MN00 0015 0018 0000 0001"
Row 2: "Гүйлгээний огноо","Эхний үлдэгдэл","Зарлага","Орлого","Эцсийн үлдэгдэл","Гүйлгээний утга","Харьцсан данс"
Rows 3+ (columns: date | balance_before | out | in | balance_after | description | counterparty_account), in THIS order:
2026-09-14T13:37:46 | 150000.00 |  | 20000 | 170000.00 | O-ТЕСТ ХЭРЭГЛЭГЧ | 5000000001
2026-09-14T13:37:51 | 170000.00 | 50 |  | 169950.00 | EASYINFO fee for S11111111,2026-09-14 |
2026-09-14T13:39:40 | 169950.00 | 50 |  | 169900.00 | EASYINFO fee for BOM20260914133933528613,2026-09-1 |
2026-09-15T10:38 | 169900.00 | 8500 |  | 161400.00 | 400000******1111:14-09-2026 13:39:34:BOM:SEZIS BUF |
2026-09-15T20:53:40 | 161400.00 | 5000 |  | 156400.00 | SY5000 99112233!F | 8115006437
2026-09-16T10:48:43 | 156400.00 | 5000 |  | 151400.00 | 400000******2222:15-09-2026 06:42:27:POS:UBCARD AP |
2026-09-21T10:04:51 | 151400.00 |  | 95000 | 246400.00 | I-БАТ ДОРЖ | 5402600001
2026-09-26T13:40:31 | 246400.00 | 97070 |  | 149330.00 | 400000******1111:25-09-2026 16:57:20:POS:MILD COSM |
2026-09-29T12:40:54 | 149330.00 | 200 |  | 149130.00 | Charges for PORD Customer Payment :000658547340 | MN440032005006000001
2026-09-29T12:40:54 | 149130.00 | 17500 |  | 131630.00 | SocialPay гүйлгээ,ЦЭЦЭГ БОЛД,ХАС | MN440032005006000001
2026-09-29T13:27:25 | 131630.00 |  | 52500 | 184130.00 | ШИЛЖҮҮЛЭГЧ ЦЭЦЭГ-ЦЭЦЭГ БОЛД | 5006000001
2026-10-01T17:12:04 | 184130.00 | 72981.95 |  | 111148.05 | 400000******1111:29-09-2026 13:28:49:MCI:APPLE COM |
2026-10-04T13:08:04 | 111108.05 | 3960 |  | 107148.05 | qpay 101782630891166, ccp-2494a138- 40a0 | MN680005005021900000
2026-10-04T13:08:04 | 111148.05 | 40 |  | 111108.05 | QPAY 335818730921540 ТЕСТ ХХК 4000 TRX | 2015113727
2026-10-08T21:01:02 | 107148.05 |  | 110000 | 217148.05 | O-БААТАР САРУУЛ | 5032300001
2026-10-09T10:02:06 | 217148.05 | 106500 |  | 110648.05 | 400000******1111:08-10-2026 21:01:13:BOM:KFC ZURAG |
2026-10-09T12:57:13 | 110648.05 | 25350 |  | 85298.05 | 400000******1111:07-10-2026 14:12:55:POS:CU-281 KH |
2026-10-10T11:30:02 | 85298.05 | 3000 |  | 82298.05 | 99000000!OD3DAY!F | 2020001511
2026-10-11T13:50:52 | 82298.05 | 4950 |  | 77348.05 | 400000******1111:09-10-2026 15:06:07:POS:CU-24 SHO |
2026-10-14T09:40:25 | 77348.05 | 12700 |  | 64648.05 | 400000******1111:13-10-2026 13:09:39:POS:SEZS - TS |
(empty cell = "" ; note two rows are intentionally out of balance order and one date has no seconds — keep exactly as given.)

CHECK: python tests/fixtures/make_golomt_fixture.py  → open the xlsx and eyeball it.
```

---

## STEP 5 — Golomt parser

```text
[paste CONTEXT block]

Files the agent needs: tests/fixtures/make_golomt_fixture.py, tests/conftest.py.

TASK: Create core/parsers/base.py, core/parsers/golomt.py, core/parsers/registry.py, tests/test_golomt_parser.py.

base.py:
@dataclass RawTxn: posted_at: datetime; amount: Decimal (positive); direction: Literal["in","out"]; balance_before: Decimal | None; balance_after: Decimal | None; description: str; counterparty_account: str | None; raw: dict[str, str]  (original row values as strings, JSON-safe)
@dataclass ParsedStatement: bank_code: str; account_no: str; iban: str | None; currency: str; holder_name: str | None; period_from: date | None; period_to: date | None; transactions: list[RawTxn]; warnings: list[str] = field(default_factory=list)
class BankParser(ABC): bank_code: ClassVar[str]; bank_name: ClassVar[str]; @abstractmethod detect(self, wb: openpyxl.Workbook) -> bool; @abstractmethod parse(self, wb) -> ParsedStatement
Helpers in base.py: to_decimal(v) -> Decimal | None ("" / None -> None; strings with commas/spaces ok; quantize 0.01), parse_dt(v) -> datetime (accepts datetime objects or ISO strings with or without seconds, uses datetime.fromisoformat after strip).

golomt.py — GolomtParser(BankParser), bank_code "GOLOMT", bank_name "Golomt Bank":
- Use wb.active sheet.
- detect(): True only if row 2 cell A == "Гүйлгээний огноо" AND row 1 contains label "IBAN" whose next cell, with spaces removed, has chars [4:8] == "0015".
- Row 1 is label/value pairs: find a label cell then take the cell immediately to its right. Labels: "Харилцагчийн нэр"->holder_name (strip), "Дансны дугаар"->account_no (text before " [", strip) and currency (inside brackets, default MNT), "IBAN"->iban (spaces removed), "Гүйлгээний огноо" (in row 1 only)->period "YYYY-MM-DD - YYYY-MM-DD" -> period_from/period_to.
- Rows 3+: stop at first row whose column A is empty. out cell non-empty -> direction "out", amount=out; else in -> "in". Skip a row with both empty (add warning). counterparty_account: stripped string or None if empty.
- Validation (append to warnings, never raise): per row, balance_before -/+ amount must equal balance_after (report row number); and first row balance_before + sum(in) - sum(out) must equal last row balance_after.

registry.py: PARSERS: list[BankParser] = [GolomtParser()]; detect_parser(wb) -> BankParser (raise ValueError("Unsupported statement format") if none); load_workbook_from_bytes(data: bytes) -> Workbook (openpyxl, data_only=True).

tests/test_golomt_parser.py (use golomt_xlsx fixture):
- detect True; a workbook with different A2 -> False.
- account_no "1800000001", currency "MNT", iban "MN000015001800000001", holder_name "ТЕСТ", period 2026-09-14..2026-10-14.
- 20 transactions; sum in == Decimal("277500.00"); sum out == Decimal("362851.95"); warnings == [].
- row 4 posted_at == datetime(2026,9,15,10,38).
- first txn direction "in", amount 20000, counterparty_account "5000000001".

CHECK: pytest -q tests/test_golomt_parser.py
```

---

## STEP 6 — Description enrichment and fee linking

```text
[paste CONTEXT block]

Files the agent needs: core/parsers/base.py, core/config.py.

TASK: Create core/enrich.py and tests/test_enrich.py. Pure functions, no DB.

@dataclass EnrichedTxn: raw: RawTxn; txn_at: datetime; txn_type: str; card_mask: str | None; channel: str | None; merchant: str | None; counterparty_account: str | None (normalized); counterparty_key: str | None; counterparty_kind: str | None; counterparty_name: str | None; fee_ref: str | None; parent_index: int | None = None

normalize_account(s: str | None) -> str | None: strip; empty -> None; if matches ^MN\d{2}(\d{4})(\d{12})$ return group2.lstrip("0"); else return s.
normalize_merchant(s) -> str: upper, strip, collapse internal whitespace to single space.
CARD_RE = r"^(\d{6}\*+\d{4}):(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}):([A-Z]+):(.+)$"  (time inside is DD-MM-YYYY HH:MM:SS)

enrich(t: RawTxn, my_accounts: set[str]) -> EnrichedTxn. Default txn_at = posted_at. acc = normalize_account(t.counterparty_account). d = description.strip(), U = d.upper(). First match wins:
1. U startswith "EASYINFO FEE FOR" -> fee; key "FEE:EASYINFO", kind bank_fee, name "SMS alert fee"; fee_ref = token after "for " up to the comma, stripped.
2. U startswith "CHARGES FOR PORD" -> fee; key "FEE:PORD", kind bank_fee, name "Transfer fee".
3. U startswith "QPAY" and acc in config.QPAY_FEE_ACCOUNTS -> fee; key "FEE:QPAY", kind bank_fee, name "QPay fee".
4. CARD_RE match -> card; card_mask, txn_at = purchase time, channel, merchant = normalize_merchant(group4); key "M:"+merchant, kind merchant, name merchant.
5. U startswith "SOCIALPAY ГҮЙЛГЭЭ," and direction out -> p2p_out; name = 2nd comma part stripped; kind person.
6. U startswith "QPAY" -> qpay; kind biller; name "QPay " + acc (or "QPay").
7. d endswith "!F" -> topup; kind biller; name "Mobile top-up".
8. direction in and acc -> p2p_in; name = text after the LAST "-" stripped (whole text if no "-"); kind person.
9. else other; kind other; name = d[:60].
Key for 5–9 = "A:"+acc if acc else ("QPAY" for qpay, "OTHER:"+name otherwise).
Then: if acc in my_accounts and txn_type in (p2p_in, p2p_out, other) -> txn_type own_transfer, kind own_account.

link_fees(items: list[EnrichedTxn]) -> None: sets parent_index on fee items (indexes into the list), never on non-fees:
- FEE:PORD and FEE:QPAY: parent = non-fee item with identical posted_at and same counterparty_account; else None.
- FEE:EASYINFO with fee_ref starting "POS"/"BOM"/"MCI": parse YYYYMMDDHHMMSS from ref chars [3:17]; parent = card item with same channel and |txn_at - ref_time| <= config.CARD_REF_TOLERANCE_SECONDS.
- Other FEE:EASYINFO: parent = nearest earlier non-fee item with 0 <= fee.posted_at - item.posted_at <= config.FEE_LINK_WINDOW_SECONDS.

tests/test_enrich.py using the golomt_xlsx fixture + GolomtParser, my_accounts={"5000000001"}:
- txn_type counts: card 8, fee 4, p2p_in 3, own_transfer 1, p2p_out 1, qpay 1, topup 2.
- 8 distinct merchants including "SEZIS BUF","SEZS - TS","CU-281 KH".
- SEZIS BUF txn_at == datetime(2026,9,14,13,39,34).
- normalize_account("MN440032005006000001") == "5006000001".
- SocialPay p2p_out key == "A:5006000001" and the p2p_in "ШИЛЖҮҮЛЭГЧ ЦЭЦЭГ-ЦЭЦЭГ БОЛД" has the same key, name "ЦЭЦЭГ БОЛД".
- after link_fees: S11111111 fee -> first row; BOM20260914133933 fee -> SEZIS BUF row; PORD fee -> SocialPay row; QPAY 40 fee -> qpay 3960 row.

CHECK: pytest -q tests/test_enrich.py
```

---

## STEP 7 — Duplicate detection and importer

```text
[paste CONTEXT block]

Files the agent needs: core/models.py, core/db.py, core/parsers/base.py, core/parsers/registry.py, core/enrich.py, tests/conftest.py.

TASK: Create core/dedupe.py, core/importer.py, tests/test_importer.py; extend tests/conftest.py (keep existing content) with a `db_session` fixture.

dedupe.py:
- file_sha256(data: bytes) -> str
- txn_hash(account_no: str, t: RawTxn) -> str = sha256 of f"{account_no}|{posted_at:%Y-%m-%dT%H:%M:%S}|{direction}|{amount:.2f}|{balance_after or ''}|{description.strip()}"

importer.py:
@dataclass ImportResult: batch_id: int | None; bank_code: str; account_no: str; total_rows: int; new_rows: int; duplicate_rows: int; warnings: list[str]; new_counterparty_ids: list[int]; already_imported: bool = False
import_file(session: Session, data: bytes, file_name: str) -> ImportResult:
1. sha = file_sha256(data); if an ImportBatch with this sha exists -> return result with already_imported=True, counts 0, no DB changes.
2. wb = load_workbook_from_bytes; parser = detect_parser(wb); st = parser.parse(wb).
3. Get-or-create Bank (code/name from parser) and Account (account_no; fill iban/currency/holder_name). Ensure the account_no is also in own_accounts (label "Imported: <bank_code>").
4. my_accounts = set of all own_accounts.account_no. items = [enrich(t, my_accounts) for t in st.transactions]; link_fees(items).
5. Create ImportBatch. For each item: h = txn_hash; skip if a Transaction with that dedupe_hash exists (count duplicate). Get-or-create Counterparty by key (kind, display_name from enrichment; new ones reviewed=False, except kind bank_fee/own_account -> reviewed=True). Insert Transaction (raw = RawTxn.raw). Keep a map index -> Transaction for new rows; after flush set parent_txn_id from parent_index when the parent was inserted in this import.
6. Call core.classify.classify_transactions(session, [new txn ids]) if module core.classify exists (import inside try/except ImportError so this step works before step 8).
7. Fill batch counts, commit, return result. On any exception: rollback and re-raise.

conftest db_session fixture: skip test if TEST_DATABASE_URL not set; init_db(TEST_DATABASE_URL) after drop_all; run core.seed logic against the test DB (refactor core/seed.py only if needed so seed(session) can be called — if you change seed.py output it in full); yield session; close.

tests/test_importer.py:
- first import: new_rows 20, duplicate_rows 0, warnings [].
- same bytes again: already_imported True.
- a second, different file containing the same rows (rebuild fixture then change cell A1's neighbour B1 to "2026-10-15" so sha differs): new_rows 0, duplicate_rows 20.
- counterparty with key "M:SEZIS BUF" exists with reviewed False; "FEE:EASYINFO" reviewed True.
- the PORD fee transaction has parent_txn_id = id of SocialPay transaction.

CHECK: pytest -q
```

---

## STEP 8 — Classification engine

```text
[paste CONTEXT block]

Files the agent needs: core/models.py, core/db.py, core/importer.py, tests/conftest.py.

TASK: Create core/classify.py and tests/test_classify.py.

Priority (first wins): category_source 'manual' is NEVER changed -> counterparty.default_category_id ('counterparty') -> first matching active rule by (priority asc, id asc) ('rule') -> none (category_id NULL, source 'none').

Functions:
- rule_matches(rule: Rule, txn: Transaction) -> bool. field value: description -> txn.description; merchant -> txn.merchant; counterparty_account -> txn.counterparty_account; txn_type -> txn.txn_type. None value -> False. Comparison case-insensitive for contains/startswith/equals; regex uses re.search with re.IGNORECASE (invalid regex -> False).
- classify_one(txn, rules: list[Rule]) -> tuple[int | None, str]  (pure, uses txn.counterparty relationship).
- classify_transactions(session, txn_ids: list[int] | None = None) -> int: (None = all) loads active rules once, updates non-manual txns, returns number changed. Does not commit.
- set_manual_category(session, txn_id: int, category_id: int | None) -> None: sets category, source 'manual' (or 'none' if category_id None, which then lets auto-classification run again). Commits.
- set_counterparty_category(session, counterparty_id: int, category_id: int | None, apply_existing: bool = True) -> int: sets default_category_id, reviewed=True; if apply_existing re-classifies that counterparty's non-manual txns; commits; returns count changed.
- reapply_all(session) -> int: classify_transactions(None) + commit.

tests/test_classify.py (db_session; FIRST insert OwnAccount(account_no="5000000001") and commit, THEN import the fixture):
- after import, all 4 fee txns have category "Bank fees" source 'rule'; own_transfer txn -> "Own account transfer"; CU-281 KH -> "Groceries" (rule).
- set_counterparty_category(M:SEZIS BUF -> "Dining & cafés") -> its txn source 'counterparty'.
- set_manual_category on the KFC txn to "Entertainment", then reapply_all -> still "Entertainment"/'manual'.
- counterparty default beats rule: set M:CU-281 KH default "Shopping" -> its txn becomes "Shopping".

CHECK: pytest -q
```

---

## STEP 9 — Shared queries + app shell + Upload page

```text
[paste CONTEXT block]

Files the agent needs: core/db.py, core/models.py, core/importer.py.

TASK: Create core/queries.py, app.py, pages/1_Upload.py.

core/queries.py (all return pandas DataFrames or simple lists; no Streamlit imports):
- categories_df(session) -> id, name, kind, sort_order (ordered by kind then sort_order).
- category_options(session) -> list[str] of "name" ordered same; plus helper category_id_by_name(session) -> dict[str,int].
- unreviewed_counterparties_df(session, include_reviewed: bool=False) -> id, display_name, kind, key, txn_count, total_out, total_in, sample_description, current_category (most common category name among its txns or None), default_category (name or None). Exclude kinds bank_fee and own_account. Order by total_out+total_in desc.
- transactions_df(session, date_from, date_to, account_ids=None, category_ids=None, include_uncategorized=True, txn_types=None, search: str | None=None, hide_fees=False) -> id, txn_at, posted_at, account_no, direction, amount, signed_amount (+in/-out), description, merchant, counterparty, txn_type, category, category_kind, category_source, note. Order txn_at desc.
- batches_df(session) -> id, imported_at, file_name, account_no, period_from, period_to, total_rows, new_rows, duplicate_rows.

app.py: st.set_page_config(page_title="My Finance", layout="wide"); @st.cache_resource engine/session factory helper get_db() returning a new Session per call; on first run call init_db(). Home shows: number of transactions, date range covered, count of unreviewed counterparties with st.page_link to pages/2_Review.py, last import time.

pages/1_Upload.py:
- st.file_uploader("Bank statement (.xlsx)", type=["xlsx"], accept_multiple_files=True).
- Button "Import". For each file: import_file(session, file.getvalue(), file.name). Show per file: bank, account, rows, new, duplicates; already_imported -> st.info; warnings -> st.warning list; exceptions -> st.error with message (do not crash the page).
- After import, if any new_counterparty_ids: st.success("N new merchants/people to classify") + st.page_link to Review.
- Below: import history table (batches_df).

CHECK: start.bat → upload tests/fixtures/golomt_sample.xlsx (after running python -m core.seed). Upload again → "already imported".
```

---

## STEP 10 — Review page (classify new merchants)

```text
[paste CONTEXT block]

Files the agent needs: core/queries.py, core/classify.py, app.py.

TASK: Create pages/2_Review.py.
- Title "Classify merchants & people". Toggle "Show already reviewed".
- Load unreviewed_counterparties_df. If empty: st.success("Nothing to review").
- st.data_editor with columns: display_name (read-only), kind (read-only), txn_count, total_out, total_in (read-only, formatted with thousands separator, 0 decimals), sample_description (read-only), suggestion = current_category (read-only), category (st.column_config.SelectboxColumn, options = category_options + "", default = default_category or current_category), apply_to_existing (CheckboxColumn, default True). Hide id/key. Use key="review_editor".
- Button "Save classifications": for each row where category is non-empty -> set_counterparty_category(id, category_id, apply_existing). Rows left empty stay unreviewed. Show count saved, then st.rerun().
- Button "Accept all suggestions": for rows with suggestion and empty category -> save suggestion.
- Under the table, an expander per counterparty is NOT needed; instead a selectbox "Show transactions for…" listing display_names, showing that counterparty's transactions (txn_at, amount, direction, description).

CHECK: open Review page → classify SEZIS BUF as "Dining & cafés" → it disappears; Transactions in DB show source 'counterparty'.
```

---

## STEP 11 — Transactions page (manual classification)

```text
[paste CONTEXT block]

Files the agent needs: core/queries.py, core/classify.py, app.py.

TASK: Create pages/3_Transactions.py.
- Sidebar filters: date range (default: first day of current month minus 2 months → today), accounts multiselect, categories multiselect (+ "Uncategorized" option), txn_type multiselect, text search, checkbox "Hide fees" (default on).
- Metrics row: total in, total out, net, count, uncategorized count.
- st.data_editor of transactions_df: editable columns ONLY category (SelectboxColumn with category_options + "") and note (TextColumn). Everything else disabled. amount formatted with thousands separator. category_source shown as small text column.
- Button "Save changes": compare edited vs original; for changed category -> set_manual_category (empty string -> None); changed note -> update note and commit. Show "Saved N changes", st.rerun().
- Button "Re-apply rules to all (keeps manual)" -> reapply_all, show count.
- Download button: filtered data as CSV (utf-8-sig so Cyrillic opens in Excel).

CHECK: change the KFC transaction to "Entertainment" → reload → still there with source manual.
```

---

## STEP 12 — Dashboard

```text
[paste CONTEXT block]

Files the agent needs: core/queries.py, app.py.

TASK: Create pages/4_Dashboard.py using Plotly Express. Use transactions_df (hide_fees=False) for a sidebar date range (default last 6 full months + current month).
Definitions: income = txns whose category_kind == 'income'; expense = category_kind == 'expense'; savings = 'savings'; transfers ('transfer') are excluded everywhere; uncategorized (category None) shown separately as "Uncategorized in/out".
1. KPI row: Income, Expenses, Savings, Net (income - expenses - savings), Bank fees total, Uncategorized out.
2. Grouped bar by month (txn_at month): income vs expenses vs savings.
3. Horizontal bar: expenses by category (desc) for the range.
4. Top 10 merchants/people by spend (out, excluding fees and transfers).
5. Line: month-by-month spend for a category chosen in a selectbox.
6. Table: largest 10 expense transactions.
Number format: thousands separator, no decimals, "₮" suffix. Charts use_container_width=True. Handle empty data gracefully (st.info).

CHECK: open Dashboard with the fixture data; numbers match Transactions page totals.
```

---

## STEP 13 — Settings page

```text
[paste CONTEXT block]

Files the agent needs: core/models.py, core/queries.py, core/classify.py, app.py.

TASK: Create pages/5_Settings.py with st.tabs: "Categories", "Rules", "My accounts", "Imports".
1. Categories: data_editor (name, kind Selectbox[income,expense,savings,transfer], sort_order), num_rows="dynamic". Save: insert/update; deleting a category is allowed only if no transactions, rules or counterparties use it (else st.error naming the usage count).
2. Rules: data_editor (priority, field Selectbox, match_type Selectbox, pattern, category Selectbox, active), num_rows="dynamic". Validate regex patterns with re.compile before saving (show error rows). Below: "Test a rule" — text inputs field/match_type/pattern → shows how many existing transactions would match and 10 samples. Button "Save & re-apply rules" → save then reapply_all.
3. My accounts: data_editor of own_accounts (account_no, label), dynamic rows. Help text: "Add all your own account numbers at any bank. Money moving between them is not counted as income/expense." Save then button "Re-detect own transfers": for txns whose normalized counterparty_account is in own_accounts and txn_type in (p2p_in,p2p_out,other) set txn_type 'own_transfer', move their counterparty kind to own_account/reviewed True, then reapply_all.
4. Imports: batches_df; select a batch + button "Delete this import" with a confirmation checkbox → deletes batch (cascade deletes its transactions).

CHECK: add 5000000001 as own account → re-detect → that incoming txn shows "Own account transfer".
```

---

## STEP 14 — Backup + final polish

```text
[paste CONTEXT block]

Files the agent needs: core/config.py, README.md.

TASK:
1. scripts/backup.py: run pg_dump (path from config.PG_DUMP_PATH, fallback "pg_dump" on PATH) using DATABASE_URL parsed with sqlalchemy.engine.make_url; pass password via PGPASSWORD env; custom format (-Fc) to backups/finance_YYYYMMDD_HHMM.dump; keep newest 10, delete older; print path. Exit code 1 with clear message on failure.
2. backup.bat: activate venv, python scripts\backup.py, pause.
3. Update README.md (full file): setup, daily use (upload → review → check dashboard), backup & restore (pg_restore -d finance --clean file.dump), how to add own accounts, how to add a new bank (point to "core/parsers": create parser class, add to PARSERS, add fixture + test).
4. In pages/5_Settings.py add nothing. In app.py add nothing.

CHECK: backup.bat → a .dump file appears in backups\.
```

---

## STEP 15 — Adding another bank (use later, once per bank)

Before using this: open your new bank's statement, copy the header rows + 6–8 transaction rows into a note, and **replace names, account numbers and card digits with fake ones** (keep the structure and the description formats exactly).

```text
[paste CONTEXT block]

Files the agent needs: core/parsers/base.py, core/parsers/golomt.py, core/parsers/registry.py, core/enrich.py.

TASK: Add a parser for <BANK NAME> (code "<CODE>").
Sample statement (anonymized; sheet name "<SHEET NAME>"; row numbers as in the file):
<PASTE ROWS HERE, one row per line, cells separated by " | ">

Create:
1. core/parsers/<code_lowercase>.py — <Name>Parser(BankParser) following the same contract and validation as golomt.py. detect() must not match Golomt files and Golomt's detect must not match this one.
2. Add it to PARSERS in registry.py (output full file).
3. tests/fixtures/make_<code>_fixture.py producing the sample above, and tests/test_<code>_parser.py checking: detect, account_no, row count, sum in, sum out, no warnings.
4. If the description formats differ from Golomt, extend core/enrich.py with bank-specific rules (enrich receives the bank code as a new optional parameter bank_code: str = "GOLOMT"; existing Golomt behaviour and tests must not change). Output enrich.py in full only if changed.
Also add the bank to core/seed.py banks list.

CHECK: pytest -q  (all old tests still pass)
```

---

## FIX prompt (use whenever a CHECK fails)

```text
[paste CONTEXT block]

The check for STEP <N> failed.
Command: <command>
Error output (last 40 lines):
<paste>
Relevant files:
<paste the files named in the error / traceback>

Find the root cause and fix it with the smallest change. Output only the changed files in full. Do not change tests unless the test itself contradicts the step specification; if so, say which line and why in one sentence.
```

## Small-change prompt (for later tweaks)

```text
[paste CONTEXT block]

Files: <paste the 1–3 files involved>
Change: <one specific change, e.g. "On the Dashboard add a pie chart of expenses by category under chart 3">
Output only the changed files in full.
```
