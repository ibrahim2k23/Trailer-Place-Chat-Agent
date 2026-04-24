# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
Conversational trailer recommendation chatbot for TrailerPlace (Wharton, TX). Customers chat naturally; the agent collects qualification slots, searches inventory via Pinecone vector DB, fit-aware reranks results, and returns matching trailers with full specs.

## Environment
- **Runtime:** `conda` env `islam360` — Python 3.10, Streamlit 1.43 (`pyproject.toml`/`uv.lock` exist but `uv` is not installed; use conda commands)
- **Working directory:** `E:/TrailerPlace/AGENT`
- **Config:** `.env` in project root (loaded via `python-dotenv`; without it, dotenv walks up and picks up the wrong keys from `E:\TrailerPlace\.env`)
- **`pyproject.toml` says `requires-python = ">=3.12"`** — ignore this; the actual runtime is 3.10 and the code must be compatible with it

## Stack
| Layer | Tech |
|---|---|
| Frontend | Streamlit 1.43 |
| LLM | OpenAI `gpt-4o-mini` (override: `OPENAI_MODEL`) |
| Embeddings | OpenAI `text-embedding-3-small` (dim: 1536) |
| Vector DB | Pinecone — index `trailerplace-listings` |
| Persistence | Supabase/Postgres (`conversation_history` table) |
| Data | `listings_final_v6.xlsx` — latest source |

## Running the App
```bash
# One-time ingestion (skip if index is already populated)
conda run -n islam360 python src/ingest.py

# Force re-index after data changes
conda run -n islam360 python src/ingest.py --force

# Launch app
conda run -n islam360 streamlit run app.py --server.headless true --server.port 8501
```

Note: `conda run` can't pass multi-line `-c` scripts — write them to a `.py` file first.

## Architecture

### Request flow (one user turn)
1. `app.py` receives the user message → calls `TrailerAgent.chat()`
2. `TrailerAgent.chat()` sends the full `_history` to OpenAI with `SEARCH_TOOL` available
3. If the model issues a `search_trailers` tool call → `_execute_tool_call()`:
   - Builds a `TrailerFilter` from tool args (normalizing via `src/normalizer.py`)
   - Infers missing hitch/weight/length from the query text and conversation history as fallback
   - Calls `_search()` → embeds query → Pinecone vector search (strict filter, then relaxed fallback)
   - Deduplicates matches (`_dedupe_matches`) by URL/title/listing_id
   - Fit-aware reranks (`_rerank_by_fit`) penalizing under/oversized trailers vs. stated requirements
   - Returns top `SEARCH_MAX_RECOMMENDATIONS` listings as JSON to the model
4. Model produces final text; `app.py` renders trailer cards for any listings mentioned in the reply
5. **Thinking agent** (`src/thinking_agent.py`): after each turn, a secondary LLM call (can be background) generates a Markdown trace of the search/rerank decisions for the sidebar debug view; falls back to a deterministic formatter if the LLM output is incomplete
6. Conversation turns are written asynchronously to Supabase via `src/conversation_store.py`

### Key modules
| File | Role |
|---|---|
| `src/agent.py` | `TrailerAgent`, `SEARCH_TOOL`, `_build_system_prompt()`, search/rerank/dedupe logic |
| `src/thinking_agent.py` | Turn-level reasoning explainer; async LLM call with deterministic fallback |
| `src/models.py` | Pydantic: `TrailerFilter`, `TrailerListing` |
| `src/normalizer.py` | `normalize_make/color/hitch/category` — canonical form for Pinecone metadata |
| `src/ingest.py` | One-time embed + upsert to Pinecone |
| `src/conversation_store.py` | Async Supabase writes; `enqueue_save_turn()` |
| `src/log_setup.py` | `configure_trailerplace_logging()` — console + `log/YYYY-MM-DD.log` + `thinking_log/YYYY-MM-DD-thinking.log` |
| `app.py` | Streamlit UI, CSS injection, auth, thinking-agent polling |

### Reranking (`_rerank_by_fit`)
When the caller provides `required_payload_lbs`, `required_length_ft`, or `required_gvwr_lbs`, each fetched listing gets a fit score = `base_score − penalty`. Penalty grows for under-capacity (heavy) and over-sized (extreme ratio) trailers. Phase logic: non-failing non-extreme → fill with non-failing oversized → last resort all entries sorted. Controlled by `RERANK_WARN_RATIO` (default 1.3) and `RERANK_EXTREME_RATIO` (default 1.5).

Sort key (`_merged_fit_sort_key`) precedence: `severe_oversize` (penalty > 3.0 with no fails) → `missing_count` → `payload_spec_tier` → `length_exact_rank` → `gvwr_spec_tier` → `has_all_required_dims` → `length_overage` → `penalty` → `-base_score`. The `severe_oversize` guard prevents a listing with complete spec data but massive oversize from beating a near-perfect-fit listing that is merely missing one spec.

