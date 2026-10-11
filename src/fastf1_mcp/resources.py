"""
MCP Resources for fastf1-mcp.

Resources expose reference data at well-known URIs. All resources
fetch from the Ergast/Jolpica API via FastF1 and return JSON strings.
"""

import json
import logging

from fastf1.ergast import Ergast

from .utils.converters import records_to_json
from .utils.ergast import first_frame, run_ergast

logger = logging.getLogger(__name__)


async def _ergast_to_json(fn, *, what: str, **kwargs) -> str:
    """Run an Ergast call in executor and return a strict-JSON string."""
    ergast = Ergast()
    result = await run_ergast(lambda **kw: fn(ergast, **kw), what=what, **kwargs)
    return json.dumps(records_to_json(first_frame(result)), allow_nan=False)


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
        lambda e, **kw: e.get_race_schedule(**kw),
        what=f"schedule for {year}",
        season=year,
        limit=30,
    )


async def drivers_resource(year: int) -> str:
    """
    Driver roster for a season.

    Returns JSON array of drivers with code, name, nationality,
    and permanent number fields.
    """
    logger.info(f"resource f1://drivers/{year}")
    return await _ergast_to_json(
        lambda e, **kw: e.get_driver_info(**kw),
        what=f"drivers for {year}",
        season=year,
        limit=1000,
    )


async def constructors_resource(year: int) -> str:
    """
    Constructor list for a season.

    Returns JSON array of constructors with id, name, and nationality.
    """
    logger.info(f"resource f1://constructors/{year}")
    return await _ergast_to_json(
        lambda e, **kw: e.get_constructor_info(**kw),
        what=f"constructors for {year}",
        season=year,
        limit=1000,
    )


async def circuits_resource() -> str:
    """
    All circuits that have hosted F1 races.

    Returns JSON array of circuits with id, name, locality,
    country, latitude, and longitude.
    """
    logger.info("resource f1://circuits")
    return await _ergast_to_json(
        lambda e, **kw: e.get_circuits(**kw), what="circuits", limit=100
    )
