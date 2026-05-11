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


# Substrings (lowercase) that, when present in a FastF1 ValueError, indicate
# the caller asked for a session type that the event doesn't have (e.g. a
# sprint at a non-sprint weekend) rather than a generic load failure.
_SESSION_NOT_HELD_HINTS = (
    "does not exist for this event",
    "is not a valid session",
    "is not part of this event",
    "not part of this event",
    "session does not exist",
    "no session with name",
    "no session of type",
)


def _is_session_not_held(exc: Exception) -> bool:
    if not isinstance(exc, ValueError):
        return False
    msg = str(exc).lower()
    return any(hint in msg for hint in _SESSION_NOT_HELD_HINTS)


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
        if _is_session_not_held(e):
            raise FastF1MCPError(
                ErrorCode.SESSION_NOT_HELD,
                f"Session '{session}' was not held at {year} {event}.",
                suggestions=[
                    "Verify the event format with list_events or get_schedule — "
                    "not every weekend has a sprint (S/SQ).",
                    "Valid session identifiers: R (race), Q (qualifying), "
                    "S (sprint), SQ (sprint qualifying), FP1, FP2, FP3.",
                ],
            ) from e
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
