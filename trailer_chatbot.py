"""
Trailer Recommendation Chatbot
================================
Connects to:
  • OpenAI            — filter extraction + answer generation + query embedding
  • Pinecone          — vector search with metadata filters

Usage:
    export PINECONE_API_KEY="..."
    export OPENAI_API_KEY="..."
    python trailer_chatbot.py

The chatbot:
1. Receives a natural-language query (e.g. "I need a bumper pull enclosed trailer under $8000")
2. Uses OpenAI to extract structured filters (condition, price range, make, color, hitch_type, category_sub)
3. Embeds the query with OpenAI
4. Queries Pinecone with the embedding + metadata filters
5. Returns top-K matches, formatted naturally by OpenAI
"""

import json
import os
import sys
from typing import Optional, Any

from openai import OpenAI
from pinecone import Pinecone
from pydantic import BaseModel, field_validator

from logger_setup import get_logger

logger = get_logger("trailer.chatbot")

try:
    # Optional dependency: allows loading keys from a local `.env` file.
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
PINECONE_INDEX_NAME = os.environ.get("PINECONE_INDEX_NAME", "trailer-recommendations")
EMBEDDING_MODEL     = "text-embedding-3-small"
TOP_K               = 5      # results to return from Pinecone
OPENAI_CHAT_MODEL   = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")


# ─────────────────────────────────────────────
# FILTER EXTRACTION MODEL
# mirrors the ingestion metadata model — all optional
# ─────────────────────────────────────────────
class TrailerFilterQuery(BaseModel):
    """
    Structured filters parsed from the user's natural-language query.
    Only non-None fields are passed to Pinecone as metadata filters.
    """
    condition:     Optional[str]   = None   # "New" | "Pre-Owned"
    price_min:     Optional[float] = None   # lower bound for price
    price_max:     Optional[float] = None   # upper bound for price
    msrp_max:      Optional[float] = None
    category_sub:  Optional[str]   = None   # e.g. "Enclosed > Unspecified"
    make:          Optional[str]   = None
    color:         Optional[str]   = None
    hitch_type:    Optional[str]   = None   # "Bumper Pull" | "Gooseneck" …

    @field_validator("condition", mode="before")
    @classmethod
    def norm_condition(cls, v):
        if not v:
            return None
        mapping = {"new": "New", "pre-owned": "Pre-Owned", "used": "Pre-Owned"}
        return mapping.get(str(v).lower(), str(v).title())

    @field_validator("color", "hitch_type", "make", mode="before")
    @classmethod
    def norm_str(cls, v):
        return str(v).strip().title() if v else None


FILTER_EXTRACTION_SYSTEM = """\
You are a filter-extraction assistant for a trailer recommendation system.
Given a user's natural-language query, extract structured search filters and return them as a JSON object.

The JSON must match this schema (all fields are optional — only include what the user implies):
{
  "condition":    "New" | "Pre-Owned" | null,
  "price_min":    number | null,
  "price_max":    number | null,
  "msrp_max":     number | null,
  "category_sub": string | null,   // e.g. "Enclosed", "Livestock", "Dump", "Utility"
  "make":         string | null,   // brand / manufacturer
  "color":        string | null,
  "hitch_type":   "Bumper Pull" | "Gooseneck" | null
}

Return ONLY the JSON object, no explanation, no markdown fences.
"""

ANSWER_SYSTEM = """\
You are a friendly and knowledgeable trailer salesperson assistant.
You will receive a user's question and a list of matching trailers retrieved from inventory.
Your job:
1. Summarise the top matches conversationally — highlight the most relevant specs.
2. If no trailers were found, apologise and suggest broadening the search.
3. Keep the response concise but informative (2–4 sentences per trailer).
4. Always mention price, condition, category, and hitch type if available.
5. End with a gentle call-to-action (visit the URL or contact the dealer).
"""


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def _extract_json_object(text: str) -> dict:
    """
    Best-effort JSON extractor. Handles accidental markdown fences and
    extra text before/after the object.
    """
    raw = (text or "").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        obj = json.loads(raw)
        logger.debug("Extracted JSON object directly: keys=%s", list(obj.keys()) if isinstance(obj, dict) else type(obj))
        return obj
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(raw[start : end + 1])
            logger.debug("Extracted JSON object via slicing: keys=%s", list(obj.keys()) if isinstance(obj, dict) else type(obj))
            return obj
        raise


