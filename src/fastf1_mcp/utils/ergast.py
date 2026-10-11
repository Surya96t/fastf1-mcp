"""Shared helpers for calling the Ergast/Jolpica API from tool handlers."""

import asyncio
import logging
from collections.abc import Callable
from functools import partial
from typing import Any

import pandas as pd

from .errors import ErrorCode, FastF1MCPError

logger = logging.getLogger(__name__)


async def run_ergast(call: Callable[..., Any], *, what: str, **kwargs) -> Any:
    """
    Run a blocking Ergast call in an executor.

    Any failure (network, HTTP error, bad season) is re-raised as a
    FastF1MCPError(DATA_UNAVAILABLE) so `@tool_handler` turns it into the
    standard error dict instead of a raw exception reaching the client.

    Args:
        call: Bound Ergast method, e.g. `Ergast().get_race_schedule`.
        what: Human-readable description used in the error message,
              e.g. "schedule for 2024".
        **kwargs: Forwarded to `call`.
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, partial(call, **kwargs))
    except FastF1MCPError:
        raise
    except Exception as e:
        logger.error(f"Ergast request failed ({what}): {e}")
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"Could not fetch {what}: {e}",
            suggestions=[
                "Check that the season/round exists (Ergast covers 1950-present)",
                "The Ergast/Jolpica API may be temporarily unavailable — retry shortly",
            ],
        ) from e


def first_frame(result: Any) -> pd.DataFrame:
    """
    Return the first DataFrame from an Ergast response.

    Multi-response objects (`ErgastMultiResponse`) carry their data in
    `.content`; simple responses are DataFrames already. An empty
    multi-response yields an empty DataFrame.
    """
    if hasattr(result, "content"):
        if len(result.content) > 0:
            return result.content[0]
        return pd.DataFrame()
    return result
