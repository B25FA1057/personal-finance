from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL: str = os.getenv("DATABASE_URL") or ""
TEST_DATABASE_URL: str | None = os.getenv("TEST_DATABASE_URL")
PG_DUMP_PATH: str | None = os.getenv("PG_DUMP_PATH")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL") or "llama3.2"
try:
    OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))
except ValueError as exc:
    raise RuntimeError("OLLAMA_TIMEOUT_SECONDS must be a positive whole number.") from exc
if OLLAMA_TIMEOUT_SECONDS < 1:
    raise RuntimeError("OLLAMA_TIMEOUT_SECONDS must be a positive whole number.")

QPAY_FEE_ACCOUNTS: frozenset[str] = frozenset(
    account.strip()
    for account in os.getenv("QPAY_FEE_ACCOUNTS", "").split(",")
    if account.strip()
)
FEE_LINK_WINDOW_SECONDS = 15
CARD_REF_TOLERANCE_SECONDS = 5

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing. Copy .env.example to .env and set it.")
