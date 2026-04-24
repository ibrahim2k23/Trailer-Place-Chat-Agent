"""
Drop and recreate public.conversation_history. Destroys all stored chat history for that table.

Run from the project folder (next to app.py) after setting confirmation in the environment:
    $env:TRAILERPLACE_CONFIRM_RECREATE=1; uv run python recreate_conversation_table.py

On Unix:
    TRAILERPLACE_CONFIRM_RECREATE=1 uv run python recreate_conversation_table.py

Requires the same .env as the app (DATABASE_URL or Supabase host credentials).
"""
from __future__ import annotations

import os
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
    if (os.getenv("TRAILERPLACE_CONFIRM_RECREATE") or "").strip().lower() not in (
        "1",
        "yes",
        "true",
    ):
        print(
            "Refusing to drop the table. This deletes all rows in public.conversation_history.\n"
            "To continue, set environment variable TRAILERPLACE_CONFIRM_RECREATE=1 and run again.",
            file=sys.stderr,
        )
        return 1

    eng = get_engine()
    if not eng:
        print(
            "No database connection configured. Set in .env one of:\n"
            "  DATABASE_URL (or TRAILERPLACE_DATABASE_URL / SUPABASE_DB_URL), or\n"
            "  SUPABASE_DB_HOST, SUPABASE_DB_USER, SUPABASE_DB_PASSWORD",
            file=sys.stderr,
        )
        return 1

    sql_path = _ROOT / "supabase_conversation_history.sql"
    if not sql_path.is_file():
        print(f"Missing {sql_path}", file=sys.stderr)
        return 1

    statements = _statements_from_sql_file(sql_path)
    if not statements:
        print(f"No SQL statements found in {sql_path}", file=sys.stderr)
        return 1

    with eng.begin() as conn:
        conn.execute(
            text("DROP TABLE IF EXISTS public.conversation_history CASCADE")
        )
        print("Dropped public.conversation_history (if it existed).")
        for i, stmt in enumerate(statements, 1):
            conn.execute(text(stmt))
            print(f"OK ({i}/{len(statements)})")

    print("Done: public.conversation_history was recreated and is empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
