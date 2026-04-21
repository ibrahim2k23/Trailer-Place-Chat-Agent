"""
Run once to embed all listings and upsert them to Pinecone.

Usage:
    uv run python src/ingest.py
    uv run python src/ingest.py --force   # re-index even if vectors exist
"""
import json
import os
import re
import sys
import time
import argparse
import hashlib
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.normalizer import (
    normalize_category,
    normalize_make,
    normalize_color,
    normalize_hitch,
    normalize_condition,
    build_category_subcategory,
    build_embedding_text,
)

load_dotenv()

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "trailerplace-listings")
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = 1536
BATCH_SIZE = 50

DATA_FILE = Path(__file__).parent.parent / "listings_final_v6.xlsx"


def get_or_create_index(pc: Pinecone):
    existing = [idx.name for idx in pc.list_indexes()]
    if INDEX_NAME not in existing:
        print(f"Creating index '{INDEX_NAME}'...")
        pc.create_index(
            name=INDEX_NAME,
            dimension=EMBEDDING_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        while True:
            status = pc.describe_index(INDEX_NAME).status
            if status.get("ready", False):
                break
            print("  waiting for index to be ready...")
            time.sleep(2)
        print("  Index ready.")
    else:
        print(f"Index '{INDEX_NAME}' already exists.")
    return pc.Index(INDEX_NAME)


def parse_money(val) -> Optional[float]:
    """Parse currency from Excel/JSON: numbers, '$8,400', '8400', NaN-safe."""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        try:
            v = float(val)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None
    s = str(val).strip()
    if not s:
        return None
    s = re.sub(r"[$,\s]", "", s)
    try:
        v = float(s)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _money_from_info(info: dict, *keys: str) -> Optional[float]:
    for k in keys:
        p = parse_money(info.get(k))
        if p is not None:
            return p
    return None


def build_vector_id(row: pd.Series, row_idx: int) -> str:
    stock_number = str(row.get("stock_number", "")).strip()
    if stock_number:
        return f"stock_{stock_number}"
    hin = str(row.get("hin", "")).strip()
    if hin:
        return f"hin_{hin}"
    url = str(row.get("url", "")).strip()
    if url:
        short = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        return f"url_{short}"
    return f"row_{row_idx}"


def build_record(row: pd.Series, row_idx: int) -> dict:
    info: dict = {}
    try:
        raw_specs = row.get("info_specs_json", "") or row.get("info_spec_json", "")
        blob = json.loads(raw_specs) if raw_specs else {}
        info = blob.get("info", {})
    except Exception:
        pass

    def col(name: str, info_key: Optional[str] = None) -> str:
        val = row.get(name, "")
        if pd.isna(val) or str(val).strip() == "":
            val = info.get(info_key or name, "")
        return str(val).strip() if val else ""

    raw_category = col("category")
    raw_subcategory = col("subcategory")
    raw_make = col("make")
    raw_color = col("color")
    raw_hitch = col("hitch_type")
    raw_condition = col("condition")
    title = col("title")
    url = col("url")
    dealer_notes = col("dealer_notes")

    condition = normalize_condition(raw_condition)
    category = normalize_category(raw_category)
    cat_sub = build_category_subcategory(raw_category, raw_subcategory)
    make = normalize_make(raw_make)
    color = normalize_color(raw_color)
    hitch = normalize_hitch(raw_hitch) if raw_hitch else None

    price = parse_money(row.get("price")) or _money_from_info(
        info, "price", "Price", "our_price", "Our Price"
    )

    price_display = f"${price:,.0f}" if price is not None else "Call for price"

    year = str(info.get("year", "")).strip() or None
    length = str(info.get("length", "")).strip() or None
    width = str(info.get("width", "")).strip() or None
    axles = str(info.get("axles", "")).strip() or None
    gvwr = str(info.get("gvwr", "")).strip() or None
    payload = str(info.get("payload_capacity", "")).strip() or None
    material = str(info.get("trailer_material", "")).strip() or None
    floor = str(info.get("floor", "")).strip() or None
    payments_from = col("payments_from")

    embedding_text = build_embedding_text(info, title, dealer_notes)

    metadata: dict = {
        "title": title,
        "condition": condition,
        "category": category,
        "category_subcategory": cat_sub,
        "make": make,
        "color": color,
        "url": url,
        "price_display": price_display,
    }
    for key, val in [
        ("price", price),
        ("hitch_type", hitch),
        ("payments_from", payments_from if payments_from else None),
        ("year", year),
        ("length", length),
        ("width", width),
        ("axles", axles),
        ("gvwr", gvwr),
        ("payload_capacity", payload),
        ("trailer_material", material),
        ("floor", floor),
    ]:
        if val is not None:
            metadata[key] = val

    vector_id = build_vector_id(row, row_idx)

    return {
        "id": vector_id,
        "embedding_text": embedding_text,
        "metadata": metadata,
    }


def embed_batch(client: OpenAI, texts: list) -> list:
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def main(force: bool = False):
    print("Loading data...")
    df = pd.read_excel(DATA_FILE)
    print(f"  {len(df)} listings loaded.")

    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = get_or_create_index(pc)

    if not force:
        stats = index.describe_index_stats()
        total = stats.get("total_vector_count", 0)
        if total > 0:
            print(f"\nIndex already has {total} vectors. Use --force to re-index.")
            return

    openai_client = OpenAI(api_key=OPENAI_API_KEY)

    print("\nBuilding records...")
    records = [build_record(row, i) for i, (_, row) in enumerate(df.iterrows())]

    print(f"Embedding {len(records)} listings in batches of {BATCH_SIZE}...")
    vectors = []
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i: i + BATCH_SIZE]
        texts = [r["embedding_text"] for r in batch]
        embeddings = embed_batch(openai_client, texts)
        for rec, emb in zip(batch, embeddings):
            vectors.append({"id": rec["id"], "values": emb, "metadata": rec["metadata"]})
        print(f"  Embedded {min(i + BATCH_SIZE, len(records))}/{len(records)}")

    print(f"\nUpserting {len(vectors)} vectors to Pinecone...")
    for i in range(0, len(vectors), 100):
        batch = vectors[i: i + 100]
        index.upsert(vectors=batch)
        print(f"  Upserted {min(i + 100, len(vectors))}/{len(vectors)}")

    print("\nDone! Index stats:")
    time.sleep(3)
    print(index.describe_index_stats())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-index even if vectors exist")
    args = parser.parse_args()
    main(force=args.force)
