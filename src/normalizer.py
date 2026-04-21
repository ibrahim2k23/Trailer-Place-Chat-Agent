import re
from typing import Optional

# ---------------------------------------------------------------------------
# Canonical mappings
# ---------------------------------------------------------------------------

CATEGORY_MAP = {
    "enclosed": "Enclosed",
    "enclose": "Enclosed",
    "utility": "Utility",
    "dump": "Dump",
    "flatbed": "Flatbed",
    "equipment": "Equipment",
    "livestock": "Livestock",
    "cattle": "Livestock",
    "tilt": "Tilt",
    "aluminum": "Aluminum",
    "car hauler": "Car Hauler",
    "fiber": "Fiber",
    "roll off": "Roll Off",
    "diesel tank": "Diesel Tank",
    "race trailer": "Race Trailer",
    "tank": "Tank",
    "welding": "Welding",
}

# Subcategories that are effectively noise (model numbers, dimensions, etc.)
_JUNK_SUBCATEGORY_RE = re.compile(r'^\d|["\']|^\d+x\d+', re.IGNORECASE)
_JUNK_SUBCATEGORY_WORDS = {"unspecified", "trailers", "none", ""}

MAKE_MAP = {
    "cargo craft trailers": "Cargo Craft Trailers",
    "cargo craft": "Cargo Craft Trailers",
    "diamond c trailers": "Diamond C Trailers",
    "diamond c": "Diamond C Trailers",
    "stallion": "Stallion",
    "texas pride": "Texas Pride",
    "iron bull trailers": "Iron Bull Trailers",
    "iron bull": "Iron Bull Trailers",
    "east texas trailers": "East Texas Trailers",
    "east texas": "East Texas Trailers",
    "calico trailers": "Calico Trailers",
    "calico": "Calico Trailers",
    "kaufman trailers": "Kaufman Trailers",
    "kaufman": "Kaufman Trailers",
    "ameraitrail": "AmeriTrail",
    "ameritrail": "AmeriTrail",
}

HITCH_MAP = {
    "bumper pull": "Bumper Pull",
    "gooseneck": "Gooseneck",
}

# Dealer contact noise patterns
_CONTACT_RE = re.compile(
    r"("
    r"trailerplace\.com|"
    r"trailer\s*place|"
    r"financing\s+and\s+delivery\s+available|"
    r"2507\s+county\s+road\s+231(?:\s+wharton\s+tx\s+77488)?|"
    r"wharton\s*tx\s*77488"
    r")",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
_URL_RE = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def normalize_category(cat: str) -> str:
    if not cat:
        return "Unknown"
    key = cat.strip().lower()
    return CATEGORY_MAP.get(key, cat.strip().title())


def normalize_subcategory(sub: Optional[str]) -> Optional[str]:
    if not sub:
        return None
    cleaned = sub.strip()
    if cleaned.lower() in _JUNK_SUBCATEGORY_WORDS:
        return None
    if _JUNK_SUBCATEGORY_RE.match(cleaned):
        return None
    # Strings that look like dimension specs (e.g. "HXD208 102\" x 22'")
    if len(cleaned) > 30:
        return None
    return cleaned.title()


def normalize_make(make: str) -> str:
    if not make:
        return "Unknown"
    key = make.strip().lower()
    return MAKE_MAP.get(key, make.strip().title())


def normalize_color(color: str) -> str:
    if not color:
        return "Unknown"
    return color.strip().title()


def normalize_hitch(hitch: Optional[str]) -> Optional[str]:
    if not hitch:
        return None
    return HITCH_MAP.get(hitch.strip().lower(), hitch.strip().title())


def normalize_condition(condition: str) -> str:
    if not condition:
        return "New"
    return condition.strip()


def build_category_subcategory(category: str, subcategory: Optional[str]) -> str:
    cat = normalize_category(category)
    sub = normalize_subcategory(subcategory)
    if sub and sub.lower() != cat.lower():
        return f"{cat} > {sub}"
    return cat


def clean_dealer_notes(notes: Optional[str]) -> str:
    if not notes:
        return ""
    text = notes.strip()
    text = _CONTACT_RE.sub(" ", text)
    text = _PHONE_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text[:800]


def build_embedding_text(info: dict, title: str, dealer_notes: Optional[str]) -> str:
    parts = [title]

    fields = [
        ("Make", info.get("make", "")),
        ("Year", info.get("year", "")),
        ("Model", info.get("model", "")),
        ("Trim", info.get("trim", "")),
        ("Condition", info.get("condition", "")),
        ("Category", info.get("category", "")),
        ("Subcategory", info.get("subcategory", "")),
        ("Hitch Type", info.get("hitch_type", "")),
        ("Color", info.get("color", "")),
        ("Length", info.get("length", "")),
        ("Width", info.get("width", "")),
        ("GVWR", info.get("gvwr", "")),
        ("Axles", info.get("axles", "")),
        ("Axle Capacity", info.get("axle_capacity", "")),
        ("Payload Capacity", info.get("payload_capacity", "")),
        ("Dry Weight", info.get("dry_weight", "")),
        ("Material", info.get("trailer_material", "")),
        ("Floor", info.get("floor", "")),
        ("Price", info.get("price", "")),
    ]

    for label, value in fields:
        if value and str(value).strip():
            parts.append(f"{label}: {value}")

    notes_clean = clean_dealer_notes(dealer_notes)
    if notes_clean:
        parts.append(f"Details: {notes_clean}")

    return " | ".join(parts)
