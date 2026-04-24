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


def _fmt_num(v: Any) -> str:
    if v is None:
        return "Not available in this turn"
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


def _score_justification(score: dict[str, Any]) -> str:
    dr = score.get("decision_rank")
    pr = score.get("payload_ratio")
    lr = score.get("length_ratio")
    fail_count = int(score.get("fail_count") or 0)
    missing_count = int(score.get("missing_count") or 0)

    reasons: list[str] = []
    if dr is None:
        reasons.append("it was not selected into ranked output (decision_rank=None)")
    else:
        reasons.append(f"it was placed at decision rank {dr}")

    if fail_count > 0:
        reasons.append(f"it failed {fail_count} required fit checks")
    if missing_count > 0:
        reasons.append(f"it is missing {missing_count} required dimensions")

    if pr is not None:
        try:
            p = float(pr)
            if p < 1.0:
                reasons.append("its payload ratio is below 1.0 (under required payload)")
            elif p == 1.0:
                reasons.append("its payload ratio is exactly 1.0 (exact payload target)")
            else:
                reasons.append("its payload ratio is above 1.0 (oversized payload vs need)")
        except Exception:
            pass

    if lr is not None:
        try:
            l = float(lr)
            if l < 1.0:
                reasons.append("its length ratio is below 1.0 (shorter than requested)")
            elif abs(l - 1.0) < 1e-9:
                reasons.append("its length ratio is 1.0 (exact requested length)")
            else:
                reasons.append("its length ratio is above 1.0 (longer than requested)")
        except Exception:
            pass

    return "; ".join(reasons)


def _deterministic_recommendation_flow(payload: dict[str, Any]) -> str:
    tool_runs = payload.get("tool_runs") or []
    last_run = tool_runs[-1] if tool_runs else {}
    search_attempts = (last_run or {}).get("search_attempts") or []
    rerank = (last_run or {}).get("rerank") or {}
    all_scores = rerank.get("all_scores") or []
    selected = payload.get("selected_recommendations") or []
    req_payload = last_run.get("required_payload_lbs")
    req_length = last_run.get("required_length_ft")
    req_source = last_run.get("requirements_source")

    lines: list[str] = [
        "# Thinking Flow",
        "",
        "## Current Step",
        "Recommendation",
        "",
        "## What Happened In This Turn",
        (
            f"The assistant executed inventory search and reranking for the user's request "
            f"(required_payload_lbs={_fmt_num(req_payload)}, required_length_ft={_fmt_num(req_length)}, "
            f"requirements_source={_fmt_num(req_source)})."
        ),
        "",
        "## Evidence Used In This Turn",
    ]

    strict_attempts = [a for a in search_attempts if isinstance(a, dict) and a.get("phase") == "strict"]
    relaxed_attempts = [a for a in search_attempts if isinstance(a, dict) and a.get("phase") == "relaxed"]
    if strict_attempts:
        s0 = strict_attempts[0]
        lines.append(
            f"- Pinecone strict filter: `{_fmt_num(s0.get('pinecone_filter'))}`; top_k={_fmt_num(s0.get('top_k'))}; match_count={_fmt_num(s0.get('match_count'))}."
        )
    if relaxed_attempts:
        r0 = relaxed_attempts[0]
        lines.append(
            f"- Pinecone relaxed filter: `{_fmt_num(r0.get('pinecone_filter'))}`; top_k={_fmt_num(r0.get('top_k'))}; match_count={_fmt_num(r0.get('match_count'))}."
        )
    lines.append(
        f"- Rerank phase: `{_fmt_num(rerank.get('phase'))}`; warn_ratio={_fmt_num(rerank.get('warn_ratio'))}; extreme_ratio={_fmt_num(rerank.get('extreme_ratio'))}."
    )

    if all_scores:
        lines.append("")
        lines.append("### Per Listing Rerank Justification")
        for s in all_scores:
            lines.append(
                "- "
                f"{_fmt_num(s.get('listing_id'))} | "
                f"decision_rank={_fmt_num(s.get('decision_rank'))}, "
                f"base_score={_fmt_num(s.get('base_score'))}, "
                f"penalty={_fmt_num(s.get('penalty'))}, "
                f"fit_score={_fmt_num(s.get('fit_score'))}, "
                f"payload_ratio={_fmt_num(s.get('payload_ratio'))}, "
                f"length_ratio={_fmt_num(s.get('length_ratio'))}, "
                f"fail_count={_fmt_num(s.get('fail_count'))}, "
                f"missing_count={_fmt_num(s.get('missing_count'))}. "
                f"Reason: {_score_justification(s)}."
            )
    else:
        lines.append("- Rerank evidence is not available in this turn.")

    if selected:
        lines.append("")
        lines.append("### Selected Recommendations (Final)")
        for row in selected:
            lines.append(
                "- "
                f"rank={_fmt_num(row.get('rank'))} | "
                f"{_fmt_num(row.get('title'))} | "
                f"length={_fmt_num(row.get('length'))} | "
                f"payload_capacity={_fmt_num(row.get('payload_capacity'))} | "
                f"gvwr={_fmt_num(row.get('gvwr'))}."
            )

    return "\n".join(lines)


