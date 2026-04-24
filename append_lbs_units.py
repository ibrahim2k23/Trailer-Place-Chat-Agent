"""
For listings_final_v5.xlsx, ensure weight columns have an 'lbs' unit suffix
where missing. Skips empty cells. Overwrites that same file by default
(close it in Excel first). Use --dry-run to preview without saving.

Columns: dry_weight, gvwr, axle_capacity, stock_capacity, payload_capacity
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

WEIGHT_COLUMNS = (
    "dry_weight",
    "gvwr",
    "axle_capacity",
    "stock_capacity",
    "payload_capacity",
)
DEFAULT_XLSX = "listings_final_v5.xlsx"


def _is_empty(val) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    s = str(val).strip()
    return s == ""


def ensure_lbs_suffix(val):
    """
    - Numbers (from Excel) -> '<n> lbs'
    - If already ends with 'lbs' (case-insensitive) -> unchanged
    - If ends with 'lb' (singular) -> append 's' -> ...lbs
    - Else -> append ' lbs'
    """
    if _is_empty(val):
        return val
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if isinstance(val, float) and val != val:  # NaN
            return val
        n = int(val) if val == int(val) else val
        return f"{n} lbs"

    s = str(val).strip()
    if not s:
        return val

    low = s.lower()
    if low.endswith("lbs"):
        return s
    if low.endswith("lb"):
        return s + "s"
    return s + " lbs"


def main() -> int:
    root = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "xlsx",
        nargs="?",
        default=str(root / DEFAULT_XLSX),
        help=f"path to xlsx (default: {DEFAULT_XLSX} next to this script)",
    )
    ap.add_argument(
        "-o",
        "--output",
        help="output path (default: overwrite input)", default=None,
    )
    ap.add_argument("--dry-run", action="store_true", help="no write; show change counts only")
    args = ap.parse_args()
    path = Path(args.xlsx)
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    df = pd.read_excel(path, engine="openpyxl")
    missing = [c for c in WEIGHT_COLUMNS if c not in df.columns]
    if missing:
        print(f"Missing columns: {missing}", file=sys.stderr)
        return 1

    changes = 0
    examples: list[tuple] = []
    for col in WEIGHT_COLUMNS:
        before = df[col]
        new_col = before.apply(
            lambda x: x if _is_empty(x) else ensure_lbs_suffix(x)
        )
        df[col] = new_col.astype("object")
        chg = ~(
            (before == new_col)
            | (pd.isna(before) & pd.isna(new_col))
        )
        c = int(chg.sum())
        changes += c
        if args.dry_run and c:
            for idx in chg[chg].index:
                if len(examples) < 20:
                    examples.append((col, idx, before.loc[idx], new_col.loc[idx]))

    for col, idx, a, b in examples:
        print(f"  {col} row {idx}: {a!r} -> {b!r}")
    print(f"Total cells changed: {changes} (in {', '.join(WEIGHT_COLUMNS)})")
    if args.dry_run:
        print("Dry run: file not written.")
        return 0

    out = Path(args.output) if args.output else path
    try:
        df.to_excel(out, index=False, engine="openpyxl")
    except PermissionError:
        print(
            f"Permission denied writing {out}. Close the workbook in Excel (or any "
            "program using it), then run this script again.",
            file=sys.stderr,
        )
        return 1
    print(f"Updated: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