def extract_filters(query: str, openai_client: OpenAI) -> TrailerFilterQuery:
    """Use OpenAI to extract structured filters from free-text query."""
    logger.info("Filter extraction start (len=%s)", len(query or ""))
    resp = openai_client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[
            {"role": "system", "content": FILTER_EXTRACTION_SYSTEM},
            {"role": "user", "content": query},
        ],
        temperature=0,
    )
    raw = resp.choices[0].message.content or ""
    logger.debug("Filter extraction raw response (truncated): %s", raw[:800])
    data = _extract_json_object(raw)
    f = TrailerFilterQuery(**data)
    logger.info("Filter extraction parsed: %s", f.model_dump(exclude_none=True))
    return f


def build_pinecone_filter(f: TrailerFilterQuery) -> Optional[dict]:
    """Convert TrailerFilterQuery into a Pinecone metadata filter dict."""
    clauses = []

    if f.condition:
        clauses.append({"condition": {"$eq": f.condition}})
    if f.make:
        clauses.append({"make": {"$eq": f.make}})
    if f.color:
        clauses.append({"color": {"$eq": f.color}})
    if f.hitch_type:
        clauses.append({"hitch_type": {"$eq": f.hitch_type}})
    if f.category_sub:
        # Partial match: category_sub might be "Enclosed > Unspecified";
        # if user says "Enclosed" we do a substring approach via $in would need
        # exact values. Use $eq on the category portion by normalising.
        clauses.append({"category_sub": {"$eq": f.category_sub}})
    if f.price_min is not None or f.price_max is not None:
        price_clause: dict = {}
        if f.price_min is not None:
            price_clause["$gte"] = f.price_min
        if f.price_max is not None:
            price_clause["$lte"] = f.price_max
        clauses.append({"price": price_clause})
    if f.msrp_max is not None:
        clauses.append({"msrp": {"$lte": f.msrp_max}})

    if not clauses:
        logger.info("Pinecone filter: none")
        return None
    if len(clauses) == 1:
        logger.info("Pinecone filter built: %s", clauses[0])
        return clauses[0]
    built = {"$and": clauses}
    logger.info("Pinecone filter built: %s", built)
    return built


def embed_query(text: str, openai_client: OpenAI) -> list[float]:
    logger.info("Embedding query (len=%s) model=%s", len(text or ""), EMBEDDING_MODEL)
    resp = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=[text])
    emb = resp.data[0].embedding
    logger.info("Embedding generated (dims=%s)", len(emb))
    return emb


def search_pinecone(embedding: list[float], pinecone_filter: Optional[dict],
                    index) -> list[dict]:
    logger.info("Pinecone query start (top_k=%s, filter=%s)", TOP_K, bool(pinecone_filter))
    kwargs = {"vector": embedding, "top_k": TOP_K, "include_metadata": True}
    if pinecone_filter:
        kwargs["filter"] = pinecone_filter
    results = index.query(**kwargs)
    matches = results.matches
    logger.info("Pinecone query done (matches=%s)", len(matches))
    return matches


def format_results_for_llm(matches: list) -> str:
    if not matches:
        logger.info("Formatting results: no matches")
        return "No matching trailers found."
    lines = []
    for i, m in enumerate(matches, 1):
        md = m.metadata
        line = (
            f"[{i}] {md.get('title','(no title)')}\n"
            f"    Make: {md.get('make','?')}  |  Condition: {md.get('condition','?')}\n"
            f"    Category: {md.get('category_sub','?')}  |  Hitch: {md.get('hitch_type','?')}\n"
            f"    Color: {md.get('color','?')}  |  Price: ${md.get('price','?')}  |  MSRP: ${md.get('msrp','?')}\n"
            f"    Length: {md.get('length','?')}  |  GVWR: {md.get('gvwr','?')}\n"
            f"    URL: {md.get('url','N/A')}\n"
            f"    Score: {m.score:.3f}"
        )
        lines.append(line)
    text = "\n\n".join(lines)
    logger.debug("Formatted results for LLM (truncated): %s", text[:1000])
    return text


def answer_user(user_query: str, results_text: str,
                conversation_history: list,
                openai_client: OpenAI) -> str:
    """Generate a conversational answer using OpenAI."""
    logger.info(
        "Answer generation start (q_len=%s, history_msgs=%s, results_len=%s)",
        len(user_query or ""),
        len(conversation_history or []),
        len(results_text or ""),
    )
    messages = conversation_history + [
        {
            "role": "user",
            "content": (
                f"User question: {user_query}\n\n"
                f"Matching inventory:\n{results_text}"
            ),
        }
    ]
    resp = openai_client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[{"role": "system", "content": ANSWER_SYSTEM}, *messages],
        temperature=0.7,
    )
    out = (resp.choices[0].message.content or "").strip()
    logger.info("Answer generation done (len=%s)", len(out))
    logger.debug("Answer (truncated): %s", out[:800])
    return out


