"""
Conversational trailer recommendation agent.
Uses OpenAI tool calling to decide when to search vs. ask clarifying questions.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone

from src.models import TrailerFilter, TrailerListing
from src.normalizer import normalize_make, normalize_color, normalize_hitch, normalize_category

load_dotenv()

logger = logging.getLogger(__name__)
product_fetch_log = logging.getLogger("trailerplace.product_fetch")


def _log_product_fetch_event(event: dict[str, Any]) -> None:
    payload = {**event, "ts": datetime.now(timezone.utc).isoformat()}
    product_fetch_log.info(json.dumps(payload, ensure_ascii=True, default=str))


def _match_summary_for_log(match: dict) -> dict[str, Any]:
    m = match.get("metadata", {}) or {}
    return {
        "id": match.get("id"),
        "score": round(float(match.get("score", 0.0)), 6),
        "title": m.get("title", "") or "",
        "url": m.get("url", "") or "",
    }


def _canonical_listing_key_from_match(match: dict) -> str:
    """
    Stable dedupe key for logical listing identity across re-ingests.
    Prefer URL path, then title stock suffix, then listing_id/id.
    """
    md = match.get("metadata", {}) or {}
    url = str(md.get("url", "") or "").strip().lower()
    if url:
        path = urlparse(url).path.strip("/").lower()
        if path:
            return f"url:{path}"

    title = str(md.get("title", "") or "").strip().lower()
    m = re.search(r"\s-\s*(\d+)\s*$", title)
    if m:
        return f"stock:{m.group(1)}"

    listing_id = str(md.get("listing_id", "") or "").strip().lower()
    if listing_id:
        return f"listing:{listing_id}"

    mid = str(match.get("id", "") or "").strip().lower()
    return f"id:{mid}" if mid else f"fallback:{id(match)}"


def _match_metadata_richness(match: dict) -> int:
    """Higher means richer metadata (used to break ties during dedupe)."""
    md = match.get("metadata", {}) or {}
    score = 0
    for v in md.values():
        if v is None:
            continue
        s = str(v).strip()
        if s:
            score += 1
    return score


def _dedupe_matches(matches: list[dict]) -> tuple[list[dict], dict[str, Any]]:
    """
    Collapse duplicate logical listings (same URL/title stock/listing id),
    keeping the best candidate by higher score then richer metadata.
    """
    if not matches:
        return matches, {"before_count": 0, "after_count": 0, "dropped_count": 0}

    best_by_key: dict[str, dict] = {}
    for m in matches:
        k = _canonical_listing_key_from_match(m)
        prev = best_by_key.get(k)
        if prev is None:
            best_by_key[k] = m
            continue
        prev_score = float(prev.get("score", 0.0))
        curr_score = float(m.get("score", 0.0))
        if curr_score > prev_score:
            best_by_key[k] = m
            continue
        if curr_score == prev_score and _match_metadata_richness(m) > _match_metadata_richness(prev):
            best_by_key[k] = m

    deduped = sorted(best_by_key.values(), key=lambda x: float(x.get("score", 0.0)), reverse=True)
    return deduped, {
        "before_count": len(matches),
        "after_count": len(deduped),
        "dropped_count": max(0, len(matches) - len(deduped)),
    }


def _env_positive_int(
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: Optional[int] = None,
) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = int(str(raw).strip(), 10)
    except ValueError:
        logger.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default
    if v < minimum:
        return minimum
    if maximum is not None and v > maximum:
        return maximum
    return v


def _env_positive_float(
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: Optional[float] = None,
) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = float(str(raw).strip())
    except ValueError:
        logger.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default
    if v < minimum:
        return minimum
    if maximum is not None and v > maximum:
        return maximum
    return v


OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "trailerplace-listings")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
# Pinecone query top_k (how many vector matches to retrieve per search)
SEARCH_TOP_K = _env_positive_int("SEARCH_TOP_K", 5, minimum=1)
# Max listings returned to the UI / tool after score-threshold filtering (at least 1)
SEARCH_MAX_RECOMMENDATIONS = _env_positive_int("SEARCH_MAX_RECOMMENDATIONS", 3, minimum=1)
THINKING_WINDOW_TURNS = _env_positive_int("THINKING_WINDOW_TURNS", 8, minimum=2, maximum=40)
# Post-ranking thresholds for over-sizing penalties.
RERANK_WARN_RATIO = _env_positive_float("RERANK_WARN_RATIO", 1.3, minimum=1.0)
RERANK_EXTREME_RATIO = _env_positive_float(
    "RERANK_EXTREME_RATIO", 1.5, minimum=RERANK_WARN_RATIO
)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


# When True, Streamlit shows cards only for listings referenced in the assistant's final text.
SHOW_ONLY_LLM_MENTIONED_CARDS = _env_bool("SHOW_ONLY_LLM_MENTIONED_CARDS", default=True)


def _listing_mentioned_in_reply(text: str, listing: TrailerListing) -> bool:
    """Heuristic: reply text cites this listing (URL, title, trailing stock id, or stock_* id)."""
    if not (text or "").strip():
        return False
    t = text.lower()
    url = (listing.url or "").strip()
    if url and url.lower() in t:
        return True
    path = urlparse(url).path.strip("/").lower()
    if path and path in t:
        return True
    title = (listing.title or "").strip()
    if title and title.lower() in t:
        return True
    stock_tail = re.search(r"\s-\s*(\d+)\s*$", title)
    if stock_tail:
        num = stock_tail.group(1)
        if re.search(rf"\b{re.escape(num)}\b", text):
            return True
    lid = (listing.listing_id or "").strip()
    m = re.match(r"(?i)stock[_-](\d+)$", lid)
    if m and re.search(rf"\b{re.escape(m.group(1))}\b", text):
        return True
    return False


def filter_listings_matching_reply(text: str, listings: list[TrailerListing]) -> list[TrailerListing]:
    if not listings:
        return []
    if not SHOW_ONLY_LLM_MENTIONED_CARDS:
        return listings
    if not (text or "").strip():
        return listings
    matched = [lst for lst in listings if _listing_mentioned_in_reply(text, lst)]
    if not matched:
        logger.info(
            "SHOW_ONLY_LLM_MENTIONED_CARDS: no reply match for any of %s listings — showing all",
            len(listings),
        )
        return listings
    logger.info(
        "SHOW_ONLY_LLM_MENTIONED_CARDS: showing %s of %s listings",
        len(matched),
        len(listings),
    )
    return matched


if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


# ---------------------------------------------------------------------------
# Pinecone search
# ---------------------------------------------------------------------------

def _build_pinecone_filter(f: TrailerFilter) -> Optional[dict]:
    pf: dict = {}

    if f.condition:
        pf["condition"] = {"$eq": f.condition}

    price_filter: dict = {}
    if f.price_min is not None:
        price_filter["$gte"] = f.price_min
    if f.price_max is not None:
        price_filter["$lte"] = f.price_max
    if price_filter:
        pf["price"] = price_filter

    if f.hitch_type:
        normalized = normalize_hitch(f.hitch_type)
        if normalized:
            pf["hitch_type"] = {"$eq": normalized}
    if f.required_length_ft is not None:
        pf["length_ft_num"] = {"$gte": f.required_length_ft}
    if f.required_gvwr_lbs is not None:
        pf["gvwr_lbs_num"] = {"$gte": f.required_gvwr_lbs}

    if f.make:
        normalized = normalize_make(f.make)
        pf["make"] = {"$eq": normalized}

    if f.color:
        normalized = normalize_color(f.color)
        pf["color"] = {"$eq": normalized}

    if f.category_subcategory:
        # Extract the main category part for filtering (before " > ")
        category = f.category_subcategory.split(" > ")[0].strip()
        normalized = normalize_category(category)
        pf["category"] = {"$eq": normalized}

    return pf if pf else None


def _coerce_money_metadata(val) -> Optional[float]:
    """Pinecone may return numbers or string numerics for price."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            f = float(val)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None
    s = str(val).strip()
    if not s:
        return None
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    try:
        f = float(s)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def _coerce_positive_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            f = float(val)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None
    s = str(val).strip()
    if not s:
        return None
    try:
        f = float(s)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _word_number_to_float(token: str) -> Optional[float]:
    t = (token or "").strip().lower()
    if not t:
        return None
    mapping = {
        "zero": 0.0,
        "one": 1.0,
        "two": 2.0,
        "three": 3.0,
        "four": 4.0,
        "five": 5.0,
        "six": 6.0,
        "seven": 7.0,
        "eight": 8.0,
        "nine": 9.0,
        "ten": 10.0,
        "half": 0.5,
        "quarter": 0.25,
    }
    if t in mapping:
        return mapping[t]
    try:
        return float(t.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_lbs(val) -> Optional[float]:
    """Parse and normalize weight to pounds (lbs)."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            f = float(val)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None
    s = str(val).strip().lower()
    if not s:
        return None
    m = re.search(
        r"\b(\d+(?:,\d{3})*(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|half|quarter)\s*(lbs?|pounds?|#|kg|kgs|kilograms?|ton|tons|tonne|tonnes)\b",
        s,
    )
    if m:
        qty = _word_number_to_float(m.group(1))
        unit = (m.group(2) or "").lower()
        if qty is None:
            return None
        if unit in ("lb", "lbs", "pound", "pounds", "#"):
            out = qty
        elif unit in ("kg", "kgs", "kilogram", "kilograms"):
            out = qty * 2.2046226218
        else:
            # Assume US short ton (2000 lbs) for "ton"/"tons".
            out = qty * 2000.0
        return out if out > 0 else None

    # Fallback: any positive number when unit is omitted.
    m2 = re.search(r"(\d+(?:,\d{3})*(?:\.\d+)?)", s)
    if not m2:
        return None
    try:
        f = float(m2.group(1).replace(",", ""))
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_length_ft(val) -> Optional[float]:
    """Parse and normalize length to feet."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            f = float(val)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None
    s = str(val).strip().lower()
    if not s:
        return None

    # Feet markers: ft, feet, ', `, ′
    # Inches markers: in, inches, ", ``, ″
    ft_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ft|feet|['′]|`(?!`))", s)
    in_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:in|inch|inches|\"|``|″)", s)
    if ft_m:
        ft = float(ft_m.group(1))
        inches = float(in_m.group(1)) if in_m else 0.0
        total = ft + (inches / 12.0)
        return total if total > 0 else None
    if in_m:
        total = float(in_m.group(1)) / 12.0
        return total if total > 0 else None
    yd_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:yd|yds|yard|yards)\b", s)
    if yd_m:
        total = float(yd_m.group(1)) * 3.0
        return total if total > 0 else None
    m_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|meter|meters|metre|metres)\b", s)
    if m_m:
        total = float(m_m.group(1)) * 3.280839895
        return total if total > 0 else None
    cm_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:cm|centimeter|centimeters|centimetre|centimetres)\b", s)
    if cm_m:
        total = float(cm_m.group(1)) / 30.48
        return total if total > 0 else None
    mm_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm|millimeter|millimeters|millimetre|millimetres)\b", s)
    if mm_m:
        total = float(mm_m.group(1)) / 304.8
        return total if total > 0 else None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    return None


