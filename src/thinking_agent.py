"""
Turn-level "thinking flow" explainer.
Generates a human-readable trace from conversation + search/rerank context.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

logger = logging.getLogger(__name__)
thinking_log = logging.getLogger("trailerplace.thinking")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


THINKING_AGENT_ENABLED = _env_bool("THINKING_AGENT_ENABLED", True)
THINKING_AGENT_BACKGROUND = _env_bool("THINKING_AGENT_BACKGROUND", True)
THINKING_AGENT_MODEL = (
    os.getenv("THINKING_AGENT_MODEL")
    or os.getenv("OPENAI_MODEL")
    or "gpt-4o-mini"
).strip()
THINKING_AGENT_MAX_CONTEXT_CHARS = int((os.getenv("THINKING_AGENT_MAX_CONTEXT_CHARS") or "24000").strip())


def thinking_agent_enabled() -> bool:
    return THINKING_AGENT_ENABLED


def thinking_agent_background() -> bool:
    return THINKING_AGENT_BACKGROUND


def _safe_json(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=True, default=str)
    if len(text) <= THINKING_AGENT_MAX_CONTEXT_CHARS:
        return text
    return text[:THINKING_AGENT_MAX_CONTEXT_CHARS] + "...[truncated]"


def _stage_from_payload(payload: dict[str, Any]) -> str:
    tool_runs = payload.get("tool_runs") or []
    selected = payload.get("selected_recommendations") or []
    if not tool_runs:
        return "qualification"
    last_run = tool_runs[-1] if isinstance(tool_runs, list) and tool_runs else {}
    rerank = last_run.get("rerank") if isinstance(last_run, dict) else None
    if selected:
        return "recommendation"
    if isinstance(rerank, dict) and rerank.get("applied"):
        return "reranking"
    return "search"


def _fmt_num(v: Any, *, none_label: str = "not in payload") -> str:
    if v is None:
        return none_label
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


def _score_friendly_reason(
    score: dict[str, Any],
    req_payload: Any,
    req_length: Any,
    req_gvwr: Any,
) -> str:
    """Plain-English explanation of why a listing ranked where it did."""
    pr = score.get("payload_ratio")
    lr = score.get("length_ratio")
    gr = score.get("gvwr_ratio")
    fail_count = int(score.get("fail_count") or 0)
    payload_from = score.get("payload_from") or "unknown"
    penalty = float(score.get("penalty") or 0.0)

    parts: list[str] = []

    # Length
    if req_length is not None:
        try:
            req_l = float(req_length)
            if lr is None:
                parts.append("length not listed in inventory")
            else:
                l = float(lr)
                trailer_l = round(l * req_l, 1)
                if l < 1.0:
                    short_by = round(req_l - trailer_l, 1)
                    parts.append(
                        f"only {trailer_l} ft — {short_by} ft shorter than the {req_l:.0f} ft minimum (too short for the load)"
                    )
                elif abs(l - 1.0) < 0.01:
                    parts.append(f"length is exactly {trailer_l} ft — perfect match")
                else:
                    over_by = round(trailer_l - req_l, 1)
                    parts.append(
                        f"{trailer_l} ft — {over_by} ft longer than the {req_l:.0f} ft requested"
                    )
        except Exception:
            pass

    # Payload
    if req_payload is not None:
        try:
            req_p = float(req_payload)
            if pr is None:
                parts.append("payload capacity not listed in this trailer's inventory data")
            else:
                p = float(pr)
                trailer_cap = round(p * req_p)
                if p < 1.0:
                    short_by = round(req_p - trailer_cap)
                    parts.append(
                        f"can only carry {trailer_cap:,} lbs — {short_by:,} lbs short of the {req_p:,.0f} lb need"
                    )
                else:
                    extra = " (estimated from GVWR, not a dedicated payload spec)" if payload_from == "gvwr" else ""
                    parts.append(
                        f"can carry {trailer_cap:,} lbs — covers the {req_p:,.0f} lb need{extra}"
                    )
        except Exception:
            pass

    # GVWR
    if req_gvwr is not None:
        try:
            req_g = float(req_gvwr)
            if gr is None:
                parts.append("GVWR not listed in inventory")
            else:
                g = float(gr)
                trailer_gvwr = round(g * req_g)
                if g < 1.0:
                    parts.append(f"GVWR of {trailer_gvwr:,} lbs is below the {req_g:,.0f} lb minimum")
                else:
                    parts.append(f"GVWR of {trailer_gvwr:,} lbs meets the {req_g:,.0f} lb requirement")
        except Exception:
            pass

    if not parts:
        parts.append("best available match" if penalty > 0.1 else "strong overall fit")

    if fail_count > 0 and not any("too short" in p or "short of" in p or "below" in p for p in parts):
        parts.append(f"undersized on {fail_count} required dimension(s)")

    return "; ".join(parts)


def _deterministic_qualification_flow(payload: dict[str, Any]) -> str:
    history = payload.get("conversation_window") or []
    user_turns = [m for m in history if m.get("role") == "user"]
    assistant_msg = str(payload.get("assistant_message") or "").strip()
    user_msg = str(payload.get("user_message") or "").strip()
    turn_n = len(user_turns)

    # First turn — user just gave contact info, skip insight
    if turn_n <= 1:
        return ""

    collected = user_msg[:80].rstrip(".").rstrip(",") if user_msg else ""

    # Extract the question the agent just asked
    question_topic = ""
    for sentence in (assistant_msg or "").replace("?", "?.").split("."):
        s = sentence.strip()
        if s.endswith("?"):
            question_topic = s.rstrip("?").strip().lower()
            break

    if collected and question_topic:
        return f"Got: {collected}. Asking about {question_topic} to narrow down the right trailer."
    if collected:
        return f"Got: {collected}. Still gathering details before searching inventory."
    return "Still collecting customer requirements before running a search."


def _deterministic_thinking_flow(payload: dict[str, Any]) -> str:
    """Build a plain-text insights note from payload data."""
    stage = _stage_from_payload(payload)
    if stage == "qualification":
        return _deterministic_qualification_flow(payload)

    tool_runs = payload.get("tool_runs") or []
    last_run = tool_runs[-1] if tool_runs else {}
    search_attempts = (last_run or {}).get("search_attempts") or []
    rerank = (last_run or {}).get("rerank") or {}
    all_scores = rerank.get("all_scores") or []
    req_payload = last_run.get("required_payload_lbs")
    req_length = last_run.get("required_length_ft")
    req_gvwr = last_run.get("required_gvwr_lbs")
    trailer_filter = (last_run or {}).get("trailer_filter") or {}

    strict = [a for a in search_attempts if isinstance(a, dict) and a.get("phase") == "strict"]
    relaxed = [a for a in search_attempts if isinstance(a, dict) and a.get("phase") == "relaxed"]
    if strict:
        mc = strict[0].get("match_count", 0)
        search_note = f"Searched inventory with all filters — found {mc} trailer(s)."
    elif relaxed:
        mc = relaxed[0].get("match_count", 0)
        search_note = f"No strict matches — relaxed filters found {mc} trailer(s)."
    else:
        search_note = "Searched inventory."

    shown = sorted(
        [s for s in all_scores if s.get("decision_rank") is not None],
        key=lambda s: s.get("decision_rank") or 99,
    )
    if not shown:
        category = trailer_filter.get("category_subcategory") or trailer_filter.get("category") or ""
        hitch = trailer_filter.get("hitch_type") or ""
        specs = ", ".join(filter(None, [category, hitch]))
        suffix = f"No trailers matched {specs} well enough to recommend." if specs else "No strong matches found."
        return f"{search_note} {suffix}"

    parts = [search_note]
    for s in shown:
        rank = s.get("decision_rank")
        title = s.get("title") or "Unknown trailer"
        reason = _score_friendly_reason(s, req_payload, req_length, req_gvwr)
        parts.append(f"#{rank} {title} — {reason}.")
    return " ".join(parts)


def _recommendation_output_is_acceptable(text: str) -> bool:
    return bool(text and len(text.strip()) > 20)


def _thinking_prompt(payload: dict[str, Any]) -> tuple[str, str]:
    stage = _stage_from_payload(payload)
    tool_runs = payload.get("tool_runs") or []
    selected = payload.get("selected_recommendations") or []
    last_run = tool_runs[-1] if tool_runs else {}
    rerank = last_run.get("rerank") if isinstance(last_run, dict) else {}
    enriched_payload = {
        **payload,
        "current_stage": stage,
        "has_tool_run": bool(tool_runs),
        "has_rerank_scores": bool((rerank or {}).get("all_scores")),
        "has_selected_recommendations": bool(selected),
    }

    system = (
        "You write short plain-English insights for a trailer dealership owner reviewing their AI sales assistant. "
        "Write naturally — like a knowledgeable colleague explaining what just happened. "
        "No markdown, no headers, no bullet points, no field names or technical jargon. "
        "Never show ratios, penalties, or raw scores. Be specific and concise."
    )

    if stage == "qualification":
        user = (
            "In 1-2 natural sentences, explain: what useful information did we just collect from the customer, "
            "and what is the agent trying to find out next and why does it matter for picking the right trailer?\n"
            "Write it as a smooth insight, not a literal transcript. Example style: "
            "'Got the payload weight — 4,000 lbs of furniture. Now asking for preferred length to match the right trailer size.'\n"
            "If this is the very first message (customer just gave name/phone/email), return exactly: SKIP\n\n"
            f"PAYLOAD:\n{_safe_json(enriched_payload)}"
        )
    else:
        user = (
            "In plain text (no markdown, no bullets), write a short insight covering:\n"
            "1. How many trailers were found and whether strict or relaxed filters were used (one short sentence).\n"
            "2. For EACH shown trailer, one sentence: its name and why it fits — compare length, payload, or GVWR "
            "to what the customer needs in plain English (e.g. '24 ft — 4 ft longer than requested but well within payload').\n"
            "Keep the whole thing under 60 words. Never invent data not in the payload.\n\n"
            f"PAYLOAD:\n{_safe_json(enriched_payload)}"
        )
    return system, user


async def generate_thinking_flow_async(payload: dict[str, Any]) -> dict[str, Any]:
    if not THINKING_AGENT_ENABLED:
        return {
            "status": "disabled",
            "thinking_markdown": "",
            "model": THINKING_AGENT_MODEL,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    system, user = _thinking_prompt(payload)
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    try:
        resp = await client.chat.completions.create(
            model=THINKING_AGENT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip()
        # LLM signals first-turn skip, or output is unusable — fall back to deterministic
        if text.upper() == "SKIP" or not _recommendation_output_is_acceptable(text):
            text = _deterministic_thinking_flow(payload)
        return {
            "status": "ok",
            "thinking_markdown": text,
            "model": THINKING_AGENT_MODEL,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.exception("Thinking agent generation failed")
        deterministic_text = _deterministic_thinking_flow(payload)
        return {
            "status": "error",
            "thinking_markdown": deterministic_text,
            "error": str(exc),
            "model": THINKING_AGENT_MODEL,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


def generate_thinking_flow(payload: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(generate_thinking_flow_async(payload))


def log_thinking_flow(session_id: str, payload: dict[str, Any], result: dict[str, Any]) -> None:
    thinking_log.info(
        "THINKING_FLOW | session_id=%s | status=%s | model=%s | generated_at=%s\n%s",
        session_id,
        result.get("status"),
        result.get("model"),
        result.get("generated_at"),
        result.get("thinking_markdown") or result.get("error") or "",
    )
    # Keep a compact source envelope for auditing and replay.
    thinking_log.info(
        "THINKING_FLOW_SOURCE | session_id=%s | payload=%s",
        session_id,
        _safe_json(payload),
    )
