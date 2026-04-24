"""
Per chat session, persist canonical listing keys already shown in the UI (for Pinecone
exclude-on-more without repeating cards).
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Iterable

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _store_dir() -> Path:
    base = (os.getenv("TRAILERPLACE_SHOWN_DIR") or "").strip()
    if base:
        return Path(base)
    return Path(__file__).resolve().parent.parent / "shown_listings"


def _path_for_session(session_id: str) -> Path:
    if not _UUID_RE.match((session_id or "").strip()):
        raise ValueError("Invalid session_id for shown_listings store")
    return _store_dir() / f"{session_id.strip()}.json"


def _get_lock(sid: str) -> threading.Lock:
    with _locks_guard:
        if sid not in _locks:
            _locks[sid] = threading.Lock()
        return _locks[sid]


def load_shown_keys(session_id: str) -> set[str]:
    if not (session_id or "").strip():
        return set()
    p = _path_for_session(session_id)
    if not p.is_file():
        return set()
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()
    keys = data.get("keys")
    if not isinstance(keys, list):
        return set()
    return {str(k) for k in keys if k}


def add_shown_keys(session_id: str, keys: Iterable[str]) -> None:
    to_add = {k for k in keys if k}
    if not to_add or not (session_id or "").strip():
        return
    _store_dir().mkdir(parents=True, exist_ok=True)
    sid = session_id.strip()
    with _get_lock(sid):
        p = _path_for_session(sid)
        current: set[str] = set()
        if p.is_file():
            try:
                with p.open(encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("keys")
                if isinstance(raw, list):
                    current = {str(k) for k in raw if k}
            except (json.JSONDecodeError, OSError):
                current = set()
        merged = current | to_add
        if merged == current:
            return
        tmp = p.with_suffix(p.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({"keys": sorted(merged)}, f, ensure_ascii=True, indent=0)
        tmp.replace(p)
