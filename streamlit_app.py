import os
import re
from datetime import datetime

import streamlit as st

import trailer_chatbot as bot
from logger_setup import get_logger

# App-wide logger
logger = get_logger("trailer.ui")

# Load local .env early so the sidebar env-check sees it.
bot.load_env_if_present()
logger.info("Streamlit app starting up")


st.set_page_config(
    page_title="Trailer Place — Chatbot",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource(show_spinner=False)
def _get_runtime():
    # Create clients once per Streamlit server process.
    logger.info("Initialising runtime clients (cached)")
    openai, _pc, index = bot.init_clients()
    logger.info("Runtime clients ready")
    return openai, index


def _env_health() -> tuple[bool, list[str]]:
    missing = []
    for k in ("OPENAI_API_KEY", "PINECONE_API_KEY"):
        if not os.environ.get(k):
            missing.append(k)
    if missing:
        logger.warning("Missing env vars detected by UI: %s", missing)
    return (len(missing) == 0, missing)


def _init_state():
    if "messages" not in st.session_state:
        # Start with an empty transcript; we only respond after the user types.
        st.session_state.messages = []
    if "show_debug" not in st.session_state:
        st.session_state.show_debug = False
    if "lead" not in st.session_state:
        st.session_state.lead = {"full_name": None, "phone": None, "email": None}
    if "lead_captured" not in st.session_state:
        st.session_state.lead_captured = False
    if "lead_thanked" not in st.session_state:
        st.session_state.lead_thanked = False


def _history_for_llm():
    # Convert UI messages → OpenAI chat format.
    out = []
    for m in st.session_state.messages:
        if m["role"] in ("user", "assistant"):
            out.append({"role": m["role"], "content": m["content"]})
    return out[-12:]  # last 6 turns


_LEAD_PATTERNS = {
    # Capture values even if multiple fields are on the same line.
    # We stop at the next field label or end-of-string.
    "full_name": re.compile(
        r"(?is)\b(?:full\s*name|name)\s*:\s*(.+?)(?=\b(?:phone\s*number|phone|mobile|email)\s*:|$)"
    ),
    "phone": re.compile(
        r"(?is)\b(?:phone\s*number|phone|mobile)\s*:\s*(.+?)(?=\b(?:full\s*name|name|email)\s*:|$)"
    ),
    "email": re.compile(
        r"(?is)\bemail\s*:\s*(.+?)(?=\b(?:full\s*name|name|phone\s*number|phone|mobile)\s*:|$)"
    ),
}


def _extract_lead_fields(text: str) -> tuple[dict, str]:
    """
    Extract lead fields from free text. Returns (fields_found, remaining_text).
    remaining_text is the original text with any lead lines removed.
    """
    raw = (text or "").strip()
    found: dict = {}

    remaining = raw
    for key, pat in _LEAD_PATTERNS.items():
        m = pat.search(raw)
        if not m:
            continue
        val = (m.group(1) or "").strip()
        if not val:
            continue
        found[key] = val
        # Remove the matched segment from remaining text.
        remaining = re.sub(re.escape(m.group(0)), " ", remaining, count=1, flags=0)

    # Normalise leftover whitespace/newlines.
    remaining = " ".join(remaining.split()).strip()

    if "email" in found:
        found["email"] = found["email"].strip().lower()

    return found, remaining


def _lead_is_complete() -> bool:
    lead = st.session_state.lead or {}
    return bool((lead.get("full_name") or "").strip()) and bool((lead.get("phone") or "").strip())


_init_state()


with st.sidebar:
    st.markdown("## Trailer Place")

    ok, missing = _env_health()
    if ok:
        st.success("Secrets detected. Ready to chat.")
    else:
        st.error(f"Missing required env vars: {', '.join(missing)}")
        st.markdown(
            "Set these in Streamlit **Secrets** (Cloud) or your local environment/.env."
        )

    st.divider()
    st.session_state.show_debug = st.toggle("Show debug", value=st.session_state.show_debug)
    top_k = st.slider("Top K results", min_value=3, max_value=10, value=5, step=1)

    st.divider()
    if st.session_state.lead_captured:
        st.success("Contact captured.")
        lead = st.session_state.lead
        st.caption(f"Name: {lead.get('full_name') or '—'}")
        st.caption(f"Phone: {lead.get('phone') or '—'}")
        st.caption(f"Email: {lead.get('email') or '—'}")


    if st.button("New chat", use_container_width=True):
        # Reset lead flow fully so thank-you can show again and we never get empty replies.
        for k in ("messages", "lead", "lead_captured", "lead_thanked"):
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    st.caption(
        "Tip: Try queries like “new bumper pull enclosed under $9000” or “used livestock in black”."
    )


st.markdown("## Trailer Recommendation Chat")
st.caption("Test the chatbot against your Pinecone inventory.")




for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])


