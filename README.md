# Trailer Recommendation System — Architecture & Design Guide

## Overview

```
Excel / CSV file
       │
       ▼
trailer_pinecone_ingest.py          ← run once (or on every file update)
  1. Read & normalise rows
  2. Merge info_specs_json into flat dict
  3. Validate metadata via Pydantic
  4. Build rich embedding text
  5. Embed with OpenAI text-embedding-3-small
  6. Upsert to Pinecone (idempotent)
       │
       ▼
  Pinecone Index: "trailer-recommendations"
  ┌─────────────────────────────────┐
  │  vector (1536-dim)              │  ← semantic content
  │  metadata:                      │  ← filter fields
  │    condition, price, msrp,      │
  │    category_sub, make,          │
  │    color, hitch_type,           │
  │    title, url, year, length …   │
  └─────────────────────────────────┘
       │
       ▼
trailer_chatbot.py                  ← interactive recommendation loop
  1. User asks natural-language question
  2. OpenAI extracts structured TrailerFilterQuery
  3. Embed query with OpenAI
  4. Pinecone query: vector + metadata $filter
  5. OpenAI generates conversational answer
```

---

## Files

| File | Purpose |
|---|---|
| `trailer_pinecone_ingest.py` | One-shot ingestion / update script |
| `trailer_chatbot.py` | Interactive CLI chatbot |
| `requirements.txt` | Python dependencies |

---

## Pydantic Models

### `TrailerMetadata` (ingestion / stored in Pinecone)
Validated metadata stored alongside each vector. All fields are optional.

| Field | Type | Notes |
|---|---|---|
| `condition` | str? | "New" \| "Pre-Owned" — normalised to title-case |
| `price` | float? | Actual sale price — 0-valued entries → None |
| `msrp` | float? | Manufacturer Suggested Retail Price |
| `category_sub` | str? | `"{category} > {subcategory}"` e.g. "Enclosed > Unspecified" |
| `make` | str? | Brand / manufacturer — title-cased |
| `color` | str? | Title-cased ("Black", "White") |
| `hitch_type` | str? | Title-cased ("Bumper Pull", "Gooseneck") |

### `TrailerFilterQuery` (chatbot — parsed from user input)
Same fields as `TrailerMetadata` plus price range support.

| Field | Type | Pinecone operator |
|---|---|---|
| `condition` | str? | `$eq` |
| `price_min` | float? | `$gte` on `price` |
| `price_max` | float? | `$lte` on `price` |
| `msrp_max` | float? | `$lte` on `msrp` |
| `category_sub` | str? | `$eq` |
| `make` | str? | `$eq` |
| `color` | str? | `$eq` |
| `hitch_type` | str? | `$eq` |

---

## Data Cleaning Decisions

### `info_specs_json` merge strategy
- Column-level values (`make`, `color`, etc.) take priority over JSON blob values.
- `info` and `specifications` sub-keys are flattened into one dict.
- Missing or "nan" strings are treated as null.

### `category_sub` construction
- `"Enclosed" + "Unspecified"` → `"Enclosed"` (sub is dropped when meaningless)
- `"LIVESTOCK" + "Livestock"` → `"Livestock > Livestock"` (both normalised)
- All values are `.title()`-cased for consistency.

### `color` / `hitch_type` normalisation
Raw data has `"BLACK"` and `"BUMPER PULL"` (all-caps). Everything is `.title()`-cased
so Pinecone `$eq` filters match reliably.

### `price` / `msrp` = 0
Some rows have 0 in these fields (meaning unknown). These are stored as `None`
in metadata so they don't interfere with `$lte` / `$gte` price filters.

---

## Embedding Text Strategy

The embedding text is structured as labelled key-value pairs:

```
Title: 2024 Alcom Enclosed - 02133
Make: Alcom
Model: Enclosed
Year: 2024
Category: Enclosed
Condition: New
Color: White
Hitch Type: Bumper Pull
Price: $7750
MSRP: $10600
Length: 14 ft 0 in
GVWR: 7000#
...
Notes: [dealer notes if > 20 chars]
```

