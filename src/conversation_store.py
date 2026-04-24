"""
Append-only chat turns to Supabase/Postgres (conversation_history).
Writes run in a thread pool so the Streamlit request path is not blocked.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv()

logger = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tp_conv")
_engine: Optional[Engine] = None
_engine_lock = threading.Lock()

_DEFAULT_URL_NAMES = (
    "DATABASE_URL",
    "TRAILERPLACE_DATABASE_URL",
    "SUPABASE_DB_URL",
)


def _get_database_url() -> Optional[str]:
    for n in _DEFAULT_URL_NAMES:
        u = (os.getenv(n) or "").strip()
        if u:
            if u.startswith("postgres://"):
                u = u.replace("postgres://", "postgresql+psycopg://", 1)
            elif u.startswith("postgresql://") and "+" not in u.split("://", 1)[0]:
                u = u.replace("postgresql://", "postgresql+psycopg://", 1)
            return u
    host = (os.getenv("SUPABASE_DB_HOST") or "").strip()
    user = (os.getenv("SUPABASE_DB_USER") or os.getenv("PGUSER") or "").strip()
    pw = os.getenv("SUPABASE_DB_PASSWORD") or os.getenv("PGPASSWORD")
    port = (os.getenv("SUPABASE_DB_PORT") or "5432").strip()
    db = (os.getenv("SUPABASE_DB_NAME") or os.getenv("PGDATABASE") or "postgres").strip()
    if host and user and pw is not None:
        from urllib.parse import quote_plus
        return (
            f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(pw)}"
            f"@{host}:{port}/{db}?sslmode=require"
        )
    return None


def get_engine() -> Optional[Engine]:
    global _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        url = _get_database_url()
        if not url:
            return None
        _engine = create_engine(url, pool_pre_ping=True)
        return _engine


def persistence_enabled() -> bool:
    if (os.getenv("TRAILERPLACE_PERSIST_CHATS") or "1").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return False
    return get_engine() is not None


def _upsert_turn(
    session_id: str,
    turn: dict[str, Any],
    tool_call: Any,
    tool_result: Any,
    update_tool: bool,
) -> None:
    eng = get_engine()
    if eng is None:
        return
    turn_s = json.dumps(turn, ensure_ascii=False, default=str)
    tc = json.dumps(tool_call, ensure_ascii=False, default=str) if tool_call is not None else "null"
    tr = json.dumps(tool_result, ensure_ascii=False, default=str) if tool_result is not None else "null"

    stmt = text(
        """
        INSERT INTO conversation_history (session_id, messages, tool_call, tool_call_result, updated_at)
        VALUES (
            CAST(:session_id AS uuid),
            jsonb_build_array(CAST(:turn AS jsonb)),
            CASE WHEN :update_tool THEN CAST(:tool_call AS jsonb) ELSE NULL END,
            CASE WHEN :update_tool THEN CAST(:tool_res AS jsonb) ELSE NULL END,
            now()
        )
        ON CONFLICT (session_id) DO UPDATE SET
            messages = conversation_history.messages
                || jsonb_build_array(CAST(:turn AS jsonb)),
            tool_call = CASE
                WHEN :update_tool THEN CAST(:tool_call AS jsonb)
                ELSE conversation_history.tool_call
            END,
            tool_call_result = CASE
                WHEN :update_tool THEN CAST(:tool_res AS jsonb)
                ELSE conversation_history.tool_call_result
            END,
            updated_at = now()
        """
    )
    with eng.connect() as conn:
        conn.execute(
            stmt,
            {
                "session_id": session_id,
                "turn": turn_s,
                "tool_call": tc,
                "tool_res": tr,
                "update_tool": update_tool,
            },
        )
        conn.commit()


def save_turn(
    session_id: str,
    user_text: str,
    assistant_text: str,
    tool_call: Any,
    tool_result: Any,
    search_runs: Any = None,
) -> None:
    if not session_id or not persistence_enabled():
        return
    turn: dict[str, Any] = {
        "at": _utc_now_iso(),
        "user": user_text,
        "assistant": assistant_text,
    }
    if tool_call is not None:
        turn["tool_call"] = tool_call
    if tool_result is not None:
        turn["tool_call_result"] = tool_result
    if search_runs is not None:
        turn["search_runs"] = search_runs

    has_tool = tool_call is not None or tool_result is not None
    try:
        _upsert_turn(session_id, turn, tool_call, tool_result, has_tool)
    except Exception:
        logger.exception("conversation_history save failed (session_id=%s)", session_id)


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def enqueue_save_turn(
    session_id: str,
    user_text: str,
    assistant_text: str,
    tool_call: Any,
    tool_result: Any,
    search_runs: Any = None,
) -> None:
    if not session_id or not persistence_enabled():
        return
    _pool.submit(save_turn, session_id, user_text, assistant_text, tool_call, tool_result, search_runs)


def get_messages_for_session(session_id: str) -> Optional[list]:
    eng = get_engine()
    if eng is None:
        return None
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT messages FROM conversation_history "
                    "WHERE session_id = CAST(:sid AS uuid) LIMIT 1"
                ),
                {"sid": session_id},
            ).mappings().first()
            if not row or row.get("messages") is None:
                return None
            m = row["messages"]
            if isinstance(m, list):
                return m
            if isinstance(m, str):
                return json.loads(m)
            return list(m) if m is not None else None
    except Exception:
        logger.exception("get_messages_for_session failed (session_id=%s)", session_id)
        return None


def _patch_turn_feedback(
    session_id: str, turn_index: int, feedback_text: str, feedback_at: str
) -> None:
    eng = get_engine()
    if eng is None:
        return
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT messages FROM conversation_history "
                "WHERE session_id = CAST(:sid AS uuid) LIMIT 1"
            ),
            {"sid": session_id},
        ).mappings().first()
        if not row:
            logger.warning("patch_turn_feedback: no row for session_id=%s", session_id)
            return
        messages = row["messages"]
        if isinstance(messages, str):
            messages = json.loads(messages)
        if not isinstance(messages, list) or turn_index < 0 or turn_index >= len(messages):
            logger.warning(
                "patch_turn_feedback: bad turn_index %s (len=%s) session_id=%s",
                turn_index,
                len(messages) if isinstance(messages, list) else "?",
                session_id,
            )
            return
        turn = messages[turn_index]
        if not isinstance(turn, dict):
            return
        if feedback_text:
            turn["user_feedback"] = {"text": feedback_text, "at": feedback_at}
        else:
            turn.pop("user_feedback", None)
        messages_json = json.dumps(messages, ensure_ascii=False)
        if feedback_text:
            log_entry = {
                "turn_index": turn_index,
                "text": feedback_text,
                "at": feedback_at,
            }
            log_json = json.dumps([log_entry], ensure_ascii=False)
            try:
                conn.execute(
                    text(
                        """
                        UPDATE conversation_history
                        SET messages = CAST(:messages AS jsonb),
                            response_feedback = COALESCE(response_feedback, '[]'::jsonb)
                                || CAST(:add AS jsonb),
                            updated_at = now()
                        WHERE session_id = CAST(:sid AS uuid)
                        """
                    ),
                    {"messages": messages_json, "add": log_json, "sid": session_id},
                )
            except Exception:
                conn.rollback()
                conn.execute(
                    text(
                        """
                        UPDATE conversation_history
                        SET messages = CAST(:messages AS jsonb),
                            updated_at = now()
                        WHERE session_id = CAST(:sid AS uuid)
                        """
                    ),
                    {"messages": messages_json, "sid": session_id},
                )
        else:
            conn.execute(
                text(
                    """
                    UPDATE conversation_history
                    SET messages = CAST(:messages AS jsonb), updated_at = now()
                    WHERE session_id = CAST(:sid AS uuid)
                    """
                ),
                {"messages": messages_json, "sid": session_id},
            )
        conn.commit()


def save_user_feedback(
    session_id: str, turn_index: int, feedback_text: str, feedback_at: str
) -> None:
    if not session_id or not persistence_enabled():
        return
    try:
        _patch_turn_feedback(
            session_id, turn_index, (feedback_text or "").strip(), feedback_at
        )
    except Exception:
        logger.exception("save_user_feedback failed (session_id=%s turn=%s)", session_id, turn_index)


def enqueue_save_user_feedback(
    session_id: str, turn_index: int, feedback_text: str, feedback_at: str
) -> None:
    if not session_id or not persistence_enabled():
        return
    _pool.submit(
        save_user_feedback, session_id, turn_index, feedback_text, feedback_at
    )
