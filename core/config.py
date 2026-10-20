from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL: str = os.getenv("DATABASE_URL") or ""
TEST_DATABASE_URL: str | None = os.getenv("TEST_DATABASE_URL")
PG_DUMP_PATH: str | None = os.getenv("PG_DUMP_PATH")

QPAY_FEE_ACCOUNTS: frozenset[str] = frozenset({"2015113727"})
FEE_LINK_WINDOW_SECONDS = 15
CARD_REF_TOLERANCE_SECONDS = 5

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing. Copy .env.example to .env and set it.")