### Streamlit UI notes
- **CSS injection:** `components.html()` zero-height iframe writing to `window.parent.document.head` — Streamlit sanitizes `<style>` in `st.markdown`
- **Chat pattern:** render bubble + response inline first, then `st.rerun()` — prevents "send twice" widget bug
- **Trailer cards:** inline HTML in `st.markdown(unsafe_allow_html=True)`
- **Thinking panel:** polls a `ThreadPoolExecutor` future every `THINKING_AGENT_POLL_MS` ms using `st.rerun()`

## Non-runtime scripts
- `append_lbs_units.py`, `build_info_spec_json.py` — one-off data prep scripts, not part of the runtime
- `create_conversation_table.py` — run once in Supabase to create the `conversation_history` table
- `CHANGES.md` — change log for prompt/agent updates; useful reference when diagnosing behavior regressions

## Pinecone Metadata Schema
```
condition, price, price_display, category, category_subcategory,
make, color, hitch_type, url, title,
payments_from, year, length, width, axles, gvwr,
payload_capacity, trailer_material, floor,
length_ft_num, gvwr_lbs_num
```
- `category` stored separately from `category_subcategory` for reliable exact-match filtering
- `price = 0` stored as `None` (excluded from range filters)
- `length_ft_num` / `gvwr_lbs_num` are numeric fields used by `required_length_ft` / `required_gvwr_lbs` Pinecone `$gte` filters
- **`required_payload_lbs` is NOT a Pinecone filter** — it lives only in `_execute_tool_call` and `_rerank_by_fit`; `TrailerFilter` does not carry it. Payload is purely a reranking signal, not a Pinecone `$gte` filter.
- **Strict → relaxed fallback:** if a strict Pinecone query returns 0 matches, `_search()` retries dropping all filter keys except `condition`, `price`, and `hitch_type`. Category, make, color, length, and GVWR filters are dropped in the relaxed pass.

## Environment Variables
```
# Required
OPENAI_API_KEY=...
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=trailerplace-listings

# Optional overrides
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
DATABASE_URL=postgresql+psycopg://USER:PASS@HOST:5432/postgres?sslmode=require

TRAILERPLACE_PERSIST_CHATS=1        # set to 0 to disable DB writes
TRAILERPLACE_WEBSITE=https://www.trailerplace.com
TRAILERPLACE_APP_USERNAME=          # enables HTTP basic auth if both set
TRAILERPLACE_APP_PASSWORD=

SEARCH_TOP_K=5                      # Pinecone top_k per query
SEARCH_MAX_RECOMMENDATIONS=3        # max listings returned to UI
SHOW_ONLY_LLM_MENTIONED_CARDS=1     # hide cards not cited in reply text

THINKING_AGENT_ENABLED=1
THINKING_AGENT_BACKGROUND=1         # run as background thread
THINKING_AGENT_MODEL=gpt-4o-mini
THINKING_AGENT_MAX_CONTEXT_CHARS=24000
THINKING_AGENT_POLL_MS=700
THINKING_WINDOW_TURNS=8             # conversation window fed to thinking agent

RERANK_WARN_RATIO=1.3
RERANK_EXTREME_RATIO=1.5

TRAILERPLACE_LOG_DIR=               # default: log/ next to app.py
TRAILERPLACE_THINKING_LOG_DIR=      # default: thinking_log/ next to app.py
```

## Logging
- Main logs: `log/YYYY-MM-DD.log` (daily roll-over, also to console)
- Thinking logs: `thinking_log/YYYY-MM-DD-thinking.log` (separate logger, no propagation)
- Product fetch events: logger `trailerplace.product_fetch` — one JSON line per Pinecone attempt with `query`, `pinecone_filter`, `match_count`, `matches`
- Rerank decisions: `RERANK_SCORE` lines per listing (auditable from logs)
- Sidebar debug: "Show product fetch debug" expands last turn's raw tool debug payload

## Thinking Agent notes
- Output is intentionally **user-friendly** — no raw ratios, penalty values, or field names in either the LLM path or the deterministic fallback
- `_score_friendly_reason(score, req_payload, req_length, req_gvwr)` translates ratios to plain English: `length_ratio=1.71` → `"24 ft — 10 ft longer than the 14 ft requested"`; `payload_ratio=None` → `"payload capacity not listed in this trailer's inventory data"`
- Deterministic output structure — three sections: **What the Customer Asked For** / **How We Searched** / **Why Each Trailer Was Ranked This Way**. Each listing entry uses its full title, marked `*(shown to customer)*` or `*(not shown — undersized)*`
- Deterministic fallback (`_deterministic_thinking_flow`) is always run first; LLM output replaces it only if it passes `_recommendation_output_is_acceptable` anchor checks (`# Thinking Flow`, `shown to customer`, `How We Searched`)
- LLM prompt explicitly forbids raw decimals, ratio numbers, penalty values, and field names like `fail_count`/`missing_count`/`base_score`
- `_fmt_num` is still present for internal use but is no longer used in any user-facing output path

## Supabase Setup
Run `create_conversation_table.py` (or the SQL it generates) once in Supabase. Each user turn appends to `conversation_history.messages`; `tool_call` / `tool_call_result` columns store filter metadata and recommended rows.
