# TrailerPlace AI Assistant — CLAUDE.md

## Project Overview
A conversational trailer recommendation chatbot for TrailerPlace (Wharton, TX).
Customers chat naturally, the agent searches the inventory via Pinecone vector DB, and returns matching trailers with full specs.

## Environment
- **Conda env:** `islam360` — always use this for running scripts
- **Python:** 3.10
- **Working directory:** `E:/TrailerPlace/DEMO POC`

## Stack
| Layer | Tech |
|---|---|
| Frontend | Streamlit 1.43 |
| LLM | OpenAI `gpt-4o-mini` |
| Embeddings | OpenAI `text-embedding-3-small` (dim: 1536) |
| Vector DB | Pinecone — index `trailerplace-listings` |
| Data | `listings_final_v4.xlsx` — 282 trailer listings |

## Project Structure
```
DEMO POC/
├── app.py                  # Streamlit frontend — entry point
├── src/
│   ├── models.py           # Pydantic: TrailerFilter, TrailerListing
│   ├── normalizer.py       # On-the-fly normalization (casing, make dedup, etc.)
│   ├── ingest.py           # One-time ingestion: embed + upsert to Pinecone
│   └── agent.py            # TrailerAgent: OpenAI tool calling + Pinecone search
├── .env                    # API keys (OPENAI_API_KEY, PINECONE_API_KEY)
├── .streamlit/
│   └── config.toml         # Base theme: orange primary, warm background
└── listings_final_v4.xlsx  # Source data
```

## Running the App
```bash
# One-time ingestion (skip if index already has 282 vectors)
conda run -n islam360 python src/ingest.py

# Launch app
conda run -n islam360 streamlit run app.py
```

## Data & Normalization
- **Source fields:** title, make, subcategory, category, condition, price, payments_from, color, hitch_type, info_specs_json, dealer_notes, url
- `info_specs_json` has two keys: `info` (all structured specs) and `specifications` (mostly empty)
- Normalization is done **on the fly** in `src/normalizer.py` — no pre-processing step
- Key issues normalized: mixed casing (BLACK/Black), duplicate makes (Diamond C / Diamond C Trailers), junk subcategories (Unspecified, model numbers), dealer contact noise in `dealer_notes`

## Pinecone Metadata Schema
```
condition, price, price_display, category, category_subcategory,
make, color, hitch_type, url, title,
payments_from, year, length, width, axles, gvwr,
payload_capacity, trailer_material, floor
```
- `category` is stored separately (in addition to `category_subcategory`) for reliable exact-match filtering
- `price_display` is always set (`$X,XXX` or `Call for price`); numeric `price` is only stored when known (for range filters)
- `price = 0` rows are stored as `None` (excluded from price range filters)

## Agent Behavior
- **First message:** customer sends name, phone, email → agent greets by first name
- **Tool calling:** `search_trailers(query, ...filters)` — agent decides when to call it vs. ask a clarifying question
- **Recommendation logic:** returns **1 trailer by default**; returns 2–3 only if the 2nd/3rd results are within `0.04` cosine score of the top result (`_pick_listings()` in `agent.py`)
- **Filter fallback:** if strict metadata filters return no results, retries with only price/condition/hitch filters

## Streamlit UI Notes
- **CSS injection:** done via `components.html()` zero-height iframe with JS writing to `window.parent.document.head` — Streamlit 1.43 sanitizes `<style>` tags in `st.markdown`, so this is the only reliable approach
- **Chat pattern:** render user bubble + response inline first, then call `st.rerun()` — this shows messages immediately AND resets widget state (prevents the "send twice" bug)
- **Trailer cards:** pure inline HTML inside `st.markdown(unsafe_allow_html=True)` — no class dependencies, all inline styles
- **Theme:** warm off-white `#F5F3EF` background, dark zinc `#18181B` sidebar, orange `#F97316` accent

## API Keys (.env)
```
OPENAI_API_KEY=...
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=trailerplace-listings
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

## Re-indexing
```bash
# Force re-index (e.g. after data changes)
conda run -n islam360 python src/ingest.py --force
```
