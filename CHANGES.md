# Changelog

## 2026-04-17 — Language Mapping Integration (`src/agent.py`)

Source: `Trailer_Language_Mapping_agent_ready_v2 4-15-26.xlsx`

---

### `SEARCH_TOOL` — updated

**`description`**
- Old: generic "call once you have enough context"
- New: explicitly instructs the model to wait until required qualification slots are collected, and to build a rich query (haul item + weight + use case + subcategory tags) for better Pinecone vector matching

**`query` field description**
- Old: "Natural language description of what the customer is looking for"
- New: Detailed guidance — include haul item, estimated weight, trailer type, and subcategory details in the query string

**`category_subcategory` field description**
- Old: listed 13 categories, no guidance on edge cases
- New: lists all 15 resolved categories from `Category_Taxonomy`; adds explicit rule that aluminum is a modifier — resolve to base category first before filtering

---

### `_build_system_prompt()` — fully rebuilt

Previous prompt: 8 numbered guidelines, ~400 chars.
New prompt: 8 structured sections, ~8,700 chars / ~2,200 tokens.

#### Section: INTENT ROUTING *(new)*
Source: `Intent_Router` sheet

Added handling for 5 non-trailer intents that previously fell through as unstructured chat:
- `financing` → scripted response + phone number
- `trade_in` → scripted response + what info to collect
- `service / parts` → scripted response + phone number
- `store_info` → location + phone number answer
- `human_handoff` → immediate offer to connect + phone number

#### Section: CATEGORY IDENTIFICATION *(new)*
Source: `Language_Map` sheet (54 rows condensed to 13 disambiguation rules)

Key rules added:
- **Aluminum modifier rule** — "aluminum" / "lightweight" / "won't rust" / "Aluma" must resolve to a base category first, never searched directly as a category
- **Toy hauler ambiguity** — confidence 0.55; always ask open-deck vs. camper before proceeding
- **Office / cooldown trailer overlap** — ask fiber/telecom vs. general office before routing
- Brand-name lookups: Galyean / Star → Livestock (cattle); Calico → Livestock (goats/hogs)
- 9 additional phrase-to-category mappings (lowboy, skid steer, hotshot, splicing trailer, etc.)

#### Section: QUALIFICATION — SLOT COLLECTION *(new)*
Source: `Slot_Rules` + `Question_Flows` sheets

Added per-category required slot sequences for all 15 categories. Model must collect slots in order and ask only ONE question at a time before calling `search_trailers`. Previously the model could search with almost no context.

Added 3 recovery rules for when a customer doesn't know a slot:
- Weight unknown → ask make/model of the item being hauled
- Size unknown → ask for a rough estimate
- Tow vehicle unknown → accept half-ton / three-quarter-ton / one-ton as rough answers

#### Section: RECOMMENDATIONS — updated framing
Source: `Recommendation_Logic` sheet

Added output template language: "Based on what you described, you're likely looking for a [category]. Depending on [key factor], you may be in the [size/capacity range]."
Retained existing rules: 1 result by default, up to 3 if genuinely close, never invent specs.

#### Section: OBJECTION HANDLING *(new)*
Source: `Objection_Handling` sheet

Added 6 scripted responses:
- Don't know size
- Don't know tow capacity
- Too expensive
- Only need it occasionally
- Want the lightest trailer
- Never bought a trailer before

#### Section: SAFETY GUARDRAILS — expanded
Source: `Safety_Guardrails` sheet

Old: 1 line — "Never make up trailer specs."
New: 7 explicit never-state-as-fact rules covering:
- Towing capacity
- Payload / GVWR fit
- CDL thresholds
- Brake requirements
- Fuel transport compliance
- Live inventory availability
- Final pricing

#### Section: HANDOFF TRIGGERS *(new)*
Source: `Handoff_Triggers` sheet

Added 5 conditions that trigger a human handoff offer with scripted responses:
- Customer asks for a person
- Customer wants an exact out-the-door quote
- Customer wants live inventory confirmation
- Customer is frustrated or conversation is looping
- High-risk compliance question (CDL, towing law, fuel transport)

#### Section: JARGON EXPLANATIONS *(new)*
Source: `Customer_Explanations` sheet

Added 8 plain-English definitions the model can use when a customer seems unfamiliar with a term: bumper pull, gooseneck, deckover, GVWR, payload, dovetail, V-nose, non-CDL.

---

### Files unchanged
- `app.py`
- `src/models.py`
- `src/normalizer.py`
- `src/ingest.py`
- `listings_final_v4.xlsx`
- Pinecone index (no re-ingestion needed)
