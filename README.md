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

## Tests

From PowerShell, run the SQLite-backed test suite without loading `.env`:

```powershell
$env:PYTHON_DOTENV_DISABLED = "1"
$env:DATABASE_URL = "sqlite://"
.\.venv\Scripts\python.exe -m pytest -q
```

## Daily use

1. Upload a bank statement `.xlsx` file.
2. Review newly detected merchants and people.
3. Check the dashboard for totals and trends.
4. Adjust categories and notes in the transactions page.

Golomt Bank's `OperativeAccountStatementRaw` workbook format is currently supported. Unsupported workbook layouts are rejected, and statement or row balance discrepancies are shown as import warnings. Re-uploading the same file is recognized; matching transactions in a different statement are skipped as duplicates.

`QPAY_FEE_ACCOUNTS` is an optional comma-separated setting for account identifiers you have verified as QPay fee accounts. It is blank by default; no account identifiers are built into the app.

Use the transaction filters to narrow by date, account, category, or transaction type, then export the filtered list as a CSV for spreadsheets. The Dashboard summarizes categorized income, expenses, savings, fees, and uncategorized inflows/outflows. Transactions identified as transfers by type or category are shown separately and excluded from ordinary cash-flow totals.

The Transactions page supports description search, merchant/payee filtering, date/category/type filters, and editing descriptions, categories, and transaction types. Results are paginated. Saving a manual category marks it as manual so rule re-application and duplicate statement imports do not overwrite it. The Dashboard uses database-side monthly and category aggregations; own-account transfers are excluded from cash-flow totals.

The Budgets page manages monthly expense-category budgets and reports actual outflows by transaction month, excluding own-account transfers. It displays remaining amounts and warns at 80% and when a budget is exceeded. Savings Goals are tracked separately using manually entered saved amounts; bank transfers are not automatically counted as contributions. Budget and goal currency fields are labels only: the current application does not convert currencies or filter transaction spending by account currency. The planning tables are created by the existing non-destructive SQLAlchemy `create_all` initialization; no existing tables or financial records are removed.

## Optional AI categorization

AI categorization uses the official Google GenAI Python SDK and Gemini API. Create an API key in [Google AI Studio](https://aistudio.google.com/), then put `GEMINI_API_KEY=your-key` in your local `.env`; use a Google AI Studio key. Optionally set `GEMINI_MODEL` (default: `gemini-2.5-flash-lite`). Google's [model guide](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-lite) describes Gemini 2.5 Flash-Lite as suitable for high-volume classification, and the current [pricing page](https://ai.google.dev/gemini-api/docs/pricing) lists free standard input/output for it. Model access and free-tier quotas vary by project and can change; confirm current availability and [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) in AI Studio before use.

Before an upload or a manual categorization run sends data, the Upload or Review page requires explicit consent and explains what is shared. The request contains a temporary transaction index, direction, transaction type, normalized merchant when available, and a redacted description. It does not contain account numbers, counterparty account IDs, transaction amounts, or dates. Person-to-person names and account-reference strings are omitted or redacted. Do not enable the consent control unless you are comfortable sending these selected details to Google.

Google's free-tier terms and data handling differ from paid-tier terms. Review the current Gemini API [pricing](https://ai.google.dev/gemini-api/docs/pricing) and [terms](https://ai.google.dev/gemini-api/terms) before sending transaction data; free-tier inputs may be used to improve Google's products.

AI responses are validated against categories already in the database and are saved as pending suggestions. Review each suggestion to accept it, choose another compatible category, or reject it. Accepting a suggestion can optionally create or update an exact-match merchant or description rule. Confirmed categories are not overwritten by later imports or AI runs. Verified own-account transfers are excluded from AI categorization; incoming merchant refunds assigned to an expense category reduce net expenses rather than inflating income. AI/network/format errors leave the transactions available for review.

The application creates two additional tables for suggestion state and import summaries through its existing `create_all` initialization. This adds tables only; it does not rewrite existing transaction rows or re-import statements.

For safe verification, use synthetic statements containing fictitious merchants, dates, amounts, and account identifiers. Run the offline tests first; they mock Gemini and do not call the API. A live test requires a valid API key and explicit consent in the app.

## Backup and restore

Create a backup with:
`backup.bat`

Restore with:
`pg_restore -d finance --clean path\to\backup.dump`

## Add your own accounts

Add every account number you own under Settings -> My accounts. Transactions between your own accounts are treated as transfers.

## Add a new bank

Create a parser under `core/parsers/`, register it in `core/parsers/registry.py`, and add a fixture plus test to validate parsing.
