import os
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
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "Tell me what kind of trailer you’re looking for (type, budget, hitch, condition, etc.).",
                "ts": datetime.utcnow().isoformat(),
            }
        ]
    if "show_debug" not in st.session_state:
        st.session_state.show_debug = False


def _history_for_llm():
    # Convert UI messages → OpenAI chat format.
    out = []
    for m in st.session_state.messages:
        if m["role"] in ("user", "assistant"):
            out.append({"role": m["role"], "content": m["content"]})
    return out[-12:]  # last 6 turns


_init_state()


with st.sidebar:
    st.markdown("## Trailer Place")
    st.caption("Test the recommendation chatbot against Pinecone inventory.")

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
    if st.button("New chat", use_container_width=True):
        for k in ("messages",):
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    st.caption(
        "Tip: Try queries like “new bumper pull enclosed under $9000” or “used livestock in black”."
    )


st.markdown("## Trailer Recommendation Chat")




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
        with st.spinner("Searching inventory…"):
            try:
                # Keep bot.TOP_K consistent with sidebar setting for this run
                bot.TOP_K = int(top_k)
                logger.info("Chat turn start (top_k=%s)", bot.TOP_K)

                openai, index = _get_runtime()
                result = bot.chat_once(
                    prompt,
                    _history_for_llm(),
                    openai_client=openai,
                    index=index,
                    retry_without_filter=True,
                )
                # Some model outputs include backticks for emphasis; Streamlit renders
                # those as inline code (green highlight in dark theme). Strip them.
                answer = (result["answer"] or "").replace("`", "")
                logger.info(
                    "Chat turn complete (matches=%s, retried_unfiltered=%s)",
                    result.get("match_count"),
                    result.get("retried_unfiltered"),
                )
            except Exception as e:
                logger.exception("Streamlit chat turn failed")
                answer = f"Error: {e}"
                result = {"filters_used": {}, "filter_error": str(e), "match_count": 0}

        st.markdown(answer)

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

    st.session_state.messages.append(
        {"role": "assistant", "content": answer, "ts": datetime.utcnow().isoformat()}
    )
