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


def phone_for_log(phone: Optional[str]) -> str:
    """Normalize phone to digits for log lines so the same user is identifiable across formats."""
    if not phone:
        return "unknown"
    digits = "".join(c for c in str(phone).strip() if c.isdigit())
    return digits if digits else "unknown"


def log_chat_exchange(user_phone: Optional[str], user_question: str, assistant_response: str) -> None:
    """Log one user question and assistant reply, keyed by phone when available."""
    uid = phone_for_log(user_phone)
    logger.info(
        "chat_exchange | user_phone=%s | question=%s | answer=%s",
        uid,
        (user_question or "").strip(),
        (assistant_response or "").strip(),
    )

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
You are the Trailer Space sales team assistant. Always speak as "we/our" (never "I/me").

You will receive a user's question and a list of matching trailers retrieved from inventory.

Critical rules:
- Never use negative/apology language such as: "apologize", "sorry", "unfortunately", "unexpectedly", "can't", "cannot".
- When listing recommendations (matches OR partial matches), the FIRST line of your message must be EXACTLY ONE of the following sentences, with no extra words before or after it:
    - Based on your preferences, here are some recommendations.
    - We’ve selected a few recommendations for you.
    - Take a look at these recommendations.
    - We suggest the following recommendations for you.
    - Check out these suggestions we found for you.
- The FIRST line must be generalized and must NOT mention, restate, or paraphrase any user filters (no "for <...>", no length, no category, no price, no hitch, no color).
- If there are no matches, do not apologise. Instead, just provide alternative.
- Do not mention embeddings, Pinecone, vector search, or internal tooling.

Output format:
- For each trailer, output EXACTLY this block (blank line between trailers):

**Name**: <title>
**Details**: <2–4 lines, conversational, highlight the most relevant specs for the user's request and also a little bit extra about the trailer; include condition/category/hitch when available>
**Color**: <color or N/A>
**Price**: <price formatted like $7,750 or 'Call for pricing' if price is not available or is zero>
For more information, please visit <url>

- Feel free to visit the links for more details or contact us at (979) 532-1486 if you have any questions
"""

_GENERIC_INTROS = (
    "Based on your preferences, here are some recommendations.",
    "We’ve selected a few recommendations for you.",
    "Take a look at these recommendations.",
    "We suggest the following recommendations for you.",
    "Check out these suggestions we found for you.",
)


def _enforce_generic_intro(text: str) -> str:
    """
    Guarantee a generalized first line that does not echo the user's filters.
    If the model starts with an allowed intro but appends extra (e.g. "for ..."),
    we trim it back to the exact allowed sentence.
    """
    s = (text or "").lstrip()
    if not s:
        return s

    # Work line-wise (Streamlit renders markdown; we want the first visible line stable).
    lines = s.splitlines()
    first = (lines[0] or "").strip()
    rest = "\n".join(lines[1:]).lstrip()

    for intro in _GENERIC_INTROS:
        if first == intro:
            return s
        if first.startswith(intro):
            # Trim any appended filters/fragments.
            return (intro + ("\n" + rest if rest else "")).strip()

    return s


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
        # Match if user provided either full category_sub OR either side of it.
        clauses.append(
            {
                "$or": [
                    {"category_sub": {"$eq": f.category_sub}},
                    {"category": {"$eq": f.category_sub}},
                    {"subcategory": {"$eq": f.category_sub}},
                ]
            }
        )
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

    def _truncate(v: object, n: int = 240) -> str:
        s = "" if v is None else str(v)
        s = s.replace("\n", " ").strip()
        return s if len(s) <= n else s[: n - 1] + "…"

    payload = []
    for i, m in enumerate(matches, 1):
        md = getattr(m, "metadata", {}) or {}
        payload.append(
            {
                "rank": i,
                "title": _truncate(md.get("title") or "(no title)", 200),
                "url": _truncate(md.get("url") or "N/A", 300),
                "price": md.get("price"),
                "msrp": md.get("msrp"),
                "condition": _truncate(md.get("condition"), 60),
                "category_sub": _truncate(md.get("category_sub"), 80),
                "hitch_type": _truncate(md.get("hitch_type"), 60),
                "color": _truncate(md.get("color"), 60),
                "length": _truncate(md.get("length"), 60),
                "gvwr": _truncate(md.get("gvwr"), 60),
                "make": _truncate(md.get("make"), 80),
                "score": round(float(getattr(m, "score", 0.0)), 4),
            }
        )

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    logger.debug("Formatted results JSON for LLM (truncated): %s", text[:1200])
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
    out = _enforce_generic_intro(out)
    logger.info("Answer generation done (len=%s)", len(out))
    logger.debug("Answer (truncated): %s", out[:800])

    # Guardrail: remove common negative/apology phrasing if it slips through.
    banned = [
        "unfortunately",
        "apologize",
        "apologies",
        "sorry",
        "unexpectedly",
        "i can't",
        "i cannot",
        "we can't",
        "we cannot",
    ]
    lowered = out.lower()
    if any(b in lowered for b in banned):
        logger.warning("Answer contained banned phrasing; applying light cleanup")
        for b in banned:
            # Remove case-insensitively by simple replacements on common forms.
            out = out.replace(b, "").replace(b.title(), "").replace(b.upper(), "")
        out = " ".join(out.split())

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
        log_chat_exchange(None, user_input, answer)

        # Keep multi-turn context (last 6 turns)
        conversation_history.append({"role": "user",      "content": user_input})
        conversation_history.append({"role": "assistant", "content": answer})
        conversation_history = conversation_history[-12:]


if __name__ == "__main__":
    main()
