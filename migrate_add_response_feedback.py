"""
Add `response_feedback` to `public.conversation_history` (idempotent).

Run from the project folder (next to `app.py`):
    uv run python migrate_add_response_feedback.py

Requires the same .env as the app: DATABASE_URL, or SUPABASE_DB_HOST + credentials.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")

from src.conversation_store import get_engine  # noqa: E402


def _statements_from_sql_file(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    parts: list[str] = []
    for block in raw.split(";"):
        lines: list[str] = []
        for line in block.splitlines():
            stripped = line.split("--", 1)[0].rstrip()
            if stripped.strip():
                lines.append(stripped)
        s = "\n".join(lines).strip()
        if s:
            parts.append(s)
    return parts


def main() -> int:
    eng = get_engine()
    if not eng:
        print(
            "No database connection configured. Set in .env one of:\n"
            "  DATABASE_URL (or TRAILERPLACE_DATABASE_URL / SUPABASE_DB_URL), or\n"
            "  SUPABASE_DB_HOST, SUPABASE_DB_USER, SUPABASE_DB_PASSWORD",
            file=sys.stderr,
        )
        return 1

    sql_path = _ROOT / "supabase_migration_add_response_feedback.sql"
    if not sql_path.is_file():
        print(f"Missing {sql_path}", file=sys.stderr)
        return 1

    statements = _statements_from_sql_file(sql_path)
    if not statements:
        print(f"No SQL statements found in {sql_path}", file=sys.stderr)
        return 1

    with eng.begin() as conn:
        for i, stmt in enumerate(statements, 1):
            conn.execute(text(stmt))
            print(f"OK ({i}/{len(statements)})")

    print("Done: response_feedback column is present (or was already).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
