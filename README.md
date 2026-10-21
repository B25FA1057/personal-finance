# Personal Finance

A local personal finance app for importing bank statements, classifying transactions, and reviewing merchant categories.

## Setup

1. Create a virtual environment:
   `python -m venv .venv`
2. Activate it and install dependencies:
   `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set your PostgreSQL values.
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

## Backup and restore

Create a backup with:
`backup.bat`

Restore with:
`pg_restore -d finance --clean path\to\backup.dump`

## Add your own accounts

Add every account number you own under Settings -> My accounts. Transactions between your own accounts are treated as transfers.

## Add a new bank

Create a parser under `core/parsers/`, register it in `core/parsers/registry.py`, and add a fixture plus test to validate parsing.
