"""
Conversational trailer recommendation agent.
Uses OpenAI tool calling to decide when to search vs. ask clarifying questions.
"""
import json
import logging
import os
import re
from typing import Optional

from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone

from src.models import TrailerFilter, TrailerListing
from src.normalizer import normalize_make, normalize_color, normalize_hitch, normalize_category

load_dotenv()

logger = logging.getLogger(__name__)


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


OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "trailerplace-listings")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
# Pinecone query top_k (how many vector matches to retrieve per search)
SEARCH_TOP_K = _env_positive_int("SEARCH_TOP_K", 5, minimum=1)
# Max listings returned to the UI / tool after score-threshold filtering (at least 1)
SEARCH_MAX_RECOMMENDATIONS = _env_positive_int("SEARCH_MAX_RECOMMENDATIONS", 3, minimum=1)


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
                    "description": "Hitch type, only if customer specifies",
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

**Equipment:** haul_item → haul_weight_lbs → haul_length_ft → tow_vehicle · optional: hitch preference, loading style (ramps / deckover / drive-over fenders)
**Car Hauler:** vehicle_type → haul_weight_lbs → vehicle_length_ft → tow_vehicle · optional: open vs. covered
**Utility:** haul_item → haul_weight_lbs → tow_vehicle · optional: size, sides / gate / tool storage
**Dump:** haul_material → haul_weight_lbs → tow_vehicle · optional: dump mechanism (scissor / telescopic / standard)
**Tilt:** haul_item → haul_weight_lbs → tow_vehicle · optional: full tilt vs. stationary front deck
**Enclosed:** use_case → cargo_size → tow_vehicle · optional: AC / windows / cabinets / finished interior
**Livestock:** animal_type → animal_count → tow_vehicle · optional: length, gate preferences (butterfly / swing / slant)
**Roll Off:** package_scope (trailer / bins / both) → bin_size → tow_vehicle
**Diesel Tank:** fuel_type (diesel / gasoline) → tank_capacity → tow_vehicle
**Flatbed:** haul_item → haul_weight_lbs → tow_vehicle · optional: step deck vs. standard, CDL concern
**Fiber:** use_case (splicing / office / cooldown) → tow_vehicle · optional: crew size, AC / workbench / generator
**Race Trailer:** vehicle_type → trailer_length_ft → tow_vehicle · optional: cabinets / workspace / living quarters
**Welding:** equipment_list → tow_vehicle · optional: total weight
**Aluminum (modifier):** resolve base_category first → payload_need → tow_vehicle
**Offroad:** use_case (camping / overlanding / gear hauling) → tow_vehicle · optional: sleeping need

Recovery rules — when a customer doesn't know a slot:
- Weight unknown → "If you know the make and model of what you're hauling, I can usually work from that."
- Size unknown → "Even a rough estimate helps — about how long is it?"
- Tow vehicle unknown → "Even a rough answer works — are you towing with an SUV, half-ton, three-quarter-ton, or one-ton?"

## SEARCHING & RECOMMENDATIONS
Once required slots are filled, call search_trailers immediately with a rich query. After results come back:
- Open with a short recap of what they asked for (category, haul, rough weight, tow vehicle, etc.), then introduce the options.
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

