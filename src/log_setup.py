"""
TrailerPlace logging: console + one file per day under `log/YYYY-MM-DD.log`.
Re-opens the file when the calendar day changes. Idempotent for repeated imports.
"""
import logging
import os
import threading
from datetime import date
from typing import Optional


class DailyDateFileHandler(logging.Handler):
    """
    Appends to log/<date>.log; on each emit, switches the output file if the
    system date has rolled over (so long-running processes get a new file at midnight).
    """

    def __init__(self, log_dir: str) -> None:
        super().__init__()
        self.log_dir = log_dir
        self._current: Optional[date] = None
        self._stream = None
        self._lock = threading.Lock()
        os.makedirs(self.log_dir, exist_ok=True)

    def _path_for(self, d: date) -> str:
        return os.path.join(self.log_dir, f"{d.isoformat()}.log")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            d = date.today()
            with self._lock:
                if d != self._current or self._stream is None:
                    if self._stream is not None:
                        self._stream.close()
                    self._current = d
                    self._stream = open(self._path_for(d), "a", encoding="utf-8")
                msg = self.format(record)
                self._stream.write(msg + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_configured = False


class DailyDateThinkingFileHandler(DailyDateFileHandler):
    """Appends to thinking_log/<date>-thinking.log."""

    def _path_for(self, d: date) -> str:
        return os.path.join(self.log_dir, f"{d.isoformat()}-thinking.log")


def _default_log_dir() -> str:
    # This file: .../project/src/src/log_setup.py → project root = parent of inner `src`
    this = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(this), "log")


def _default_thinking_log_dir() -> str:
    this = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(this), "thinking_log")


def configure_trailerplace_logging() -> str:
    """
    Attach console + daily log file. Safe to call multiple times.
    Returns the resolved log directory.
    """
    global _configured
    log_dir = (os.getenv("TRAILERPLACE_LOG_DIR") or _default_log_dir()).strip() or _default_log_dir()
    if _configured:
        return log_dir

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format=_LOG_FORMAT,
            datefmt=_DATE_FORMAT,
        )
    else:
        root.setLevel(logging.INFO)
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and h.formatter is None:
                h.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))

    if not any(isinstance(h, DailyDateFileHandler) for h in root.handlers):
        file_handler = DailyDateFileHandler(log_dir)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(file_handler)

    # Structured product fetches: one JSON line per fetch attempt
    product_fetch = logging.getLogger("trailerplace.product_fetch")
    product_fetch.setLevel(logging.INFO)
    product_fetch.propagate = True

    # Human-readable turn-level thinking logs in separate files.
    thinking_dir = (
        os.getenv("TRAILERPLACE_THINKING_LOG_DIR") or _default_thinking_log_dir()
    ).strip() or _default_thinking_log_dir()
    thinking_logger = logging.getLogger("trailerplace.thinking")
    thinking_logger.setLevel(logging.INFO)
    thinking_logger.propagate = False
    if not any(isinstance(h, DailyDateThinkingFileHandler) for h in thinking_logger.handlers):
        thinking_handler = DailyDateThinkingFileHandler(thinking_dir)
        thinking_handler.setLevel(logging.INFO)
        thinking_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        thinking_logger.addHandler(thinking_handler)

    _configured = True
    return log_dir
