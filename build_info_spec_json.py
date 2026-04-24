"""
Build `info_spec_json` column: JSON with `info` (non-empty row fields minus excluded
columns) and `specifications` (parsed from `specifications_default`).

Run from project root (CSV or XLSX):
    uv run python build_info_spec_json.py
    uv run python build_info_spec_json.py listings_final_v5.xlsx
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

EXCLUDED_FROM_INFO = frozenset(
    {
        "specifications_default",
        "featured",
        "api_description_toggle",
        "dealer_notes",
        "msrp",
        "info_spec_json",
    }
)


def is_empty(val) -> bool:
    if val is None:
        return True
    try:
        if pd.isna(val):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(val, str):
        s = val.strip()
        if not s or s.lower() == "nan":
            return True
        return False
    if isinstance(val, (float, int)) and isinstance(val, float) and math.isnan(val):
        return True
    return False


def serialize_value(val):
    if is_empty(val):
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int,)) and not isinstance(val, bool):
        return int(val)
    if isinstance(val, float):
        if math.isnan(val):
            return None
        if val.is_integer():
            return int(val)
        return val
    if hasattr(val, "item"):
        try:
            return serialize_value(val.item())
        except (ValueError, AttributeError):
            pass
    return str(val).strip()


def parse_specifications_default(raw) -> dict:
    if is_empty(raw):
        return {}
    s = str(raw).strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def row_to_payload(row: pd.Series) -> str:
    info: dict[str, object] = {}
    for col in row.index:
        if col in EXCLUDED_FROM_INFO:
            continue
        v = serialize_value(row[col])
        if v is None:
            continue
        info[col] = v

    specs_raw = row["specifications_default"] if "specifications_default" in row.index else None
    specifications = parse_specifications_default(specs_raw)

    return json.dumps(
        {"info": info, "specifications": specifications},
        ensure_ascii=False,
    )


def _load_table(path: Path) -> pd.DataFrame:
    suf = path.suffix.lower()
    if suf in (".xlsx", ".xls"):
        return pd.read_excel(path, engine="openpyxl")
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False)


def _save_table(df: pd.DataFrame, path: Path) -> None:
    suf = path.suffix.lower()
    if suf in (".xlsx", ".xls"):
        df.to_excel(path, index=False, engine="openpyxl")
    else:
        df.to_csv(path, index=False, encoding="utf-8-sig")


def main() -> None:
    default_in = Path(__file__).resolve().parent / "scrapped_inventory.csv"
    p = argparse.ArgumentParser(
        description="Add info_spec_json column to a CSV or XLSX table",
    )
    p.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help=f"Source CSV or XLSX (default: {default_in.name})",
    )
    p.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Same as INPUT (for older scripts that used --csv)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: overwrite input; same format as input)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print sample + parse stats only; do not write",
    )
    p.add_argument(
        "--sample-rows",
        type=int,
        default=3,
        help="With --dry-run, number of sample JSON lines to print",
    )
    args = p.parse_args()

    src = args.csv or args.input or default_in
    if not src.is_file():
        raise SystemExit(f"File not found: {src}")

    df = _load_table(src)
    # CSV path: empty strings preserved. XLSX: NaN/empty handled in serialize_value / is_empty

    parse_failures = 0
    if "specifications_default" in df.columns:
        for raw in df["specifications_default"]:
            if is_empty(raw):
                continue
            s = str(raw).strip()
            try:
                data = json.loads(s)
                if not isinstance(data, dict):
                    parse_failures += 1
            except json.JSONDecodeError:
                parse_failures += 1

    df["info_spec_json"] = df.apply(row_to_payload, axis=1)

    if args.dry_run:
        print(f"Rows: {len(df)}")
        print(f"Non-empty specifications_default cells that failed dict parse: {parse_failures}")
        for i in range(min(args.sample_rows, len(df))):
            print(df["info_spec_json"].iloc[i][:500] + ("..." if len(df["info_spec_json"].iloc[i]) > 500 else ""))
        return

    out = args.output if args.output is not None else src
    _save_table(df, out)
    print(f"Wrote {out} with info_spec_json ({len(df)} rows).")


if __name__ == "__main__":
    main()
