"""
Rewrite the `category` column in listings_final_v5.xlsx for known two-word values
by keeping only the first word (matches how normalize_category / Pinecone expect single tokens).

By default the original file is updated in place. A copy of the previous file is saved as
listings_final_v5.xlsx.bak (next to the workbook).

Usage (from project folder next to the .xlsx):
    uv run python strip_category_second_word.py
    uv run python strip_category_second_word.py -o copy_only.xlsx
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

# Lowercase key: full two-word label as it may appear in the sheet (case-insensitive).
# Value: replacement string for the category cell.
TWO_WORD_TO_CANONICAL: dict[str, str] = {
    "enclosed cargo": "Enclosed",
    "fiber optic": "Fiber",
    "equipment trailer": "Equipment",
    "livestock trailer": "Livestock",
    "flatbed trailer": "Flatbed",
    "tilt trailer": "Tilt",
    "welding trailer": "Welding",
}


def _norm_key(s: str) -> str:
    return " ".join(s.split()).lower()


def transform_category(val) -> tuple[str, bool]:
    """Return (new_value, changed)."""
    if pd.isna(val):
        return (val, False)  # type: ignore[return-value]
    s = str(val).strip()
    if not s:
        return s, False
    key = _norm_key(s)
    if key in TWO_WORD_TO_CANONICAL:
        new_s = TWO_WORD_TO_CANONICAL[key]
        return new_s, new_s != s
    return s, False


def main() -> int:
    root = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description="Remove second word for known category two-word forms in an Excel file."
    )
    ap.add_argument(
        "xlsx",
        nargs="?",
        default=str(root / "listings_final_v5.xlsx"),
        type=Path,
        help="Path to Excel workbook (default: ./listings_final_v5.xlsx next to this script).",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="If set, write only to this file and leave the input file unchanged.",
    )
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="When updating the original file, do not create a .bak copy first (unsafe).",
    )
    args = ap.parse_args()

    path: Path = args.xlsx
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    df = pd.read_excel(path, engine="openpyxl")
    if "category" not in df.columns:
        print("Column 'category' not found. Columns: " + ", ".join(map(str, df.columns)), file=sys.stderr)
        return 1

    changed = 0
    new_values = []
    for v in df["category"]:
        nv, ch = transform_category(v)
        new_values.append(nv)
        if ch:
            changed += 1
    df["category"] = new_values

    if args.output is not None:
        out = args.output
    else:
        out = path
        if not args.no_backup:
            bak = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, bak)
            print(f"Backup: {bak}")
        else:
            print("No backup created (--no-backup).", file=sys.stderr)

    df.to_excel(out, index=False, engine="openpyxl")
    print(f"Wrote: {out}")
    print(f"Category cells updated: {changed} / {len(df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
