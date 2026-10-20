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

## Daily use

1. Upload a bank statement `.xlsx` file.
2. Review newly detected merchants and people.
3. Check the dashboard for totals and trends.
4. Adjust categories and notes in the transactions page.

Golomt Bank's `OperativeAccountStatementRaw` workbook format is currently supported. Unsupported workbook layouts are rejected, and statement or row balance discrepancies are shown as import warnings. Re-uploading the same file is recognized; matching transactions in a different statement are skipped as duplicates.

Use the transaction filters to narrow by date, account, category, or transaction type, then export the filtered list as a CSV for spreadsheets. The Dashboard summarizes categorized income, expenses, savings, fees, and uncategorized spending; transfers remain separate from income and expenses.

## Backup and restore

Create a backup with:
`backup.bat`

Restore with:
`pg_restore -d finance --clean path\to\backup.dump`

## Add your own accounts

Add every account number you own under Settings -> My accounts. Transactions between your own accounts are treated as transfers.

## Add a new bank

Create a parser under `core/parsers/`, register it in `core/parsers/registry.py`, and add a fixture plus test to validate parsing.
