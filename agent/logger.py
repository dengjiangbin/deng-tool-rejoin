"""Readable local logging with URL masking."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .constants import LOG_BACKUP_COUNT, LOG_MAX_BYTES, LOG_PATH
from .url_utils import mask_urls_in_text


def _safe_value(value: Any) -> str:
    if value is None:
        return "none"
    text = str(value)
    text = mask_urls_in_text(text)
    return text.replace("\n", " ").replace("\r", " ")


class DengFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        return base


def configure_logging(log_path: Path = LOG_PATH, level: str = "INFO") -> logging.Logger:
    """Configure and return the DENG logger."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("deng_tool_rejoin")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = DengFormatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%dT%H:%M:%S%z")

    file_handler = RotatingFileHandler(str(log_path), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def log_event(logger: logging.Logger, level: str, event_type: str, message: str = "", **fields: Any) -> None:
    """Write a structured one-line event to the local log."""
    parts = [event_type]
    if message:
        parts.append(_safe_value(message))
    for key in sorted(fields):
        value = _safe_value(fields[key])
        if " " in value or value == "":
            value = f'"{value}"'
        parts.append(f"{key}={value}")
    line = " ".join(parts)
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(line)