def _deterministic_nonsearch_flow(payload: dict[str, Any], stage: str) -> str:
    user_msg = str(payload.get("user_message") or "").strip()
    assistant_msg = str(payload.get("assistant_message") or "").strip()
    stage_name = "Qualification" if stage == "qualification" else "Search"
    return "\n".join(
        [
            "# Thinking Flow",
            "",
            "## Current Step",
            stage_name,
            "",
            "## What Happened In This Turn",
            (
                "The assistant handled only the current conversational step and did not complete recommendation ranking yet."
                if stage == "qualification"
                else "The assistant completed search-stage actions for this turn."
            ),
            "",
            "## Evidence Used In This Turn",
            f"- User message: {user_msg or 'Not available in this turn'}",
            f"- Assistant message: {assistant_msg or 'Not available in this turn'}",
        ]
    )


def _deterministic_thinking_flow(payload: dict[str, Any]) -> str:
    stage = _stage_from_payload(payload)
    if stage in ("recommendation", "reranking"):
        return _deterministic_recommendation_flow(payload)
    return _deterministic_nonsearch_flow(payload, stage)


def _recommendation_output_is_acceptable(text: str, payload: dict[str, Any]) -> bool:
    stage = _stage_from_payload(payload)
    if stage not in ("recommendation", "reranking"):
        return True
    # Minimum required anchors for recommendation-stage explanation.
    must_have = [
        "Pinecone strict filter",
        "Rerank phase",
        "decision_rank=",
        "fit_score=",
        "Reason:",
    ]
    return all(token in text for token in must_have)


def _thinking_prompt(payload: dict[str, Any]) -> tuple[str, str]:
    stage = _stage_from_payload(payload)
    tool_runs = payload.get("tool_runs") or []
    selected = payload.get("selected_recommendations") or []
    last_run = tool_runs[-1] if tool_runs else {}
    rerank = last_run.get("rerank") if isinstance(last_run, dict) else {}
    has_rerank_scores = bool((rerank or {}).get("all_scores"))
    enriched_payload = {
        **payload,
        "current_stage": stage,
        "has_tool_run": bool(tool_runs),
        "has_rerank_scores": has_rerank_scores,
        "has_selected_recommendations": bool(selected),
    }

    system = (
        "You are a transparent step-by-step explainer for a trailer-sales chatbot.\n"
        "Write only what happened in the CURRENT turn and CURRENT stage.\n"
        "Never speculate, never invent numbers, never describe future steps that did not happen yet.\n"
        "If a value is missing in payload, explicitly write 'Not available in this turn'.\n"
        "Do not use placeholders like Listing A/B/C."
    )
    user = (
        "Create a markdown report titled 'Thinking Flow'.\n"
        "The report must be ONLY about this turn and must stay concise.\n\n"
        "Required sections:\n"
        "1) Current Step\n"
        "2) What Happened In This Turn\n"
        "3) Evidence Used In This Turn\n\n"
        "Rules by stage:\n"
        "- If current_stage=qualification: do NOT mention Pinecone search/rerank/recommendations as completed actions.\n"
        "- If current_stage=search: mention Pinecone filter/query result count only.\n"
        "- If current_stage=reranking or recommendation and rerank scores exist: include one bullet per fetched listing with exact fields: decision_rank, base_score, penalty, fit_score, payload_ratio, length_ratio, fail_count, missing_count.\n"
        "- If rerank scores do not exist: explicitly say rerank evidence is not available in this turn.\n"
        "- Keep it 6-18 lines total. No fabricated examples.\n\n"
        f"INPUT_PAYLOAD_JSON:\n{_safe_json(enriched_payload)}"
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
        if not _recommendation_output_is_acceptable(text, payload):
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
