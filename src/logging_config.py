"""
Shared logging setup. Import and call `setup_logging()` once at app/script entrypoint
(e.g. top of src/serving/app.py, or the __main__ block of training scripts) so every
module logs consistently.
"""

import logging
import sys

from config.settings import settings


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)

    # Avoid duplicate handlers if setup_logging() gets called more than once
    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
