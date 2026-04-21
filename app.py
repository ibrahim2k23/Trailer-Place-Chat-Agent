"""
TrailerPlace AI Assistant — Streamlit frontend.

Run:
    conda run -n islam360 streamlit run app.py
"""
import os
import secrets

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from src.agent import TrailerAgent
from src.models import TrailerListing

load_dotenv()

_AUTH_USER = (os.getenv("TRAILERPLACE_APP_USERNAME") or "").strip()
_AUTH_PASS = (os.getenv("TRAILERPLACE_APP_PASSWORD") or "").strip()
_AUTH_CONFIGURED = bool(_AUTH_USER and _AUTH_PASS)


def _password_matches(got: str, expected: str) -> bool:
    ga, ea = got.encode("utf-8"), expected.encode("utf-8")
    if len(ga) != len(ea):
        return False
    return secrets.compare_digest(ga, ea)


st.set_page_config(
    page_title="TrailerPlace · Assistant",
    page_icon="🚛",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
# CSS INJECTION — via iframe JS so Streamlit sanitizer is bypassed
# ─────────────────────────────────────────────────────────────
components.html("""
<script>
(function() {
  var css = `
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');

    :root {
      --tp-bg: var(--background-color);
      --tp-secondary-bg: var(--secondary-background-color);
      --tp-text: var(--text-color);
      --tp-border: rgba(128, 128, 128, 0.28);
      --tp-shadow: rgba(0, 0, 0, 0.15);
    }

    html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"],
    [data-testid="stChatMessage"], [data-testid="stChatMessage"] * {
      font-family: 'Outfit', sans-serif !important;
    }
    [data-testid="stAppViewContainer"], [data-testid="stMain"] {
      background: var(--tp-bg) !important;
      color: var(--tp-text) !important;
    }

    /* Hide chrome */
    #MainMenu, footer, [data-testid="stToolbar"],
    [data-testid="stDecoration"] { display: none !important; }

    .block-container {
      padding: 1.5rem 1.5rem 0.5rem 1.5rem !important;
      max-width: 800px !important;
    }

    /* Sidebar */
    [data-testid="stSidebar"] { background: var(--tp-secondary-bg) !important; }
    [data-testid="stSidebar"] * {
      color: var(--tp-text) !important;
      font-family: 'Outfit', sans-serif !important;
    }
    [data-testid="stSidebar"] hr { border-color: var(--tp-border) !important; }
    [data-testid="stSidebar"] button {
      background: var(--tp-bg) !important;
      border: 1px solid var(--tp-border) !important;
      color: var(--tp-text) !important;
      border-radius: 8px !important;
      font-family: 'Outfit', sans-serif !important;
      transition: background .15s;
    }
    [data-testid="stSidebar"] button:hover { filter: brightness(0.96); }

    /* Chat input */
    [data-testid="stChatInput"] > div {
      background: var(--tp-secondary-bg) !important;
      border: 1.5px solid var(--tp-border) !important;
      border-radius: 14px !important;
      box-shadow: 0 2px 10px var(--tp-shadow) !important;
    }
    [data-testid="stChatInput"] textarea {
      font-family: 'Outfit', sans-serif !important;
      font-size: 15px !important;
      color: var(--tp-text) !important;
      caret-color: var(--tp-text) !important;
    }
    [data-testid="stChatInput"] textarea::placeholder {
      color: rgba(148, 163, 184, 0.95) !important;
    }
    [data-testid="stChatInput"] button {
      background: #F97316 !important;
      border-radius: 10px !important;
      border: none !important;
    }
    [data-testid="stChatInput"] button:hover { background: #EA6A0A !important; }
    [data-testid="stChatInput"] button svg { stroke: #fff !important; fill: #fff !important; }

    /* Remove chat message default background box */
    [data-testid="stChatMessage"] {
      background: transparent !important;
      box-shadow: none !important;
      border: none !important;
      padding: 2px 0 !important;
      gap: 8px !important;
    }
    [data-testid="stChatMessageContent"] {
      background: transparent !important;
    }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #D1CCC4; border-radius: 10px; }
  `;
  var el = window.parent.document.createElement('style');
  el.textContent = css;
  window.parent.document.head.appendChild(el);
})();
</script>
""", height=0)


# ─────────────────────────────────────────────────────────────
# TRAILER CARD — inline styles only, no class dependencies
# ─────────────────────────────────────────────────────────────
def render_card(listing: TrailerListing, rank: int):
    price_str = (
        listing.price_display
        or (f"${listing.price:,.0f}" if listing.price is not None else "Call for price")
    )
    badge_bg  = "#DCFCE7" if listing.condition == "New" else "#FEF9C3"
    badge_fg  = "#15803D" if listing.condition == "New" else "#A16207"

    specs = [
        ("Make",     listing.make),
        ("Year",     listing.year),
        ("Type",     listing.category_subcategory),
        ("Hitch",    listing.hitch_type),
        ("Color",    listing.color),
        ("Length",   listing.length),
        ("Width",    listing.width),
        ("GVWR",     listing.gvwr),
        ("Axles",    listing.axles),
        ("Material", listing.trailer_material),
        ("Floor",    listing.floor),
        ("Payload",  listing.payload_capacity),
    ]
    spec_cells = "".join(
        f'<div style="min-width:88px;">'
        f'<div style="font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#9CA3AF;font-weight:500;font-family:Outfit,sans-serif;">{lbl}</div>'
        f'<div style="font-size:13px;font-weight:600;color:#18181B;font-family:Outfit,sans-serif;">{val}</div>'
        f'</div>'
        for lbl, val in specs if val
    )
    pay_html = (
        f'<div style="font-size:12px;color:#6B7280;margin-top:3px;font-family:Outfit,sans-serif;">'
        f'Payments from <b style="color:#18181B;">{listing.payments_from}</b></div>'
    ) if listing.payments_from else ""

    # No line may start with 4+ spaces — Streamlit Markdown treats that as a code block
    # and would render literal tags like </div> in a monospace box.
    st.markdown(
        f'<div style="background:#FFFFFF;border:1px solid #E9E6E0;border-left:4px solid #F97316;'
        f"border-radius:12px;padding:16px 18px;margin:8px 0 4px 0;"
        f'box-shadow:0 2px 8px rgba(0,0,0,0.07);">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">'
        f'<div>'
        f'<div style="font-size:15px;font-weight:700;color:#18181B;margin-bottom:5px;font-family:Outfit,sans-serif;">'
        f"#{rank}&nbsp;&nbsp;{listing.title}</div>"
        f'<span style="display:inline-block;padding:2px 10px;border-radius:20px;'
        f"background:{badge_bg};color:{badge_fg};font-size:11px;font-weight:600;"
        f'font-family:Outfit,sans-serif;">{listing.condition}</span></div>'
        f'<div style="text-align:right;">'
        f'<div style="font-size:21px;font-weight:800;color:#F97316;font-family:Outfit,sans-serif;">{price_str}</div>'
        f"{pay_html}</div></div>"
        f'<div style="border-top:1px solid #F3F0EB;margin:12px 0;"></div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:14px 20px;">{spec_cells}</div>'
        f'<a href="{listing.url}" target="_blank" '
        f'style="display:inline-block;margin-top:14px;background:#F97316;color:#FFFFFF;'
        f"padding:8px 18px;border-radius:8px;font-size:13px;font-weight:600;"
        f'text-decoration:none;font-family:Outfit,sans-serif;">'
        f"View Full Listing &rarr;</a></div>",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────
if "auth_ok" not in st.session_state:
    st.session_state.auth_ok = False
if "messages" not in st.session_state:
    st.session_state.messages = []


# ─────────────────────────────────────────────────────────────
# LOGIN (env: TRAILERPLACE_APP_USERNAME + TRAILERPLACE_APP_PASSWORD)
# ─────────────────────────────────────────────────────────────
if not _AUTH_CONFIGURED:
    st.error(
        "App login is not configured. Add **TRAILERPLACE_APP_USERNAME** and "
        "**TRAILERPLACE_APP_PASSWORD** to your `.env` file (both non-empty), then restart."
    )
    st.stop()

if not st.session_state.auth_ok:
    with st.sidebar:
        st.markdown("### 🚛 TrailerPlace")
        st.caption("Sign in to continue")
    st.markdown("### Sales Chat")
    st.caption("Sign in to use the assistant")
    with st.form("app_login"):
        u = st.text_input("Username", autocomplete="username")
        p = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)
        if submitted:
            if u.strip() == _AUTH_USER and _password_matches(p, _AUTH_PASS):
                st.session_state.auth_ok = True
                st.rerun()
            else:
                st.error("Incorrect username or password.")
    st.stop()


if "agent" not in st.session_state:
    st.session_state.agent = TrailerAgent()


# ─────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🚛 TrailerPlace")
    st.caption("AI Sales Assistant")
    st.divider()
    st.markdown("📍 Wharton, TX")
    st.markdown("📞 (979) 532-1486")
    st.markdown("💳 Financing available")
    st.markdown("🚚 Delivery available")
    st.divider()
    if st.button("↺  New Conversation", use_container_width=True):
        st.session_state.agent = TrailerAgent()
        st.session_state.messages = []
        st.rerun()
    if st.button("Log out", use_container_width=True):
        st.session_state.auth_ok = False
        if "agent" in st.session_state:
            del st.session_state.agent
        st.session_state.messages = []
        st.rerun()


# ─────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────
st.markdown("### Sales Chat")
st.caption("Ask about any trailer in our inventory")


# ─────────────────────────────────────────────────────────────
# RENDER HISTORY
# ─────────────────────────────────────────────────────────────
if not st.session_state.messages:
    st.markdown("""
<div style="text-align:center;padding:60px 20px 40px 20px;">
  <div style="font-size:36px;margin-bottom:12px;">🚛</div>
  <div style="font-size:17px;font-weight:600;color:var(--text-color);margin-bottom:6px;font-family:Outfit,sans-serif;">
    Welcome to TrailerPlace
  </div>
  <div style="font-size:14px;color:var(--text-color);opacity:.72;max-width:320px;margin:0 auto;line-height:1.6;font-family:Outfit,sans-serif;">
    Start by sending your name, phone number, and email — then tell us what you're looking for.
  </div>
</div>
""", unsafe_allow_html=True)
else:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            for i, listing in enumerate(msg.get("listings") or [], 1):
                render_card(listing, i)


# ─────────────────────────────────────────────────────────────
# CHAT INPUT
# ─────────────────────────────────────────────────────────────
placeholder = (
    "Share your name, phone, and email to get started..."
    if not st.session_state.messages else "Type a message..."
)

if prompt := st.chat_input(placeholder):
    # 1. Persist user message
    st.session_state.messages.append({"role": "user", "content": prompt, "listings": None})

    # 2. Render user bubble immediately (visible before agent responds)
    with st.chat_message("user"):
        st.markdown(prompt)

    # 3. Get response — spinner is visible while user bubble is already on screen
    with st.chat_message("assistant"):
        with st.spinner(""):
            response_text, listings = st.session_state.agent.chat(prompt)
        st.markdown(response_text)
        for i, listing in enumerate(listings or [], 1):
            render_card(listing, i)

    # 4. Persist response
    st.session_state.messages.append({
        "role": "assistant",
        "content": response_text,
        "listings": listings or None,
    })

    # 5. Rerun to reset widget state — prevents the "send twice" bug.
    #    Content is already rendered above so the rerun re-draws from history seamlessly.
    st.rerun()
