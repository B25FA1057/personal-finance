from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from core.config import DATABASE_URL, PG_DUMP_PATH


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    backup_dir = root / "backups"
    backup_dir.mkdir(exist_ok=True)

    parsed = make_url(DATABASE_URL)
    database = parsed.database or "finance"
    host = parsed.host or "localhost"
    port = parsed.port or 5432
    user = parsed.username or "postgres"
    password = parsed.password

    dump_path = PG_DUMP_PATH or "pg_dump"
    os.environ["PGPASSWORD"] = password or ""

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    dump_file = backup_dir / f"finance_{ts}.dump"
    cmd = [dump_path, "-Fc", "-h", host, "-p", str(port), "-U", user, "-d", database, "-f", str(dump_file)]
    try:
        subprocess.run(cmd, check=True)
    except Exception as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1

    dumps = sorted(backup_dir.glob("finance_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dumps[10:]:
        old.unlink(missing_ok=True)

    print(dump_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
