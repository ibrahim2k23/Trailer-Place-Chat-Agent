import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional


class DailyDateFileHandler(logging.Handler):
    """
    A simple daily log handler that writes to logs/YYYY-MM-DD.log.
    When the date changes, it automatically closes the old file and opens a new one.
    """

    def __init__(self, log_dir: str = "logs", level: int = logging.INFO):
        super().__init__(level=level)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: date = date.today()
        self._stream: Optional[object] = None
        self._path: Optional[Path] = None
        self._open_for_today()

    def _today_path(self) -> Path:
        return self.log_dir / f"{date.today().isoformat()}.log"

    def _open_for_today(self) -> None:
        self._current_date = date.today()
        self._path = self._today_path()
        # Line-buffered text file for realtime tailing.
        self._stream = open(self._path, "a", encoding="utf-8", buffering=1)

    def _maybe_rotate(self) -> None:
        if date.today() != self._current_date:
            try:
                if self._stream:
                    self._stream.close()
            finally:
                self._open_for_today()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._maybe_rotate()
            msg = self.format(record)
            if self._stream:
                self._stream.write(msg + "\n")
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._stream:
                self._stream.close()
        finally:
            self._stream = None
            super().close()


def get_logger(name: str = "trailer") -> logging.Logger:
    """
    Create/reuse a logger configured to write to:
    - logs/YYYY-MM-DD.log
    - stdout (so Streamlit logs also show in Cloud logs)
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = DailyDateFileHandler(level=level)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

    logger.info("Logger initialised (level=%s)", level_name)
    return logger

