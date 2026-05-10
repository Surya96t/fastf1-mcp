import logging
import sys

from .config import settings


def setup_logging() -> logging.Logger:
    """Configure structured logging for the server. Idempotent."""

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)

    if not any(getattr(h, "_fastf1_mcp_handler", False) for h in root_logger.handlers):
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        console_handler._fastf1_mcp_handler = True  # type: ignore[attr-defined]
        root_logger.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("fastf1").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logger = logging.getLogger("fastf1_mcp")
    logger.setLevel(settings.log_level)

    return logger
