"""
Trailer Recommendation System — Pinecone Ingestion Script
==========================================================
Usage:
    pip install -r requirements.txt
    export PINECONE_API_KEY="your-key"
    export OPENAI_API_KEY="your-key"   # or use a local embedder
    python trailer_pinecone_ingest.py --file Trailer_Sample.xlsx

The script:
1. Reads the Excel file
2. Cleans / normalises every row
3. Builds a rich text blob for embedding (title + dealer_notes + all spec fields)
4. Extracts structured metadata for Pinecone filter queries
5. Upserts into a Pinecone index (creates it if it doesn't exist)
"""

import argparse
import json
import os
import re
import sys
import time
import hashlib
from typing import Optional

import pandas as pd
from pydantic import BaseModel, field_validator, model_validator
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI

from logger_setup import get_logger

logger = get_logger("trailer.ingest")

try:
    # Optional dependency: allows loading keys from a local `.env` file.
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
PINECONE_INDEX_NAME = os.environ.get("PINECONE_INDEX_NAME", "trailer-recommendations")
EMBEDDING_MODEL     = "text-embedding-3-small"   # 1536 dims
EMBEDDING_DIM       = 1536
BATCH_SIZE          = 96   # Pinecone upsert batch limit
METRIC              = "cosine"

PINECONE_CLOUD      = os.environ.get("PINECONE_CLOUD", "aws")
PINECONE_REGION     = os.environ.get("PINECONE_REGION", "us-east-1")