prompt = st.chat_input("Ask about trailers…")
if prompt:
    logger.info("User message received (len=%s)", len(prompt))
    st.session_state.messages.append(
        {"role": "user", "content": prompt, "ts": datetime.utcnow().isoformat()}
    )
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        found, remaining = _extract_lead_fields(prompt)
        if found:
            logger.info("Lead fields found: %s", sorted(found.keys()))
            st.session_state.lead.update({k: v for k, v in found.items() if v})
        was_captured = bool(st.session_state.lead_captured)
        st.session_state.lead_captured = _lead_is_complete()
        just_captured = (not was_captured) and bool(st.session_state.lead_captured)

        if not st.session_state.lead_captured:
            logger.info(
                "Message gated; lead incomplete (have_name=%s have_phone=%s)",
                bool((st.session_state.lead.get("full_name") or "").strip()),
                bool((st.session_state.lead.get("phone") or "").strip()),
            )
            missing_parts = []
            if not (st.session_state.lead.get("full_name") or "").strip():
                missing_parts.append("Full name")
            if not (st.session_state.lead.get("phone") or "").strip():
                missing_parts.append("Phone number")
            msg = "Before we can help with trailer recommendations, please share:\n\n"
            msg += "\n".join([f"- **{p}:**" for p in missing_parts])
            msg += "\n- **Email:** (optional)"
            st.markdown(msg)
            answer = msg
            result = {"filters_used": {}, "filter_error": None, "match_count": 0}
        else:
            thank_you = "Thank you for contacting Trailer Space. How can we help you today?"
            show_thank_you = just_captured and (not st.session_state.lead_thanked)
            if show_thank_you:
                st.session_state.lead_thanked = True
                st.markdown(thank_you)

            answer = thank_you if show_thank_you else ""
            result = {"filters_used": {}, "filter_error": None, "match_count": 0}

            query = (remaining or "").strip()
            if query:
                with st.spinner("Searching inventory…"):
                    try:
                        bot.TOP_K = int(top_k)
                        logger.info("Chat turn start (top_k=%s)", bot.TOP_K)

                        openai, index = _get_runtime()
                        result = bot.chat_once(
                            query,
                            _history_for_llm(),
                            openai_client=openai,
                            index=index,
                            retry_without_filter=True,
                        )
                        model_answer = (result["answer"] or "").replace("`", "")
                        logger.info(
                            "Chat turn complete (matches=%s, retried_unfiltered=%s)",
                            result.get("match_count"),
                            result.get("retried_unfiltered"),
                        )
                        st.markdown(model_answer)
                        answer = (thank_you + "\n\n" + model_answer) if show_thank_you else model_answer
                    except Exception as e:
                        logger.exception("Streamlit chat turn failed")
                        err = f"Error: {e}"
                        st.markdown(err)
                        answer = (thank_you + "\n\n" + err) if show_thank_you else err
                        result = {"filters_used": {}, "filter_error": str(e), "match_count": 0}

            if not (remaining or "").strip() and not (answer or "").strip():
                nudge = (
                    "How can we help you find the right trailer today? "
                    "Tell us your budget, trailer type, hitch, size, and condition."
                )
                st.markdown(nudge)
                answer = nudge

        if st.session_state.show_debug:
            with st.expander("Debug", expanded=False):
                st.write(
                    {
                        "filters_used": result.get("filters_used"),
                        "filter_error": result.get("filter_error"),
                        "match_count": result.get("match_count"),
                        "retried_unfiltered": result.get("retried_unfiltered"),
                    }
                )
                st.text(result.get("results_text", ""))

    # Avoid rendering empty assistant bubbles (e.g., lead updates with no query).
    if (answer or "").strip():
        st.session_state.messages.append(
            {"role": "assistant", "content": answer, "ts": datetime.utcnow().isoformat()}
        )