# ─────────────────────────────────────────────
# PUBLIC API (importable for Streamlit / web)
# ─────────────────────────────────────────────
def load_env_if_present() -> None:
    """
    Load a local `.env` file if python-dotenv is installed.
    This is a no-op in environments without `.env` (e.g. Streamlit Cloud),
    where secrets should be configured as environment variables.
    """
    if load_dotenv:
        logger.info("Loading local .env (python-dotenv present)")
        load_dotenv(override=False)
    else:
        logger.info("python-dotenv not installed; skipping .env load")


def init_clients() -> tuple[OpenAI, Any, Any]:
    """
    Initialise OpenAI + Pinecone clients and return (openai, pinecone, index).
    Raises a clear error if required environment variables are missing.
    """
    load_env_if_present()

    pinecone_key = os.environ.get("PINECONE_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not all([pinecone_key, openai_key]):
        missing = [k for k, v in {
            "PINECONE_API_KEY": pinecone_key,
            "OPENAI_API_KEY": openai_key,
        }.items() if not v]
        logger.error("Missing env vars: %s", missing)
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    logger.info("Initialising clients (index=%s, chat_model=%s)", PINECONE_INDEX_NAME, OPENAI_CHAT_MODEL)
    pc = Pinecone(api_key=pinecone_key)
    openai = OpenAI(api_key=openai_key)
    index = pc.Index(PINECONE_INDEX_NAME)
    logger.info("Clients initialised")
    return openai, pc, index


def chat_once(
    user_input: str,
    conversation_history: list[dict],
    *,
    openai_client: OpenAI,
    index: Any,
    retry_without_filter: bool = True,
) -> dict:
    """
    Execute one full turn:
    - extract filters
    - embed user query
    - query Pinecone (optionally retry without filter)
    - generate assistant answer
    Returns a dict containing answer + debug info for UIs.
    """
    user_input = (user_input or "").strip()
    if not user_input:
        raise ValueError("user_input is empty")

    logger.info("Chat turn start: %s", user_input)
    filters_used: dict = {}
    filter_error: Optional[str] = None
    try:
        filters = extract_filters(user_input, openai_client)
        filters_used = filters.model_dump(exclude_none=True)
    except Exception as e:
        filter_error = str(e)
        logger.exception("Filter extraction failed; continuing unfiltered")
        filters = TrailerFilterQuery()

    pinecone_filter = build_pinecone_filter(filters)
    embedding = embed_query(user_input, openai_client)
    matches = search_pinecone(embedding, pinecone_filter, index)

    retried_unfiltered = False
    if retry_without_filter and (not matches) and pinecone_filter:
        logger.info("No filtered matches; retrying unfiltered")
        matches = search_pinecone(embedding, None, index)
        retried_unfiltered = True

    results_text = format_results_for_llm(matches)
    answer = answer_user(user_input, results_text, conversation_history, openai_client)

    logger.info(
        "Chat turn done (matches=%s, retried_unfiltered=%s, filters=%s)",
        len(matches),
        retried_unfiltered,
        bool(filters_used),
    )
    return {
        "answer": answer,
        "filters_used": filters_used,
        "filter_error": filter_error,
        "match_count": len(matches),
        "retried_unfiltered": retried_unfiltered,
        "results_text": results_text,
    }


# ─────────────────────────────────────────────
# CHATBOT LOOP
# ─────────────────────────────────────────────
def main():
    try:
        openai, pc, index = init_clients()
    except Exception as e:
        logger.exception("Failed to init clients")
        sys.exit(f"ERROR: {e}")

    conversation_history: list = []

    print("🚛  Trailer Recommendation Chatbot")
    print("    Type your query (or 'quit' to exit)\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            logger.info("CLI session ended by user")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            logger.info("CLI quit command received")
            break

        print("   🔎  Thinking …", end="", flush=True)
        result = chat_once(
            user_input,
            conversation_history,
            openai_client=openai,
            index=index,
            retry_without_filter=True,
        )
        answer = result["answer"]
        if result.get("filters_used"):
            print(f" → filters={result['filters_used']} | matches={result['match_count']}")
        else:
            print(f" → matches={result['match_count']}")

        print(f"\nAssistant: {answer}\n")

        # Keep multi-turn context (last 6 turns)
        conversation_history.append({"role": "user",      "content": user_input})
        conversation_history.append({"role": "assistant", "content": answer})
        conversation_history = conversation_history[-12:]


if __name__ == "__main__":
    main()