# ─────────────────────────────────────────────
# PYDANTIC METADATA MODEL
# (used both here for ingestion and in the chatbot for filter parsing)
# ─────────────────────────────────────────────
class TrailerMetadata(BaseModel):
    """
    All fields are optional so the chatbot can do partial-match filtering.
    Stored verbatim as Pinecone metadata and used as $eq / $lte / $gte filters.
    """
    condition:        Optional[str]   = None   # "New" | "Pre-Owned"
    price:            Optional[float] = None   # actual sale price
    msrp:             Optional[float] = None   # manufacturer suggested retail
    category:         Optional[str]   = None   # e.g. "Aluminum"
    subcategory:      Optional[str]   = None   # e.g. "Livestock"
    category_sub:     Optional[str]   = None   # "Enclosed > Unspecified" etc.
    make:             Optional[str]   = None   # normalised brand
    color:            Optional[str]   = None   # normalised colour
    hitch_type:       Optional[str]   = None   # "Bumper Pull" | "Gooseneck" …

    # Extra fields stored in metadata (not used for filtering but handy)
    title:            Optional[str]   = None
    url:              Optional[str]   = None
    year:             Optional[int]   = None
    length:           Optional[str]   = None
    gvwr:             Optional[str]   = None
    stock_number:     Optional[str]   = None

    @field_validator("condition", mode="before")
    @classmethod
    def normalise_condition(cls, v):
        if not v or str(v).strip().lower() in ("", "nan", "none"):
            return None
        v = str(v).strip()
        mapping = {"new": "New", "pre-owned": "Pre-Owned", "used": "Pre-Owned"}
        return mapping.get(v.lower(), v.title())

    @field_validator("color", "hitch_type", "make", "category", "subcategory", mode="before")
    @classmethod
    def title_case_str(cls, v):
        if not v or str(v).strip().lower() in ("", "nan", "none"):
            return None
        return str(v).strip().title()

    @field_validator("price", "msrp", mode="before")
    @classmethod
    def coerce_float(cls, v):
        if v is None:
            return None
        try:
            f = float(str(v).replace(",", "").strip())
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def safe_str(v) -> str:
    """Return empty string for null-ish values."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def parse_info_specs(raw: str) -> dict:
    """Parse the info_specs_json blob; return empty dict on failure."""
    if not raw or safe_str(raw) == "":
        return {}
    try:
        blob = json.loads(raw)
        info  = blob.get("info", {})
        specs = blob.get("specifications", {})
        return {**info, **specs}
    except json.JSONDecodeError:
        return {}


def normalise_row(row: pd.Series) -> dict:
    """
    Merge top-level columns with info_specs_json, giving column-level values
    priority (they are considered more authoritative than the JSON blob).
    Returns a flat normalised dict.
    """
    specs = parse_info_specs(safe_str(row.get("info_specs_json", "")))

    def pick(*keys, fallback=""):
        """First non-empty value from column row[key] or specs[key]."""
        for k in keys:
            v = safe_str(row.get(k, "")) or safe_str(specs.get(k, ""))
            if v:
                return v
        return fallback

    category    = pick("category").title()
    subcategory = pick("subcategory").title()

    # Build combined category>sub field
    if category and subcategory and subcategory.lower() not in ("unspecified", ""):
        category_sub = f"{category} > {subcategory}"
    elif category:
        category_sub = category
    else:
        category_sub = None

    return {
        # metadata filter fields
        "condition":    pick("condition"),
        "price":        pick("price"),
        "msrp":         pick("msrp"),
        "category":     category or None,
        "subcategory":  subcategory if subcategory and subcategory.lower() != "unspecified" else None,
        "category_sub": category_sub,
        "make":         pick("make"),
        "color":        pick("color"),
        "hitch_type":   pick("hitch_type"),
        # extra metadata
        "title":        pick("title"),
        "url":          pick("url"),
        "year":         specs.get("year", ""),
        "length":       specs.get("length", ""),
        "gvwr":         specs.get("gvwr", ""),
        "stock_number": specs.get("stock_number", pick("stock_number")),
        # embedding extras
        "model":        specs.get("model", ""),
        "trim":         specs.get("trim", ""),
        "vin":          specs.get("vin", ""),
        "dry_weight":   specs.get("dry_weight", ""),
        "axles":        specs.get("axles", ""),
        "axle_capacity":specs.get("axle_capacity", ""),
        "tires":        specs.get("tires", ""),
        "trailer_material": specs.get("trailer_material", ""),
        "dealer_notes": safe_str(row.get("dealer_notes", "")),
    }


def build_embedding_text(d: dict) -> str:
    """
    Concatenate all meaningful fields into a single string for embedding.
    Structured fields first (for semantic weight), free text last.
    """
    parts = [
        f"Title: {d['title']}",
        f"Make: {d['make']}",
        f"Model: {d['model']}",
        f"Year: {d['year']}",
        f"Category: {d['category_sub']}",
        f"Condition: {d['condition']}",
        f"Color: {d['color']}",
        f"Hitch Type: {d['hitch_type']}",
        f"Price: ${d['price']}",
        f"MSRP: ${d['msrp']}",
        f"Length: {d['length']}",
        f"GVWR: {d['gvwr']}",
        f"Axles: {d['axles']}",
        f"Axle Capacity: {d['axle_capacity']}",
        f"Dry Weight: {d['dry_weight']}",
        f"Tires: {d['tires']}",
        f"Material: {d['trailer_material']}",
        f"Trim: {d['trim']}",
    ]
    # Append dealer notes only if they look like real text (>20 chars, not just phone#)
    notes = d.get("dealer_notes", "")
    if notes and len(notes) > 20:
        parts.append(f"Notes: {notes}")

    return "\n".join(p for p in parts if not p.endswith(": ") and not p.endswith(": $"))


def stable_id(row_dict: dict) -> str:
    """
    Deterministic vector ID: use stock_number if present, else hash the URL,
    else hash the title. This ensures re-runs are idempotent (upsert).
    """
    if row_dict.get("stock_number"):
        return f"trailer-{row_dict['stock_number']}"
    seed = row_dict.get("url") or row_dict.get("title") or str(row_dict)
    return "trailer-" + hashlib.md5(seed.encode()).hexdigest()[:12]


def get_embeddings(texts: list[str], client: OpenAI) -> list[list[float]]:
    """Batch-embed texts with retry on rate limit."""
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [e.embedding for e in response.data]


def build_pinecone_metadata(md: TrailerMetadata, extra: dict) -> dict:
    """
    Convert Pydantic model + extra fields into a flat dict Pinecone accepts.
    Pinecone metadata values must be str | int | float | bool | list[str].
    """
    base = {k: v for k, v in md.model_dump().items() if v is not None}
    for k in ("year",):
        if extra.get(k):
            try:
                base[k] = int(extra[k])
            except (ValueError, TypeError):
                pass
    for k in ("length", "gvwr", "stock_number", "url", "title"):
        if extra.get(k):
            base[k] = str(extra[k])
    return base


# ─────────────────────────────────────────────
# MAIN INGESTION
# ─────────────────────────────────────────────
def ingest(file_path: str):
    # Load local .env first (if present) so users can "pass" API keys without
    # setting OS-wide environment variables (especially on Windows).
    if load_dotenv:
        logger.info("Loading local .env (python-dotenv present)")
        load_dotenv(override=False)
    else:
        logger.info("python-dotenv not installed; skipping .env load")

    # ── clients ──────────────────────────────
    pinecone_key = os.environ.get("PINECONE_API_KEY")
    openai_key   = os.environ.get("OPENAI_API_KEY")
    if not pinecone_key:
        logger.error("Missing PINECONE_API_KEY")
        sys.exit("ERROR: Set PINECONE_API_KEY environment variable.")
    if not openai_key:
        logger.error("Missing OPENAI_API_KEY")
        sys.exit("ERROR: Set OPENAI_API_KEY environment variable.")

    logger.info(
        "Initialising clients (index=%s cloud=%s region=%s model=%s)",
        PINECONE_INDEX_NAME,
        PINECONE_CLOUD,
        PINECONE_REGION,
        EMBEDDING_MODEL,
    )
    pc     = Pinecone(api_key=pinecone_key)
    openai = OpenAI(api_key=openai_key)

    # ── read file ─────────────────────────────
    print(f"📂  Reading {file_path} …")
    logger.info("Reading input file: %s", file_path)
    ext = file_path.lower().rsplit(".", 1)[-1]
    if ext in ("xlsx", "xlsm", "xls"):
        df = pd.read_excel(file_path, dtype=str)
    elif ext == "csv":
        df = pd.read_csv(file_path, dtype=str)
    else:
        logger.error("Unsupported file extension: %s", ext)
        sys.exit(f"ERROR: Unsupported file type .{ext}. Use .xlsx or .csv")

    # Fill NaN with empty string for safe processing
    df = df.fillna("")
    print(f"   {len(df)} rows found.")
    logger.info("Rows loaded: %s", len(df))

    # ── ensure Pinecone index exists ──────────
    existing = [idx.name for idx in pc.list_indexes()]
    if PINECONE_INDEX_NAME not in existing:
        print(f"🗄️   Creating Pinecone index '{PINECONE_INDEX_NAME}' …")
        logger.info("Creating Pinecone index: %s", PINECONE_INDEX_NAME)
        pc.create_index(
            name      = PINECONE_INDEX_NAME,
            dimension = EMBEDDING_DIM,
            metric    = METRIC,
            spec      = ServerlessSpec(cloud=PINECONE_CLOUD, region=PINECONE_REGION),
        )
        # Wait until ready
        while not pc.describe_index(PINECONE_INDEX_NAME).status["ready"]:
            print("   Waiting for index to be ready …")
            logger.info("Waiting for index to be ready...")
            time.sleep(3)
    else:
        print(f"✅  Using existing index '{PINECONE_INDEX_NAME}'.")
        logger.info("Using existing index: %s", PINECONE_INDEX_NAME)

    index = pc.Index(PINECONE_INDEX_NAME)

    # ── process rows ──────────────────────────
    vectors     = []   # (id, embedding, metadata)
    skipped     = 0

    for i, row in df.iterrows():
        norm = normalise_row(row)

        # Validate & build Pydantic metadata
        try:
            md = TrailerMetadata(**{
                "condition":    norm["condition"]    or None,
                "price":        norm["price"]        or None,
                "msrp":         norm["msrp"]         or None,
                "category":     norm["category"]     or None,
                "subcategory":  norm["subcategory"]  or None,
                "category_sub": norm["category_sub"] or None,
                "make":         norm["make"]          or None,
                "color":        norm["color"]         or None,
                "hitch_type":   norm["hitch_type"]    or None,
                "title":        norm["title"]          or None,
                "url":          norm["url"]            or None,
                "year":         norm["year"]           or None,
                "length":       norm["length"]         or None,
                "gvwr":         norm["gvwr"]           or None,
                "stock_number": norm["stock_number"]   or None,
            })
        except Exception as e:
            print(f"   ⚠️  Row {i} metadata validation failed: {e}. Skipping.")
            logger.warning("Row %s metadata validation failed; skipping. error=%s", i, e)
            skipped += 1
            continue

        embed_text = build_embedding_text(norm)
        if not embed_text.strip():
            print(f"   ⚠️  Row {i} produced empty embedding text. Skipping.")
            logger.warning("Row %s produced empty embedding text; skipping.", i)
            skipped += 1
            continue

        vec_id   = stable_id(norm)
        meta     = build_pinecone_metadata(md, norm)
        vectors.append((vec_id, embed_text, meta))

    print(f"\n📝  {len(vectors)} rows ready for embedding ({skipped} skipped).")
    logger.info("Rows ready: %s | skipped: %s", len(vectors), skipped)

    # ── embed in batches ──────────────────────
    upsert_batches = []
    for batch_start in range(0, len(vectors), BATCH_SIZE):
        batch     = vectors[batch_start : batch_start + BATCH_SIZE]
        texts     = [v[1] for v in batch]

        print(f"   🔢  Embedding rows {batch_start}–{batch_start + len(batch) - 1} …")
        logger.info("Embedding batch start=%s size=%s", batch_start, len(batch))
        embeddings = get_embeddings(texts, openai)
        logger.info("Embedding batch done (dims=%s)", len(embeddings[0]) if embeddings else None)

        upsert_batch = []
        for (vec_id, _, meta), emb in zip(batch, embeddings):
            upsert_batch.append({
                "id":       vec_id,
                "values":   emb,
                "metadata": meta,
            })
        upsert_batches.append(upsert_batch)

    # ── upsert to Pinecone ────────────────────
    total_upserted = 0
    for batch in upsert_batches:
        index.upsert(vectors=batch)
        total_upserted += len(batch)
        print(f"   ✅  Upserted {total_upserted}/{len(vectors)} vectors.")
        logger.info("Upserted batch (total_upserted=%s/%s)", total_upserted, len(vectors))

    print(f"\n🎉  Done! {total_upserted} trailers indexed in '{PINECONE_INDEX_NAME}'.")
    logger.info("Ingest done (total_upserted=%s)", total_upserted)
    stats = index.describe_index_stats()
    print(f"   Total vectors in index: {stats.total_vector_count}")
    logger.info("Index stats total_vector_count=%s", stats.total_vector_count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest trailer data into Pinecone.")
    parser.add_argument("--file", required=True, help="Path to .xlsx or .csv file")
    args = parser.parse_args()
    ingest(args.file)
