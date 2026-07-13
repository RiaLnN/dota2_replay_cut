"""Configure application-wide logging handlers."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    """Initialize the root logger once and prevent duplicate handlers."""
    root = logging.getLogger()
    if getattr(root, "_dota_recorder_configured", False):
        return

    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger(__name__).warning("Не удалось создать файл лога: %s", exc)
    


    root._dota_recorder_configured = True  # type: ignore[attr-defined]