"""
MCP Resources for fastf1-mcp.

Resources expose reference data at well-known URIs. All resources
fetch from the Ergast/Jolpica API via FastF1 and return JSON strings.
"""

import asyncio
import json
import logging
import math
from functools import partial

import pandas as pd
from fastf1.ergast import Ergast

logger = logging.getLogger(__name__)


def _to_serialisable(obj):
    """Recursively convert non-JSON-serialisable values (Timestamps, NaN, etc.)."""
    if isinstance(obj, dict):
        return {k: _to_serialisable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_serialisable(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    # pandas Timestamp / datetime-like
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


async def _ergast_to_json(fn, **kwargs) -> str:
    """Run an Ergast call in executor and return JSON string."""
    loop = asyncio.get_running_loop()
    ergast = Ergast()
    result = await loop.run_in_executor(None, partial(fn, ergast, **kwargs))

    if hasattr(result, "content") and len(result.content) > 0:
        df = result.content[0]
    else:
        df = result

    records = _to_serialisable(df.to_dict(orient="records"))
    return json.dumps(records)


# ---------------------------------------------------------------------------
# Resource functions (registered onto `mcp` in server.py via mcp.resource())
# ---------------------------------------------------------------------------


async def schedule_resource(year: int) -> str:
    """
    F1 race calendar for a season.

    Returns JSON array of events with round, raceName, circuitName,
    country, and date fields.
    """
    logger.info(f"resource f1://schedule/{year}")
    return await _ergast_to_json(
        lambda e, **kw: e.get_race_schedule(**kw), season=year, limit=30
    )


async def drivers_resource(year: int) -> str:
    """
    Driver roster for a season.

    Returns JSON array of drivers with code, name, nationality,
    and permanent number fields.
    """
    logger.info(f"resource f1://drivers/{year}")
    return await _ergast_to_json(
        lambda e, **kw: e.get_driver_info(**kw), season=year, limit=1000
    )


async def constructors_resource(year: int) -> str:
    """
    Constructor list for a season.

    Returns JSON array of constructors with id, name, and nationality.
    """
    logger.info(f"resource f1://constructors/{year}")
    return await _ergast_to_json(
        lambda e, **kw: e.get_constructor_info(**kw), season=year, limit=1000
    )


async def circuits_resource() -> str:
    """
    All circuits that have hosted F1 races.

    Returns JSON array of circuits with id, name, locality,
    country, latitude, and longitude.
    """
    logger.info("resource f1://circuits")
    return await _ergast_to_json(lambda e, **kw: e.get_circuits(**kw), limit=100)
