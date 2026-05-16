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


_PUBLIC_QUIET_NAMESPACES: tuple[str, ...] = (
    "deng.rejoin",
    "deng_tool_rejoin",
)


def _silence_namespace_to_file(name: str, file_handler: logging.Handler) -> None:
    """Attach the file handler to a logger namespace and stop propagation to root.

    This prevents Python's default ``lastResort`` stderr handler from emitting
    ``deng.rejoin.*`` warnings/errors to the public terminal.
    """
    lg = logging.getLogger(name)
    lg.propagate = False
    # Remove any pre-existing stream handlers that might leak to stderr/stdout.
    for h in list(lg.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(
            h, RotatingFileHandler
        ):
            lg.removeHandler(h)
    # Only add file handler once.
    if not any(isinstance(h, RotatingFileHandler) for h in lg.handlers):
        lg.addHandler(file_handler)


def configure_logging(log_path: Path = LOG_PATH, level: str = "INFO") -> logging.Logger:
    """Configure and return the DENG logger.

    Also silences the ``deng.rejoin.*`` logger namespace (used by window_layout,
    supervisor child modules, etc.) so their warnings/errors NEVER reach the
    public terminal via Python's lastResort handler.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("deng_tool_rejoin")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    formatter = DengFormatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%dT%H:%M:%S%z")

    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        file_handler = RotatingFileHandler(
            str(log_path), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    else:
        file_handler = next(
            h for h in logger.handlers if isinstance(h, RotatingFileHandler)
        )

    # Silence all internal namespaces so they only write to the file.
    for ns in _PUBLIC_QUIET_NAMESPACES:
        _silence_namespace_to_file(ns, file_handler)

    return logger


def silence_public_loggers() -> None:
    """Ensure internal ``deng.rejoin.*`` namespace never leaks to public stdout/stderr.

    Safe to call before any configure_logging() — uses a NullHandler placeholder
    so messages are simply dropped rather than emitted to stderr via lastResort.
    Called by every public entry point (cmd_start, main, etc.).
    """
    for ns in _PUBLIC_QUIET_NAMESPACES:
        lg = logging.getLogger(ns)
        lg.propagate = False
        # Drop any pre-existing stream handlers
        for h in list(lg.handlers):
            if isinstance(h, logging.StreamHandler) and not isinstance(
                h, RotatingFileHandler
            ):
                lg.removeHandler(h)
        if not lg.handlers:
            lg.addHandler(logging.NullHandler())


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