This maximises semantic relevance for queries like:
- "white bumper pull enclosed trailer" → exact field matches
- "small aluminum cargo trailer under $8k" → length/material/price context
- "livestock hauler with tack room" → notes / title match

---

## Running

### Step 1: Install dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Set environment variables
```bash
export PINECONE_API_KEY="pcsk_..."
export OPENAI_API_KEY="sk-..."
```

#### Windows-friendly option: use a local `.env` file
- Copy `.env.example` → `.env`
- Paste your real keys into `.env`
- The scripts will auto-load `.env` on startup (requires `python-dotenv`, included in `requirements.txt`)

### Step 3: Ingest data
```bash
python trailer_pinecone_ingest.py --file Trailer_Sample.xlsx
```
Re-run any time with a new/updated file. Upserts are idempotent (same
`stock_number` → same vector ID → overwrites the old entry).

### Step 4A: Chat (CLI)
```bash
python trailer_chatbot.py
```

Example queries:
- "Show me new bumper pull enclosed trailers under $9000"
- "Any used livestock trailers in black?"
- "What's the cheapest dump trailer you have?"
- "I need something for hauling cattle, budget is $35k"

### Step 4B: Chat (Streamlit UI)
```bash
streamlit run streamlit_app.py
```

---

## Extending to a Web API

Wrap `trailer_chatbot.py` logic in FastAPI:

```python
from fastapi import FastAPI
from pydantic import BaseModel as PBM

app = FastAPI()

class ChatRequest(PBM):
    message: str
    session_id: str

@app.post("/chat")
async def chat(req: ChatRequest):
    filters   = extract_filters(req.message, openai)
    embedding = embed_query(req.message, openai)
    matches   = search_pinecone(embedding, build_pinecone_filter(filters), index)
    answer    = answer_user(req.message, format_results_for_llm(matches), [], openai)
    return {"answer": answer, "filters_used": filters.model_dump(exclude_none=True)}
```

---

## Pinecone Index Specs

| Setting | Value |
|---|---|
| Name | `trailer-recommendations` |
| Dimensions | 1536 (OpenAI text-embedding-3-small) |
| Metric | cosine |
| Cloud | AWS us-east-1 (Serverless) |

---

## Deploying the Streamlit app (Streamlit Community Cloud)

### Prereqs
- Your code is pushed to GitHub (Streamlit Cloud deploys from a GitHub repo).
- Your Pinecone index already exists and has data (run `trailer_pinecone_ingest.py` locally first).

### 1) Create a GitHub repo and push
From the project folder:

```bash
git add .
git commit -m "Add Streamlit UI"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

Important:
- Do **not** commit your real `.env` (it’s ignored by `.gitignore`).
- It’s OK to commit `.env.example`.

### 2) Create the Streamlit app in the Cloud dashboard
1. Go to Streamlit Community Cloud and choose **New app**.
2. Select your GitHub repo + branch (`main`).
3. **Main file path**: `streamlit_app.py`
4. Click **Deploy**.

### 3) Add secrets (environment variables)
In the app’s settings, open **Secrets** and add:

```toml
OPENAI_API_KEY = "sk-..."
PINECONE_API_KEY = "pcsk_..."
PINECONE_INDEX_NAME = "trailer-recommendations"  # optional (defaults to this)
OPENAI_CHAT_MODEL = "gpt-4o-mini"                # optional
```

Notes:
- Streamlit Cloud exposes these as environment variables at runtime.
- You don’t use a `.env` file in the cloud; you use **Secrets**.

### 4) Confirm dependencies
Streamlit Cloud installs from `requirements.txt`. This repo includes `streamlit` and the
OpenAI/Pinecone deps needed for the UI.

### 5) Troubleshooting checklist
- **App shows “Missing required env vars”**: add the keys in **Secrets** (step 3).
- **Empty results**: confirm your Pinecone index name matches and your index has vectors.
- **Filter too strict**: the app retries without filters automatically if a filtered query returns 0.