This trailer is an excellent match for your needs, providing a significant payload capacity that exceeds what you plan to haul. The bumper pull hitch allows for easy towing with your half-ton SUV.
```

- **Do not** add lines like "View this trailer", "Click here", or paste the raw URL again on its own line — the hyperlinked title plus card citation rules below are enough.
- Only add extra options beyond your top pick if they are genuinely very close in price, size, and use case to the top result.
- Never list more than MAX_RECOMMENDATIONS_PLACEHOLDER.
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
- "don't know what my truck can tow" → "I can help narrow options, but final towing capacity should be confirmed for your exact truck. What are you towing with — even a rough answer helps."
- "too expensive" → "Understood. We can usually narrow things down by budget, size, and how often you'll use it so you're not buying more trailer than you need. Do you have a budget range in mind?"
- "only need it once in a while" → "In that case it often makes sense to focus on the simplest trailer that safely fits what you're hauling. What are you hauling, and how heavy is it?"
- "want the lightest trailer" → "Aluminum may be worth looking at if lightweight and corrosion resistance are priorities. What type of trailer are you wanting in aluminum?"
- "never bought one before" → "No problem at all — that's exactly what I can help with. We can keep it simple and start with what you're hauling."

## SAFETY GUARDRAILS — never state these as confirmed facts
- **Towing capacity:** "I can help narrow options, but final towing capacity should be confirmed for your exact truck setup."
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

    def _search(self, query: str, trailer_filter: TrailerFilter, top_k: int = 5) -> list[TrailerListing]:
        vector = self._embed(query)
        pf = _build_pinecone_filter(trailer_filter)

        results = self.pc_index.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=pf,
        )

        matches = results.get("matches", [])
        self._log_matches(matches, query=query, pinecone_filter=pf, relaxed=False)
        # If strict filters return nothing, retry without category/make/color filters
        if not matches and pf:
            relaxed = {
                k: v for k, v in (pf or {}).items()
                if k in ("condition", "price", "hitch_type")
            }
            results = self.pc_index.query(
                vector=vector,
                top_k=top_k,
                include_metadata=True,
                filter=relaxed if relaxed else None,
            )
            matches = results.get("matches", [])
            self._log_matches(matches, query=query, pinecone_filter=relaxed if relaxed else None, relaxed=True)

        return [_metadata_to_listing(m) for m in matches]

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

    def _execute_tool_call(self, tool_call) -> tuple[str, list[TrailerListing]]:
        args = json.loads(tool_call.function.arguments)
        query = args.pop("query")

        trailer_filter = TrailerFilter(
            condition=args.get("condition"),
            price_min=args.get("price_min"),
            price_max=args.get("price_max"),
            category_subcategory=args.get("category_subcategory"),
            make=args.get("make"),
            color=args.get("color"),
            hitch_type=args.get("hitch_type"),
        )

        listings = self._search(query, trailer_filter, top_k=SEARCH_TOP_K)
        selected = self._pick_listings(listings, max_count=SEARCH_MAX_RECOMMENDATIONS)

        if not selected:
            tool_result = "No trailers found matching those criteria."
        else:
            result_dicts = []
            for i, lst in enumerate(selected, 1):
                d = {
                    "rank": i,
                    "title": lst.title,
                    "condition": lst.condition,
                    "price": lst.price_display
                    or (f"${lst.price:,.0f}" if lst.price is not None else "Call for price"),
                    "payments_from": lst.payments_from,
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

        return tool_result, selected

    @staticmethod
    def _pick_listings(
        listings: list[TrailerListing],
        threshold: float = 0.04,
        max_count: int = 3,
    ) -> list[TrailerListing]:
        """
        Return 1 listing by default.
        Return up to `max_count` only when extra results are within `threshold`
        cosine-score of the top result (i.e. genuinely close matches).
        """
        if not listings:
            return []
        cap = max(1, max_count)
        top_score = listings[0].score or 0.0
        selected = [listings[0]]
        for lst in listings[1:cap]:
            if (top_score - (lst.score or 0.0)) <= threshold:
                selected.append(lst)
            else:
                break
        return selected

    def chat(self, user_message: str) -> tuple[str, list[TrailerListing]]:
        """
        Process a user message and return (assistant_text, trailer_listings).
        trailer_listings is non-empty only when the agent performed a search.
        """
        self._history.append({"role": "user", "content": user_message})

        returned_listings: list[TrailerListing] = []

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
                visible = filter_listings_matching_reply(text, returned_listings)
                return text, visible

            # Tool call
            self._history.append(msg)

            for tool_call in msg.tool_calls:
                tool_result, listings = self._execute_tool_call(tool_call)
                if listings:
                    returned_listings = listings[:SEARCH_MAX_RECOMMENDATIONS]

                self._history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

            # Loop back to get the final text response after tool results
