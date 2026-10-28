# Personal Finance

A local personal finance app for importing bank statements, classifying transactions, and reviewing merchant categories.

## Setup

1. Create a virtual environment:
   `python -m venv .venv`
2. Activate it and install dependencies:
   `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set your PostgreSQL values. Replace the placeholders; do not commit `.env`.
4. Create the databases in PostgreSQL:
   `CREATE DATABASE finance;`
   `CREATE DATABASE finance_test;`
5. Initialize the schema:
   `python -m core.seed`
6. Start the app:
   `start.bat`
   Open `http://127.0.0.1:8502`.

## Tests

From PowerShell, run the SQLite-backed test suite without loading `.env`:

```powershell
$env:PYTHON_DOTENV_DISABLED = "1"
$env:DATABASE_URL = "sqlite://"
.\.venv\Scripts\python.exe -m pytest -q
```

## Daily use

Use the sidebar to navigate Overview, Reports, Transactions, Import Statements, Review Categories, Budgets, Savings Goals, and Settings. Import previews validate the supported workbook and show existing duplicate counts before insertion.

If the Categories table is empty, open Settings → Categories and choose “Initialize missing default categories.” This adds only missing built-in categories; it does not change transactions, existing category definitions, rules, budgets, or savings goals.

After an import, existing counterparty mappings and deterministic rules run first. With explicit local AI consent, remaining eligible transactions are categorized in a background worker; imports do not wait for Ollama. AI output stays pending until you accept or reject it in Review. Ollama requests use small batches, one bounded retry for transient transport/server failures, one corrective request for invalid model output, a timeout, and cancellation between batches. Failed batches are reported while independent batches continue. If the app process stops, any remaining uncategorized transactions can be retried from Review.

When editing a transaction category, enable “Create/update merchant rule” to save an exact-match normalized merchant rule for future imports. It does not silently reclassify previously approved transactions.

The Dashboard includes date presets, cash-flow summaries, categorized expense trends, budget performance, recent transactions, and separately identified uncategorized outflows. Reports provides monthly totals and CSV export. Transactions supports filtering, page sizes, editing, and CSV export.

Golomt Bank's `OperativeAccountStatementRaw` workbook format is currently supported. Unsupported workbook layouts are rejected, and statement or row balance discrepancies are shown as import warnings. Re-uploading the same file is recognized; matching transactions in a different statement are skipped as duplicates.

`QPAY_FEE_ACCOUNTS` is an optional comma-separated setting for account identifiers you have verified as QPay fee accounts. It is blank by default; no account identifiers are built into the app.

Use the transaction filters to narrow by date, account, category, or transaction type, then export the filtered list as a CSV for spreadsheets. The Dashboard summarizes categorized income, expenses, savings, fees, and uncategorized inflows/outflows. Transactions identified as transfers by type or category are shown separately and excluded from ordinary cash-flow totals.

The Transactions page supports description search, merchant/payee filtering, date/category/type filters, and editing descriptions, categories, and transaction types. Results are paginated. Saving a manual category marks it as manual so rule re-application and duplicate statement imports do not overwrite it. The Dashboard uses database-side monthly and category aggregations; own-account transfers are excluded from cash-flow totals.

The Budgets page manages monthly expense-category budgets and reports actual outflows by transaction month, excluding own-account transfers. It displays remaining amounts and warns at 80% and when a budget is exceeded. Savings Goals are tracked separately using manually entered saved amounts; bank transfers are not automatically counted as contributions. Budget and goal currency fields are labels only: the current application does not convert currencies or filter transaction spending by account currency. Financial summaries across multiple account currencies should therefore be treated as unconverted amounts. The planning tables are created by the existing non-destructive SQLAlchemy `create_all` initialization; no existing tables or financial records are removed.

## Optional AI categorization

AI categorization uses a local Ollama service only. Install Ollama and pull a local model (for example, `ollama pull llama3.2`), then optionally set `OLLAMA_BASE_URL` (default: `http://127.0.0.1:11434`), `OLLAMA_MODEL` (default: `llama3.2`), and `OLLAMA_TIMEOUT_SECONDS` (default: `180`) in `.env`. Transactions are processed in batches of at most 10. The timeout applies to the actual non-streaming `/api/chat` inference request. A single retry is used for timeouts, transient connection failures, HTTP 429, and server-side HTTP errors; other HTTP errors are reported without retrying. Invalid or incomplete model output gets at most one corrective request per batch, and failed batches do not prevent later independent batches from running. The application rejects non-loopback Ollama URLs and bypasses configured HTTP proxies, so transaction data is not sent to a cloud AI provider.

The Upload and Review pages require explicit consent before requesting suggestions. Requests contain a temporary transaction index, direction, transaction type, normalized merchant when available, and a redacted description. They do not contain account numbers, counterparty account IDs, amounts, or dates. Person-to-person names and account-reference strings are omitted or redacted. If Ollama is unavailable, the model is missing, a request times out, or a response is invalid, imports remain saved and transactions remain available in Review.

AI responses are validated against categories already in the database and are saved as pending suggestions. Review each suggestion to accept it, choose another compatible category, or reject it. Accepting a suggestion can optionally create or update an exact-match merchant or description rule. Confirmed categories are not overwritten by later imports or AI runs. Verified own-account transfers are excluded from AI categorization; incoming merchant refunds assigned to an expense category reduce net expenses rather than inflating income. AI/network/format errors leave the transactions available for review.

The application creates two additional tables for suggestion state and import summaries through its existing `create_all` initialization. This adds tables only; it does not rewrite existing transaction rows or re-import statements.

For safe verification, use synthetic statements containing fictitious merchants, dates, amounts, and account identifiers. Run the offline tests first; they mock the local Ollama request and do not call the service. A live test requires Ollama to be running with the selected model available and explicit consent in the app.

## Backup and restore

Create a backup with:
`backup.bat`

Restore with:
`pg_restore -d finance --clean path\to\backup.dump`

## Add your own accounts

Add every account number you own under Settings -> My accounts. Transactions between your own accounts are treated as transfers.

## Add a new bank

Create a parser under `core/parsers/`, register it in `core/parsers/registry.py`, and add a fixture plus test to validate parsing.
