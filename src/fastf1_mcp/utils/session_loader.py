"""Shared helpers for loading FastF1 sessions inside tool handlers."""

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from ..session_manager import session_manager
from .errors import ErrorCode, FastF1MCPError

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


async def require_session(
    year: int,
    event: str | int,
    session: str,
    *,
    with_telemetry: bool = False,
    load_laps: bool = True,
    min_year: int = 2018,
):
    """Load a FastF1 session or raise FastF1MCPError on failure / out-of-range year."""
    if not isinstance(year, int) or year < min_year:
        raise FastF1MCPError(
            ErrorCode.YEAR_OUT_OF_RANGE,
            f"FastF1 session data requires {min_year}+, got {year}",
            suggestions=[f"Use get_race_results_historical for pre-{min_year} data"],
        )
    try:
        if with_telemetry:
            return await session_manager.get_session_with_telemetry(
                year, event, session
            )
        return await session_manager.get_session(
            year, event, session, load_laps=load_laps
        )
    except FastF1MCPError:
        raise
    except Exception as e:
        logger.error(f"Failed to load session {year} {event} {session}: {e}")
        raise FastF1MCPError(
            ErrorCode.SESSION_NOT_FOUND,
            f"Could not load session: {year} {event} {session}. {e}",
            suggestions=["Use list_events(year) to see valid event names"],
        ) from e


def tool_handler(func: Callable[P, Awaitable[Any]]) -> Callable[P, Awaitable[Any]]:
    """Convert any FastF1MCPError raised inside the tool into the standard error dict."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs):
        try:
            return await func(*args, **kwargs)
        except FastF1MCPError as e:
            return e.to_dict()

    return wrapper