def _extract_weight_lbs_from_text(text: str) -> Optional[float]:
    if not (text or "").strip():
        return None
    t = text.lower()
    m = re.search(
        r"\b(\d+(?:,\d{3})*(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|half|quarter)\s*(lbs?|pounds?|#|kg|kgs|kilograms?|ton|tons|tonne|tonnes)\b",
        t,
    )
    if not m:
        return None
    qty = _word_number_to_float(m.group(1))
    if qty is None or qty <= 0:
        return None
    unit = (m.group(2) or "").lower()
    if unit in ("lb", "lbs", "pound", "pounds", "#"):
        out = qty
    elif unit in ("kg", "kgs", "kilogram", "kilograms"):
        out = qty * 2.2046226218
    else:
        # Assume US short ton for customer-facing towing conversations.
        out = qty * 2000.0
    return out if out > 0 else None


def _extract_length_ft_from_text(text: str) -> Optional[float]:
    if not (text or "").strip():
        return None
    t = text.lower()
    # patterns: "7 ft 6 in", "7ft", "7'6\"", "7`6``", "7'"
    m = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(?:ft|feet|['′]|`(?!`))\s*(?:(\d+(?:\.\d+)?)\s*(?:in|inch|inches|\"|``|″))?",
        t,
    )
    if m:
        ft = float(m.group(1))
        inches = float(m.group(2)) if m.group(2) else 0.0
        total = ft + (inches / 12.0)
        return total if total > 0 else None

    # If text explicitly says "length" and gives a bare number, treat as feet.
    m2 = re.search(r"\blength\b[^\d]{0,20}(\d+(?:\.\d+)?)\b", t)
    if m2:
        try:
            v = float(m2.group(1))
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None
    # Metric / alternate unit fallbacks in free text.
    m3 = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:m|meter|meters|metre|metres)\b", t)
    if m3:
        return float(m3.group(1)) * 3.280839895
    m4 = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:cm|centimeter|centimeters|centimetre|centimetres)\b", t)
    if m4:
        return float(m4.group(1)) / 30.48
    m5 = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:mm|millimeter|millimeters|millimetre|millimetres)\b", t)
    if m5:
        return float(m5.group(1)) / 304.8
    m6 = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:yd|yds|yard|yards)\b", t)
    if m6:
        return float(m6.group(1)) * 3.0
    return None


def _spec_str_nonempty(val) -> bool:
    """True if listing spec string looks present (for ranking, not display)."""
    if val is None:
        return False
    s = str(val).strip()
    if not s:
        return False
    low = s.lower()
    if low in ("unknown", "n/a", "na", "-", "--", "tbd"):
        return False
    return True


def _infer_hitch_type_from_text(text: str) -> Optional[str]:
    """
    If user text clearly prefers one hitch type, return 'Gooseneck' or 'Bumper Pull'.
    If both or neither are indicated, return None. (Conservative: avoids wrong filters.)
    """
    if not (text or "").strip():
        return None
    t = text.lower()
    has_gooseneck = bool(
        re.search(
            r"\bgooseneck\b|goose[\s\-]+neck|gooseneck\s+hitch|"
            r"\bgn\s+(?:hitch|trailer)\b|with\s+a\s+gooseneck",
            t,
        )
    )
    has_bumper = bool(
        re.search(
            r"bumper\s*pull|bumper-pull|\btag[\s\-]*along\b|"
            r"\b(?:a\s+)?bumper\s+hitch\b|pull[-\s]?behind|tow\s+behind",
            t,
        )
    )
    if has_gooseneck and has_bumper:
        return None
    if has_gooseneck:
        return "Gooseneck"
    if has_bumper:
        return "Bumper Pull"
    return None


def _is_clear_light_cargo_text(text: str) -> bool:
    if not (text or "").strip():
        return False
    t = text.lower()
    patterns = (
        r"\bgolf[\s\-]*cart(?:s)?\b",
        r"\batv(?:s)?\b",
        r"\butv(?:s)?\b",
        r"\blawn(?:\s+equipment|\s+tools?)\b",
        r"\bgarden(?:ing)?\s+tools?\b",
        r"\bpush\s+mower(?:s)?\b",
        r"\bsmall\s+equipment\b",
        r"\blight\s+cargo\b",
    )
    return any(re.search(p, t) for p in patterns)


