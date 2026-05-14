"""
logger.py
=========
Configures the application-wide logging infrastructure.
Supports both console and rotating file handlers.
"""

import logging
import logging.handlers
import os
from pathlib import Path


def setup_logger(
    name: str = "nifty_bot",
    level: str = "INFO",
    log_to_file: bool = True,
    log_dir: str = "logs",
    max_bytes: int = 10_485_760,
    backup_count: int = 5,
) -> logging.Logger:
    """
    Set up and return a named logger with console + rotating file handler.

    Parameters
    ----------
    name         : Logger / application name
    level        : Logging level string (DEBUG, INFO, WARNING, ERROR)
    log_to_file  : Whether to write logs to a rotating file
    log_dir      : Directory for log files (created if absent)
    max_bytes    : Max bytes per log file before rotation
    backup_count : Number of rotated files to keep
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger(name)
    root.setLevel(numeric_level)
    root.handlers.clear()

    # ── Console handler ──────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(numeric_level)
    root.addHandler(ch)

    # ── Rotating file handler ────────────────────────────────
    if log_to_file:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_file = os.path.join(log_dir, f"{name}.log")
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        fh.setLevel(numeric_level)
        root.addHandler(fh)

    return root


def get_logger(module_name: str) -> logging.Logger:
    """Return a child logger for the given module name."""
    return logging.getLogger(f"nifty_bot.{module_name}")