def _coerce_required_payload_lbs(val) -> Optional[float]:
    numeric = _coerce_positive_float(val)
    if numeric is not None:
        return numeric
    return _parse_lbs(val)


def _coerce_required_length_ft(val) -> Optional[float]:
    numeric = _coerce_positive_float(val)
    if numeric is not None:
        return numeric
    return _parse_length_ft(val)


def _metadata_to_listing(match: dict) -> TrailerListing:
    m = match["metadata"]
    price = _coerce_money_metadata(m.get("price"))
    raw_display = m.get("price_display")
    price_display = str(raw_display).strip() if raw_display is not None and str(raw_display).strip() else None
    if not price_display:
        price_display = f"${price:,.0f}" if price is not None else "Call for price"
    return TrailerListing(
        listing_id=m.get("listing_id", match["id"]),
        title=m.get("title", ""),
        condition=m.get("condition", ""),
        price=price,
        price_display=price_display,
        payments_from=m.get("payments_from"),
        category_subcategory=m.get("category_subcategory", ""),
        make=m.get("make", ""),
        color=m.get("color", ""),
        hitch_type=m.get("hitch_type"),
        year=m.get("year"),
        length=m.get("length"),
        width=m.get("width"),
        axles=m.get("axles"),
        gvwr=m.get("gvwr"),
        payload_capacity=m.get("payload_capacity"),
        trailer_material=m.get("trailer_material"),
        floor=m.get("floor"),
        url=m.get("url", ""),
        score=round(match.get("score", 0.0), 3),
    )


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_trailers",
        "description": (
            "Search the trailer inventory once required qualification slots are collected. "
            "Always provide a rich query string that captures haul item, weight, use case, "
            "and any subcategory details. Add filter fields only when the customer has "
            "clearly specified them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Detailed natural language description: include haul item, estimated weight, "
                        "trailer type, subcategory details (e.g. 'equipment trailer for skid steer "
                        "9500 lbs gooseneck'), and any feature tags from the customer."
                    ),
                },
                "condition": {
                    "type": "string",
                    "enum": ["New", "Pre-Owned"],
                    "description": "Trailer condition, only if customer specifies",
                },
                "price_min": {
                    "type": "number",
                    "description": "Minimum price in USD",
                },
                "price_max": {
                    "type": "number",
                    "description": "Maximum price in USD",
                },
                "category_subcategory": {
                    "type": "string",
                    "description": (
                        "Resolved trailer category. Use one of: Equipment, Car Hauler, Utility, "
                        "Dump, Tilt, Enclosed, Livestock, Roll Off, Diesel Tank, Flatbed, "
                        "Fiber, Race Trailer, Welding, Aluminum. "
                        "For aluminum modifier: resolve to base category first (e.g. Utility, Equipment). "
                        "Only set once category is confirmed from the conversation."
                    ),
                },
                "make": {
                    "type": "string",
                    "description": (
                        "Manufacturer brand — only if explicitly named. "
                        "Known brands: Iron Bull Trailers, Diamond C Trailers, Cargo Craft Trailers, "
                        "Aluma, East Texas Trailers, P&C, Galyean, Stallion, Calico Trailers, "
                        "Baseline, Star, Texas Pride, Alcom, Kaufman Trailers, AmeriTrail."
                    ),
                },
                "color": {
                    "type": "string",
                    "description": "Trailer color, only if customer specifies",
                },
                "hitch_type": {
                    "type": "string",
                    "enum": ["Bumper Pull", "Gooseneck"],
                    "description": (
                        "REQUIRED for Pinecone filtering whenever the customer clearly wants "
                        "a Bumper Pull or Gooseneck hitch (or common aliases: goose neck, tag along). "
                        "The word 'Gooseneck' in listing titles is often a brand, not a hitch: when the user "
                        "wants a gooseneck *hitch*, you MUST still set this to 'Gooseneck'. "
                        "Omit only if they express no hitch preference."
                    ),
                },
                "required_payload_lbs": {
                    "type": "number",
                    "description": (
                        "Required haul weight in lbs when the customer clearly provides it "
                        "(for fit-aware ranking). Omit if unknown."
                    ),
                },
                "required_length_ft": {
                    "type": "number",
                    "description": (
                        "Required haul/load length in feet when the customer clearly provides it "
                        "(for fit-aware ranking). Omit if unknown."
                    ),
                },
                "required_gvwr_lbs": {
                    "type": "number",
                    "description": (
                        "Minimum trailer GVWR in lbs when the customer clearly provides it "
                        "(for fit-aware filtering/ranking). Omit if unknown."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _public_site_url_for_prompt() -> str:
    w = (os.getenv("TRAILERPLACE_WEBSITE", "https://www.trailerplace.com") or "").strip()
    return w if w.startswith("http") else "https://www.trailerplace.com"


def _build_system_prompt() -> str:
    return ("""You are a friendly, knowledgeable trailer sales assistant for TrailerPlace, Wharton TX (979-532-1486). Your job is to help customers find the right trailer through a natural, conversational discovery process.

## CONVERSATION START
The customer's first message will contain their name, phone, and email. Extract their first name and greet them warmly by name. Then ask what they're looking for.

## INTENT ROUTING
Identify the customer's intent before assuming they want a trailer search:
- **financing** → "Let me connect you with our finance team — give us a call at 979-532-1486 or stop by in store. I can also help narrow down the trailer type first if that's helpful."
- **trade_in** → "Our sales team handles trade-in appraisals — give us a call at 979-532-1486. If you have the year, make, and model of your current trailer that'll help them out."
- **service / parts** → "Our service and parts team can help with that — reach them at 979-532-1486."
- **store_info** → "We're located in Wharton, TX. You can reach us at 979-532-1486. We offer financing and delivery."
- **human_handoff** → "Absolutely — you can reach our team directly at 979-532-1486. Happy to keep helping here too if you'd like."

## CATEGORY IDENTIFICATION
Map what the customer says to a category. Critical disambiguation rules:
- "aluminum" / "lightweight" / "won't rust" / "Aluma" → MODIFIER, not a category. Ask: "What type of trailer are you wanting in aluminum — utility, equipment, enclosed, or something else?"
- "toy hauler" → uncertain. Ask: "Will you be hauling a vehicle on an open deck, or are you looking for a camper-style toy hauler?"
- "office trailer" / "cooldown trailer" / "job site trailer" → Ask: "Will this be for fiber / telecom work specifically, or a more general office or cooldown trailer?"
- "lowboy" / "low profile" → Equipment
- "box trailer" / "cargo trailer" / "V-nose" → Enclosed
- "skid steer" / "mini ex" / "mini excavator" / "tractor" → Equipment
- "landscape trailer" / "lawnmower trailer" / "ATV trailer" → Utility
- "splicing trailer" / "fiber optic trailer" → Fiber (specialty Enclosed)
- "enclosed car hauler" → Race Trailer
- "dumpster trailer" / "roll-off" → Roll Off
- "fuel tank" / "tank trailer" → Diesel Tank
- "hotshot" / "step deck" / "platform trailer" / "non-CDL" → Flatbed
- "Galyean" / "Star trailer" → Livestock (cattle)
- "Calico trailer" → Livestock (goats / hogs)

## QUALIFICATION — COLLECT THESE SLOTS BEFORE CALLING search_trailers
Ask ONE question at a time, in order. Stop collecting once you have the required slots.

## CRITICAL WEIGHT QUESTION EXCEPTION — READ THIS BEFORE ASKING ANY WEIGHT QUESTION

1.Before asking `haul_weight_lbs` for ANY category, run this check first:

2.**If the haul item is inherently lightweight, DO NOT ask about weight. Ever. Not even to confirm.**
Instead, silently set `required_payload_lbs = 1000` in the search tool call and move on.

3.This is a silent internal rule only. The customer must never hear any reference to weight, skipping, or assumptions — not even a passing mention.

4.Do NOT produce any sentence about weight for these items. Just ask the next question directly.

Items that are ALWAYS considered lightweight (never ask weight for these):
- Golf carts, golf equipment
- ATVs, UTVs, side-by-sides (single unit)
- Dirt bikes, motorcycles (single unit)
- Lawn mowers, zero-turns, riding mowers
- Gardening tools, landscaping hand tools
- Small generators (portable / inverter type)
- Canoes, kayaks, small watercraft (non-motorized)
- Bicycles, e-bikes
- Small furniture or household items
- Light hobby equipment (RC vehicles, camping gear)

The rule: if a reasonable person would NOT need a scale to know it's under 1,500 lbs, it's lightweight. Skip the weight question and proceed.
**Only ask weight when the item is ambiguously heavy** — e.g. excavators, tractors, large boats, vehicles, construction materials, bulk landscaping material (gravel, mulch, soil), livestock. When in doubt on genuinely heavy items, ask once.

## OPTIONAL SLOT PRE-CHECK — READ BEFORE ASKING ANY OPTIONAL QUESTION

Before asking any optional question from the per-category slot lists, scan everything
the customer has already said in this conversation. If they have already provided
information that answers an optional slot — even indirectly or in passing — treat that
slot as filled and do NOT ask about it.

Examples of what counts as already answered:
- Customer said "I have a gooseneck hitch" → hitch preference is filled, do not ask
- Customer said "I need ramps to load it" → loading style is filled, do not ask
- Customer said "it needs to be enclosed, I want AC in it" → AC is filled, do not ask
- Customer said "I need a 20-foot trailer" → length preference is filled, do not ask
- Customer said "I want side rails and a rear gate" → sides/gate is filled, do not ask

This is a silent internal rule only. Never acknowledge to the customer
that a slot was already answered or is being skipped. Simply ask the
next unanswered question as if it were the natural continuation.

Apply this pre-check to BOTH required and optional slots. Only ask about slots
the customer has genuinely left unanswered.


**Equipment:** haul_item → haul_weight_lbs(only ask if haul item is not light items etc.) → haul_length_ft· optional: hitch preference, loading style (ramps / deckover / drive-over fenders)
**Car Hauler:** vehicle_type → haul_weight_lbs(only ask if haul item is not light items etc.) → vehicle_length_ft · optional: open vs. covered
**Utility:** haul_item → haul_weight_lbs(only ask if haul item is not light items etc.) · optional: size, sides / gate / tool storage
**Dump:** haul_material → haul_weight_lbs(only ask if haul item is not light items etc.) · optional: dump mechanism (scissor / telescopic / standard)
**Tilt:** haul_item → haul_weight_lbs(only ask if haul item is not light items etc.) · optional: full tilt vs. stationary front deck
**Enclosed:** use_case → cargo_size · optional: AC / windows / cabinets / finished interior
**Livestock:** trailer_length_ft · optional: gate preferences (butterfly / swing / slant)(Do not ask cattle type or count, just ask about the length of the trailer)
**Roll Off:** package_scope (trailer / bins / both) → bin_size · optional: step deck vs. standard, CDL concern
**Diesel Tank:** fuel_type (diesel / gasoline) → tank_capacity · optional: step deck vs. standard, CDL concern
**Flatbed:** haul_item → haul_weight_lbs(only ask if haul item is not light items etc.) · optional: step deck vs. standard, CDL concern
**Fiber:** use_case (splicing / office / cooldown) · optional: crew size, AC / workbench / generator
**Race Trailer:** vehicle_type → trailer_length_ft · optional: cabinets / workspace / living quarters
**Welding:** equipment_list · optional: total weight
**Aluminum (modifier):** resolve base_category first → payload_need · optional: sleeping need
**Offroad:** use_case (camping / overlanding / gear hauling) · optional: sleeping need

Recovery rules — when a customer doesn't know a slot:
- Size unknown → "Even a rough estimate helps — about how long is it?"

## SEARCHING & RECOMMENDATIONS
Once required slots are filled, call search_trailers immediately with a rich query. After results come back:
- Open with a short recap of what they asked for (category, haul, rough weight etc.), then introduce the options.
- **Hitch type and Pinecone:** The vector database filters on structured fields. If the customer clearly wants a **Bumper Pull** or **Gooseneck** hitch (or phrasing like *gooseneck hitch*, *goose neck*, *bumper pull*, *tag along*), you MUST set tool arg **`hitch_type`** to **`"Bumper Pull"`** or **`"Gooseneck"`** — the free-text `query` alone is not enough. **Brand vs hitch:** the word *Gooseneck* in a model name (e.g. a manufacturer) is not a hitch; when the customer is asking for a *gooseneck hitch* / *gooseneck* in a hitch context, they mean **`hitch_type: "Gooseneck"`**.
- If customer clearly states the load weight, load length, or a minimum trailer GVWR, you MUST pass them in tool args (`required_payload_lbs`, `required_length_ft`, `required_gvwr_lbs`) as numeric values so recommendation ranking can prefer right-sized trailers.
- Normalize units before tool args: convert any weight units (tons, kg, etc.) to **lbs** for `required_payload_lbs` and `required_gvwr_lbs`, and convert any length units (m/cm/mm/yd/in) to **feet** for `required_length_ft`.
- When you present **multiple** trailers, number them **`1.`**, **`2.`**, **`3.`** (etc.) in the order you want them read. For a **single** trailer, you may omit the number.
- For **each** trailer, use this **fixed layout** — do not skip or reorder steps **2** and **3**:
  1) **Title line** — Markdown link: **`[Full listing title exactly as returned — includes stock after the dash](that row's listing URL from search JSON)`**. Use the **per-listing URL** from the tool for that trailer (not the generic dealership site URL unless it is the same field).
  2) **Structured highlight block** — **at most 6** lines. Each line starts with **`•`** then **`Label: value`** (e.g. `•Price: $11,250`). Hard cap: **never more than six** bullet rows per trailer. Pull values **only** from the search tool / JSON for that row. **Omit** missing, null, empty, or "Unknown" fields entirely. Pick up to six labels that matter most for *this* customer (e.g. Price, Length, GVWR, Payload Capacity, Hitch Type, Color). **Do not** list low-value or redundant pairs (e.g. skip **Material** if **Floor** already conveys construction, or vice versa, unless both are clearly distinct and useful). Use sensible units (lbs, ft) when shown in the data.
  3) **Why-it-fits (required)** — immediately after the bullet block, a **blank line**, then **one or two sentences** (never zero; never more than two) in plain language tying **that** trailer to what the customer said (haul, weight, length, budget, hitch, tow vehicle, etc.). Every sentence must be grounded in the same search fields — no invented specs. This paragraph is **mandatory** for every trailer you list; ending right after the bullet block without this explanation is **not allowed**.
- **Example — copy this shape** (swap in real title, real listing URL, real fields, real customer tie-in; do not copy these numbers unless they appear in live search results):

```
1. [2026 Iron Bull Trailers Dtb 15K Bp Dump - 07595](LISTING_URL_FROM_SEARCH_TOOL_FOR_THIS_ROW)

•Price: $11,250
•Length: 14 ft
•GVWR: 14,900 lbs
•Payload Capacity: 9,135 lbs
•Hitch Type: Bumper Pull
•Color: Tan

This trailer is an excellent match for your needs, providing a significant payload capacity that exceeds what you plan to haul. The bumper pull hitch allows for easy towing.
```

- **Do not** add lines like "View this trailer", "Click here", or paste the raw URL again on its own line — the hyperlinked title plus card citation rules below are enough.
- Only add extra options beyond your top pick if they are genuinely very close in price, size, and use case to the top result.
- Never list more than MAX_RECOMMENDATIONS_PLACEHOLDER.
- If the tool returns N recommendations, present all N in the same order; do not arbitrarily drop items from the textual list.
- When you describe inventory from search results, cite each trailer you want the customer to see as a card: include its **stock number** (the digits after the dash in the title, e.g. 46556) or a **short exact phrase from the title**, or the listing **URL** — otherwise matching cards may be hidden.
- Use this framing: "Based on what you described, you're likely looking for a [category]. Depending on [key factor], you may be in the [size / capacity range]."
- If results aren't a perfect match, say so honestly and show the closest available option.
- Never invent specs — only reference what's in the search results.
- Always mention that financing and delivery are available.
- Use the customer's first name naturally throughout.
- After you show recommendations, end with **exactly one** warm closing question — **vary the wording** each time. Draw inspiration from (do not list all at once): "Does any of these suit what you're looking for?" · "Are you interested in moving forward with any of these trailers?" · "Is there a unit you'd like our team to follow up on?" · "Do any of these feel like the right fit for your haul?" · "Would you like more detail on any of these listings?" · "Does one of these stand out for you?" · "Want to narrow it down, or does one already look promising?" · "Are any of these close enough to talk next steps?" · "Should I help you compare two of these?" · "Do these hit the mark, or should we keep looking?" · "Which way are you leaning?" — pick **one** question only; do not stack several closings in the same reply.

## INTEREST & NEXT STEPS (after you showed inventory — you handle this in natural language)
- If the customer clearly picks **one** specific listing (stock number after the dash in the title, first/second/third matching the order you listed, a recognizable **partial title**, color when it uniquely identifies one unit, etc.), confirm that their interest has been **logged in the system**, that **they will get a response from the team soon**, and invite them to browse the full site in the meantime: **TRAILERPLACE_WEB_PLACEHOLDER**. Do not claim someone already called them.
- If they sound interested (yes, sounds good, I want one, etc.) but **do not** say which trailer, ask **one** short follow-up: which model they mean — they may answer with **stock digits**, **first/second/third**, **full or partial listing title**, or other hints you can match to the last results you discussed.
- Never invent that a human already contacted them; "logged / team will follow up soon" is appropriate.

## OBJECTION HANDLING
- "don't know what size" → "No problem — if you tell me what you're hauling and about how much it weighs, I can usually narrow the size down pretty quickly."
- "too expensive" → "Understood. We can usually narrow things down by budget, size, and how often you'll use it so you're not buying more trailer than you need. Do you have a budget range in mind?"
- "only need it once in a while" → "In that case it often makes sense to focus on the simplest trailer that safely fits what you're hauling. What are you hauling, and how heavy is it?"
- "want the lightest trailer" → "Aluminum may be worth looking at if lightweight and corrosion resistance are priorities. What type of trailer are you wanting in aluminum?"
- "never bought one before" → "No problem at all — that's exactly what I can help with. We can keep it simple and start with what you're hauling."

## SAFETY GUARDRAILS — never state these as confirmed facts
- **Payload / GVWR fit:** "Final payload fit should be confirmed from the actual trailer specs."
- **CDL thresholds:** "I can help point you in the right direction, but final CDL and legal compliance should be confirmed for your full setup and location."
- **Brake requirements:** "Brake requirements can vary — final requirements should be confirmed for your location and setup."
- **Fuel transport compliance:** "Compliance for fuel transport should be confirmed based on your use case and local requirements."
- **Live inventory:** "I can show likely matches, and a team member can confirm current availability."
- **Final pricing:** "I can help with a starting point, and our team can confirm exact pricing and options — call us at 979-532-1486."

## HANDOFF TRIGGERS — offer to connect to a person when:
- Customer asks for a person / sales rep → "You can reach our team at 979-532-1486. Anything else I can help with in the meantime?"
- Customer wants an exact out-the-door quote → use pricing guardrail + "Give us a call at 979-532-1486 for exact pricing."
- Customer wants live inventory confirmation → "A team member can confirm current availability — call 979-532-1486."
- Customer is frustrated or conversation is looping → "I'm sorry this isn't clicking — let me get you connected with our team directly at 979-532-1486."
- Compliance question (CDL, towing law, fuel transport rules) → use the relevant guardrail + suggest calling.

## JARGON — explain simply when a customer seems unfamiliar with a term
- bumper pull: hooks to a standard receiver hitch behind the truck
- gooseneck: connects in the bed of the truck — more stability and higher capacity than bumper pull
- deckover: deck sits above the wheels, giving you full deck width for wider loads
- GVWR: the maximum total loaded weight the trailer is rated for
- payload: how much cargo the trailer can carry (GVWR minus the trailer's own weight)
- dovetail: a sloped rear section that makes loading easier, often paired with ramps
- V-nose: angled front on an enclosed trailer — helps with aerodynamics and interior storage space
- non-CDL: customers usually mean staying under certain weight thresholds — always use the CDL guardrail language
""").replace("MAX_RECOMMENDATIONS_PLACEHOLDER", str(SEARCH_MAX_RECOMMENDATIONS)).replace(
        "TRAILERPLACE_WEB_PLACEHOLDER",
        _public_site_url_for_prompt(),
    )


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class TrailerAgent:
    def __init__(self):
        self.openai = OpenAI(api_key=OPENAI_API_KEY)
        self.pc_index = Pinecone(api_key=PINECONE_API_KEY).Index(INDEX_NAME)
        self._history: list[dict] = [
            {"role": "system", "content": _build_system_prompt()}
        ]

    def _embed(self, text: str) -> list[float]:
        resp = self.openai.embeddings.create(model=EMBEDDING_MODEL, input=[text])
        return resp.data[0].embedding

    def _search(
        self, query: str, trailer_filter: TrailerFilter, top_k: int = 5
    ) -> tuple[list[TrailerListing], list[dict[str, Any]]]:
        vector = self._embed(query)
        pf = _build_pinecone_filter(trailer_filter)
        search_attempts: list[dict[str, Any]] = []

        def _one_query(pinecone_filter: Optional[dict], relaxed: bool) -> list[dict]:
            logger.info(
                "PINECONE_FILTER | phase=%s | top_k=%s | filter=%s",
                "relaxed" if relaxed else "strict",
                top_k,
                json.dumps(pinecone_filter, ensure_ascii=True) if pinecone_filter else "None",
            )
            r = self.pc_index.query(
                vector=vector,
                top_k=top_k,
                include_metadata=True,
                filter=pinecone_filter,
            )
            mlist = r.get("matches", [])
            attempt = {
                "phase": "relaxed" if relaxed else "strict",
                "query": query,
                "top_k": top_k,
                "pinecone_filter": pinecone_filter,
                "match_count": len(mlist),
                "matches": [_match_summary_for_log(x) for x in mlist],
            }
            search_attempts.append(attempt)
            _log_product_fetch_event(attempt)
            self._log_matches(
                mlist, query=query, pinecone_filter=pinecone_filter, relaxed=relaxed
            )
            return mlist

        matches = _one_query(pf, False)
        if not matches and pf:
            relaxed_f = {
                k: v for k, v in (pf or {}).items()
                if k in ("condition", "price", "hitch_type")
            }
            f_arg = relaxed_f if relaxed_f else None
            matches = _one_query(f_arg, True)

        deduped_matches, dedupe_debug = _dedupe_matches(matches)
        if dedupe_debug.get("dropped_count", 0) > 0:
            logger.info(
                "MATCH_DEDUPE | before=%s | after=%s | dropped=%s",
                dedupe_debug["before_count"],
                dedupe_debug["after_count"],
                dedupe_debug["dropped_count"],
            )
        search_attempts.append({"dedupe": dedupe_debug})
        return [_metadata_to_listing(m) for m in deduped_matches], search_attempts

    @staticmethod
    def _log_matches(matches: list[dict], query: str, pinecone_filter: Optional[dict], relaxed: bool) -> None:
        mode = "relaxed" if relaxed else "strict"
        logger.info(
            "Pinecone %s search results fetched | query=%s | filter=%s | match_count=%s",
            mode,
            query,
            json.dumps(pinecone_filter, ensure_ascii=True) if pinecone_filter else "None",
            len(matches),
        )

        for idx, match in enumerate(matches, 1):
            metadata = match.get("metadata", {}) or {}
            logger.info(
                "Match #%s | id=%s | score=%.6f",
                idx,
                match.get("id"),
                float(match.get("score", 0.0)),
            )
            for key in sorted(metadata.keys()):
                logger.info("  %s: %s", key, metadata.get(key))

    @staticmethod
    def _rerank_by_fit(
        listings: list[TrailerListing],
        *,
        required_payload_lbs: Optional[float],
        required_length_ft: Optional[float],
        required_gvwr_lbs: Optional[float],
        desired_count: int,
        warn_ratio: float = RERANK_WARN_RATIO,
        extreme_ratio: float = RERANK_EXTREME_RATIO,
    ) -> tuple[list[TrailerListing], dict[str, Any]]:
        needs_present = (
            required_payload_lbs is not None
            or required_length_ft is not None
            or required_gvwr_lbs is not None
        )
        required_dim_count = (
            int(required_payload_lbs is not None)
            + int(required_length_ft is not None)
            + int(required_gvwr_lbs is not None)
        )
        if not listings or not needs_present:
            return listings, {
                "applied": False,
                "reason": "missing_clear_requirements_or_no_listings",
                "required_payload_lbs": required_payload_lbs,
                "required_length_ft": required_length_ft,
                "required_gvwr_lbs": required_gvwr_lbs,
            }

        def _emit_rerank_score_logs(
            *,
            phase_name: str,
            all_entries: list[dict[str, Any]],
            ranked_entries: list[dict[str, Any]],
        ) -> None:
            """
            Emit one structured score line per fetched listing so reranking decisions
            are auditable from logs.
            """
            decision_rank_by_entry = {id(e): i for i, e in enumerate(ranked_entries, 1)}
            for fetched_pos, e in enumerate(all_entries, 1):
                lst = e["listing"]
                decision_rank = decision_rank_by_entry.get(id(e))
                logger.info(
                    "RERANK_SCORE | phase=%s | fetched_pos=%s | decision_rank=%s | listing_id=%s | title=%s | base_score=%.6f | penalty=%.6f | fit_score=%.6f | payload_ratio=%s | length_ratio=%s | gvwr_ratio=%s | fail_count=%s | missing_count=%s | has_all_required_dims=%s | payload_spec_tier=%s | length_exact_rank=%s | gvwr_spec_tier=%s",
                    phase_name,
                    fetched_pos,
                    decision_rank if decision_rank is not None else "None",
                    lst.listing_id,
                    lst.title,
                    float(e["base_score"]),
                    float(e["penalty"]),
                    float(e["fit_score"]),
                    e["payload_ratio"],
                    e["length_ratio"],
                    e["gvwr_ratio"],
                    e["fail_count"],
                    e["missing_count"],
                    e["has_all_required_dims"],
                    e.get("payload_spec_tier"),
                    e.get("length_exact_rank"),
                    e.get("gvwr_spec_tier"),
                )

        def _entry_for_debug(e: dict[str, Any], decision_rank: Optional[int]) -> dict[str, Any]:
            return {
                "decision_rank": decision_rank,
                "listing_id": e["listing"].listing_id,
                "title": e["listing"].title,
                "base_score": round(e["base_score"], 6),
                "fit_score": round(e["fit_score"], 6),
                "penalty": e["penalty"],
                "payload_ratio": e["payload_ratio"],
                "length_ratio": e["length_ratio"],
                "gvwr_ratio": e["gvwr_ratio"],
                "payload_from": e["payload_from"],
                "fail_count": e["fail_count"],
                "missing_count": e["missing_count"],
                "has_all_required_dims": e["has_all_required_dims"],
                "payload_spec_tier": e.get("payload_spec_tier"),
                "length_exact_rank": e.get("length_exact_rank"),
                "gvwr_spec_tier": e.get("gvwr_spec_tier"),
            }

        def _build_debug_payload(
            *, phase_name: str, all_entries: list[dict[str, Any]], ranked_entries: list[dict[str, Any]]
        ) -> dict[str, Any]:
            decision_rank_by_entry = {id(e): i for i, e in enumerate(ranked_entries, 1)}
            ranked_preview = [
                _entry_for_debug(e, decision_rank_by_entry.get(id(e)))
                for e in ranked_entries[: min(8, len(ranked_entries))]
            ]
            all_scores = [
                _entry_for_debug(e, decision_rank_by_entry.get(id(e)))
                for e in all_entries
            ]
            return {
                "applied": True,
                "phase": phase_name,
                "required_payload_lbs": required_payload_lbs,
                "required_length_ft": required_length_ft,
                "required_gvwr_lbs": required_gvwr_lbs,
                "warn_ratio": warn_ratio,
                "extreme_ratio": extreme_ratio,
                "ranked_fit_summary": ranked_preview,
                "all_scores": all_scores,
            }

        def _length_overage_for_sort(e: dict[str, Any]) -> float:
            if required_length_ft is None:
                return 0.0
            lr = e.get("length_ratio")
            if lr is None:
                return 999.0
            return max(0.0, float(lr) - 1.0)

        def _merged_fit_sort_key(e: dict[str, Any]) -> tuple[Any, ...]:
            # 1) Fewest missing required dimensions, 2) better payload spec source,
            # 3) length exact as tie-break (not a global prefix), 4) GVWR present,
            # 5) legacy tie-breakers.
            return (
                e["missing_count"],
                int(e.get("payload_spec_tier", 0)),
                int(e.get("length_exact_rank", 0)),
                int(e.get("gvwr_spec_tier", 0)),
                not e["has_all_required_dims"],
                round(_length_overage_for_sort(e), 6),
                e["penalty"],
                -e["base_score"],
            )

        entries: list[dict[str, Any]] = []
        for lst in listings:
            payload = _parse_lbs(lst.payload_capacity)
            payload_from = "payload_capacity"
            if payload is None:
                payload = _parse_lbs(lst.gvwr)
                payload_from = "gvwr" if payload is not None else "unknown"
            length_ft = _parse_length_ft(lst.length)
            gvwr_lbs = _parse_lbs(lst.gvwr)

            payload_ratio = (
                (payload / required_payload_lbs)
                if (required_payload_lbs is not None and payload is not None and required_payload_lbs > 0)
                else None
            )
            length_ratio = (
                (length_ft / required_length_ft)
                if (required_length_ft is not None and length_ft is not None and required_length_ft > 0)
                else None
            )
            gvwr_ratio = (
                (gvwr_lbs / required_gvwr_lbs)
                if (required_gvwr_lbs is not None and gvwr_lbs is not None and required_gvwr_lbs > 0)
                else None
            )

            fail_count = 0
            missing_count = 0
            penalty = 0.0
            max_ratio = 1.0
            used_dims = 0

            dims = [
                ("payload", payload_ratio, required_payload_lbs is not None),
                ("length", length_ratio, required_length_ft is not None),
                ("gvwr", gvwr_ratio, required_gvwr_lbs is not None),
            ]
            for dim_name, ratio, required in dims:
                if not required:
                    continue
                if ratio is None:
                    missing_count += 1
                    penalty += 0.35
                    continue
                used_dims += 1
                max_ratio = max(max_ratio, ratio)
                if ratio < 1.0:
                    fail_count += 1
                    penalty += (1.0 - ratio) * 6.0
                    continue
                over = ratio - 1.0
                if over > 0:
                    penalty += over * 1.4
                if ratio > warn_ratio:
                    penalty += (ratio - warn_ratio) * 2.4
                if ratio > extreme_ratio:
                    penalty += (ratio - extreme_ratio) * 4.8

                if dim_name == "payload" and payload_from == "gvwr":
                    # Use GVWR only as fallback signal when payload is missing.
                    penalty += 0.2

            if required_payload_lbs is None:
                payload_spec_tier = 0
            elif payload_ratio is None:
                payload_spec_tier = 2
            elif payload_from == "payload_capacity" and _spec_str_nonempty(lst.payload_capacity):
                payload_spec_tier = 0
            else:
                payload_spec_tier = 1

            if required_length_ft is None:
                length_exact_rank = 0
            elif length_ratio is None:
                length_exact_rank = 2
            elif abs(float(length_ratio) - 1.0) <= 1e-6:
                length_exact_rank = 0
            else:
                length_exact_rank = 1

            if required_gvwr_lbs is None:
                gvwr_spec_tier = 0
            else:
                gvwr_spec_tier = 0 if gvwr_ratio is not None else 1

            base_score = float(lst.score or 0.0)
            entries.append(
                {
                    "listing": lst,
                    "base_score": base_score,
                    "fit_score": base_score - penalty,
                    "penalty": round(penalty, 6),
                    "fail_count": fail_count,
                    "missing_count": missing_count,
                    "used_dims": used_dims,
                    "has_all_required_dims": bool(
                        required_dim_count > 0 and used_dims >= required_dim_count
                    ),
                    "max_ratio": round(max_ratio, 6),
                    "payload_ratio": None if payload_ratio is None else round(payload_ratio, 6),
                    "length_ratio": None if length_ratio is None else round(length_ratio, 6),
                    "gvwr_ratio": None if gvwr_ratio is None else round(gvwr_ratio, 6),
                    "payload_from": payload_from,
                    "payload_spec_tier": payload_spec_tier,
                    "length_exact_rank": length_exact_rank,
                    "gvwr_spec_tier": gvwr_spec_tier,
                }
            )

        # When the customer required a length, we previously prefixed all
        # exact-length matches ahead of the rest, which could rank
        # "length exact + missing payload" above "complete payload + not exact length".
        # Merged key sorts all non-failing results once: complete specs first, then
        # length exact as a tie-break (length_exact_rank), not a hard prefix.
        if required_length_ft is not None:
            nonfailing = [
                e
                for e in entries
                if e["fail_count"] == 0 and e["used_dims"] > 0
            ]
            if nonfailing:
                ranked_entries = sorted(nonfailing, key=_merged_fit_sort_key)
                phase = "phase_nonfailing_merged_fit"
                _emit_rerank_score_logs(
                    phase_name=phase,
                    all_entries=entries,
                    ranked_entries=ranked_entries,
                )
                ranked_listings = [e["listing"] for e in ranked_entries]
                debug = _build_debug_payload(
                    phase_name=phase, all_entries=entries, ranked_entries=ranked_entries
                )
                return ranked_listings, debug

        phase_a = [
            e for e in entries
            if e["fail_count"] == 0 and e["used_dims"] > 0 and e["max_ratio"] <= extreme_ratio
        ]
        if phase_a:
            ranked_phase_a = sorted(phase_a, key=_merged_fit_sort_key)
            phase = "phase_a_non_extreme_fit"
            if len(ranked_phase_a) >= max(1, desired_count):
                ranked_entries = ranked_phase_a
            else:
                phase_b_fill = sorted(
                    [
                        e for e in entries
                        if e not in ranked_phase_a and e["fail_count"] == 0 and e["used_dims"] > 0
                    ],
                    key=_merged_fit_sort_key,
                )
                # Fill to desired count with closest oversized options if needed.
                ranked_entries = ranked_phase_a + phase_b_fill
                phase = "phase_a_with_phase_b_fill"
        else:
            phase_b = [
                e for e in entries
                if e["fail_count"] == 0 and e["used_dims"] > 0
            ]
            if phase_b:
                ranked_entries = sorted(phase_b, key=_merged_fit_sort_key)
                phase = "phase_b_fallback_oversized"
            else:
                # Last resort: keep recommendation flow alive while heavily preferring closest fit.
                ranked_entries = sorted(
                    entries,
                    key=_merged_fit_sort_key,
                )
                phase = "phase_b_last_resort_no_clear_fit"

        _emit_rerank_score_logs(
            phase_name=phase,
            all_entries=entries,
            ranked_entries=ranked_entries,
        )
        ranked_listings = [e["listing"] for e in ranked_entries]
        debug = _build_debug_payload(
            phase_name=phase, all_entries=entries, ranked_entries=ranked_entries
        )
        return ranked_listings, debug

    def _execute_tool_call(
        self, tool_call,
    ) -> tuple[str, list[TrailerListing], dict[str, Any], list[dict[str, Any]] | None]:
        args = json.loads(tool_call.function.arguments)
        query = args.pop("query")
        required_payload_lbs = _coerce_required_payload_lbs(args.pop("required_payload_lbs", None))
        required_length_ft = _coerce_required_length_ft(args.pop("required_length_ft", None))
        required_gvwr_lbs = _coerce_required_payload_lbs(args.pop("required_gvwr_lbs", None))
        requirements_source = "tool_args"

        hitch_type: Optional[str] = args.pop("hitch_type", None)
        hitch_type_source: Optional[str] = "tool_args" if hitch_type else None
        if not hitch_type:
            inferred_h = _infer_hitch_type_from_text(query)
            if inferred_h:
                hitch_type = inferred_h
                hitch_type_source = "inferred_from_query"
        if not hitch_type:
            for msg in reversed(self._history):
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                inferred_h = _infer_hitch_type_from_text(str(msg.get("content") or ""))
                if inferred_h:
                    hitch_type = inferred_h
                    hitch_type_source = "inferred_from_history"
                    break
        if hitch_type:
            normalized_h = normalize_hitch(hitch_type)
            if normalized_h:
                hitch_type = normalized_h
            if hitch_type_source and hitch_type_source != "tool_args":
                logger.info(
                    "HITCH_INFERRED | hitch_type=%s | source=%s",
                    hitch_type,
                    hitch_type_source,
                )

        # Fallback: if model omitted explicit need fields, infer from query/user history.
        if required_payload_lbs is None or required_length_ft is None:
            inferred_payload = _extract_weight_lbs_from_text(query)
            inferred_length = _extract_length_ft_from_text(query)
            if required_payload_lbs is None and inferred_payload is not None:
                required_payload_lbs = inferred_payload
                requirements_source = "inferred_from_query"
            if required_length_ft is None and inferred_length is not None:
                required_length_ft = inferred_length
                requirements_source = "inferred_from_query"

        if required_payload_lbs is None or required_length_ft is None:
            # Search recent user messages backwards; last clear value wins.
            for msg in reversed(self._history):
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") != "user":
                    continue
                content = str(msg.get("content") or "")
                if required_payload_lbs is None:
                    p = _extract_weight_lbs_from_text(content)
                    if p is not None:
                        required_payload_lbs = p
                        requirements_source = "inferred_from_history"
                if required_length_ft is None:
                    l = _extract_length_ft_from_text(content)
                    if l is not None:
                        required_length_ft = l
                        requirements_source = "inferred_from_history"
                if required_payload_lbs is not None and required_length_ft is not None:
                    break

        light_cargo_detected = _is_clear_light_cargo_text(query)
        if not light_cargo_detected:
            for msg in reversed(self._history):
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                if _is_clear_light_cargo_text(str(msg.get("content") or "")):
                    light_cargo_detected = True
                    break
        if required_payload_lbs is None and light_cargo_detected:
            required_payload_lbs = 1000.0
            requirements_source = "light_cargo_default"

        trailer_filter = TrailerFilter(
            condition=args.get("condition"),
            price_min=args.get("price_min"),
            price_max=args.get("price_max"),
            category_subcategory=args.get("category_subcategory"),
            make=args.get("make"),
            color=args.get("color"),
            hitch_type=hitch_type,
            required_length_ft=required_length_ft,
            required_gvwr_lbs=required_gvwr_lbs,
        )

        query_for_search = query
        normalized_fragments: list[str] = []
        ql = query.lower()
        if required_payload_lbs is not None and not re.search(r"\b(lbs?|pounds?|kg|kgs|tons?|tonnes?)\b", ql):
            normalized_fragments.append(f"{round(required_payload_lbs)} lbs")
        if required_length_ft is not None and not re.search(
            r"\b(ft|feet|inch|inches|in|m|meter|meters|cm|mm|yd|yard|yards)\b",
            ql,
        ):
            normalized_fragments.append(f"{round(required_length_ft, 2)} ft")
        if required_gvwr_lbs is not None and not re.search(r"\bgvwr\b", ql):
            normalized_fragments.append(f"minimum gvwr {round(required_gvwr_lbs)} lbs")
        if normalized_fragments:
            query_for_search = f"{query} | normalized_requirements: {' '.join(normalized_fragments)}"

        listings, search_attempts = self._search(query_for_search, trailer_filter, top_k=SEARCH_TOP_K)
        logger.info(
            "RERANK_INPUT | payload_lbs=%s | length_ft=%s | gvwr_lbs=%s | source=%s",
            required_payload_lbs,
            required_length_ft,
            required_gvwr_lbs,
            requirements_source,
        )
        reranked, rerank_debug = self._rerank_by_fit(
            listings,
            required_payload_lbs=required_payload_lbs,
            required_length_ft=required_length_ft,
            required_gvwr_lbs=required_gvwr_lbs,
            desired_count=SEARCH_MAX_RECOMMENDATIONS,
        )
        logger.info(
            "RERANK_PHASE | applied=%s | phase=%s | top_count=%s",
            rerank_debug.get("applied"),
            rerank_debug.get("phase"),
            len(reranked),
        )
        selected = self._pick_listings(reranked, max_count=SEARCH_MAX_RECOMMENDATIONS)
        tfilter: dict[str, Any]
        if hasattr(trailer_filter, "model_dump"):
            tfilter = trailer_filter.model_dump(exclude_none=True)  # type: ignore[union-attr]
        else:
            tfilter = {k: v for k, v in trailer_filter.dict().items() if v is not None}  # type: ignore[call-arg]

        result_for_db: list[dict[str, Any]] | None
        if not selected:
            tool_result = "No trailers found matching those criteria."
            result_for_db = []
        else:
            result_dicts = []
            for i, lst in enumerate(selected, 1):
                d = {
                    "rank": i,
                    "title": lst.title,
                    "condition": lst.condition,
                    "price": lst.price_display
                    or (f"${lst.price:,.0f}" if lst.price is not None else "Call for price"),
                    "category": lst.category_subcategory,
                    "make": lst.make,
                    "color": lst.color,
                    "hitch_type": lst.hitch_type,
                    "year": lst.year,
                    "length": lst.length,
                    "width": lst.width,
                    "axles": lst.axles,
                    "gvwr": lst.gvwr,
                    "payload_capacity": lst.payload_capacity,
                    "material": lst.trailer_material,
                    "floor": lst.floor,
                    "url": lst.url,
                    "relevance_score": lst.score,
                }
                result_dicts.append(d)
            tool_result = json.dumps(result_dicts, indent=2)
            result_for_db = result_dicts

        tool_debug: dict[str, Any] = {
            "query": query,
            "query_for_search": query_for_search,
            "trailer_filter": tfilter,
            "hitch_type": hitch_type,
            "hitch_type_source": hitch_type_source,
            "required_payload_lbs": required_payload_lbs,
            "required_length_ft": required_length_ft,
            "required_gvwr_lbs": required_gvwr_lbs,
            "requirements_source": requirements_source,
            "search_attempts": search_attempts,
            "rerank": rerank_debug,
            "recommendation_payload": result_for_db,
        }
        return tool_result, selected, tool_debug, result_for_db

    @staticmethod
    def _pick_listings(
        listings: list[TrailerListing],
        threshold: float = 0.04,
        max_count: int = 3,
    ) -> list[TrailerListing]:
        """
        Return up to `max_count` listings in ranked order.
        (No close-score pruning; caller already controls ranking quality.)
        """
        _ = threshold
        if not listings:
            return []
        return listings[: max(1, max_count)]

    def _thinking_conversation_window(self, max_turns: int = THINKING_WINDOW_TURNS) -> list[dict[str, str]]:
        """
        Build a compact conversation window for explainer input.
        Includes user/assistant messages from recent turns.
        """
        compact: list[dict[str, str]] = []
        for msg in self._history:
            if isinstance(msg, dict):
                role = str(msg.get("role") or "")
                content = msg.get("content")
            else:
                # OpenAI SDK message objects (e.g., ChatCompletionMessage) are not dicts.
                role = str(getattr(msg, "role", "") or "")
                content = getattr(msg, "content", None)
            if role not in ("user", "assistant"):
                continue
            text = str(content or "").strip()
            if not text:
                continue
            compact.append({"role": role, "content": text})
        return compact[-max_turns:]

    def chat(
        self, user_message: str
    ) -> tuple[str, list[TrailerListing], list[dict[str, Any]], dict[str, Any] | None]:
        """
        Process a user message and return
        (assistant_text, trailer_listings, product_fetch_debug_per_tool, thinking_context).
        `product_fetch_debug` is a list of tool-call debug dicts (one per search_trailers
        in this user turn), empty if no search ran.
        `thinking_context` is a compact payload for the thinking explainer.
        """
        self._history.append({"role": "user", "content": user_message})

        returned_listings: list[TrailerListing] = []
        product_fetch_per_tool: list[dict[str, Any]] = []

        while True:
            response = self.openai.chat.completions.create(
                model=OPENAI_MODEL,
                messages=self._history,
                tools=[SEARCH_TOOL],
                tool_choice="auto",
            )

            msg = response.choices[0].message

            # No tool call → direct response
            if not msg.tool_calls:
                text = msg.content or ""
                self._history.append({"role": "assistant", "content": text})
                thinking_context = {
                    "user_message": user_message,
                    "assistant_message": text,
                    "conversation_window": self._thinking_conversation_window(),
                    "tool_runs": product_fetch_per_tool,
                    "selected_recommendations": [
                        {
                            "rank": i,
                            "listing_id": lst.listing_id,
                            "title": lst.title,
                            "length": lst.length,
                            "payload_capacity": lst.payload_capacity,
                            "gvwr": lst.gvwr,
                            "price_display": lst.price_display,
                            "url": lst.url,
                            "relevance_score": lst.score,
                        }
                        for i, lst in enumerate(returned_listings, 1)
                    ],
                }
                return text, returned_listings, product_fetch_per_tool, thinking_context

            # Tool call
            self._history.append(msg)

            for tool_call in msg.tool_calls:
                tool_result, listings, tool_debug, result_for_db = self._execute_tool_call(
                    tool_call
                )
                product_fetch_per_tool.append(tool_debug)
                if listings:
                    returned_listings = listings[:SEARCH_MAX_RECOMMENDATIONS]

                self._history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

            # Loop back to get the final text response after tool results
