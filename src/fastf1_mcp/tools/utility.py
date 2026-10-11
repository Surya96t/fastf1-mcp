import asyncio
import logging

from fastf1.ergast import Ergast

from ..session_manager import session_manager
from ..utils.converters import records_to_json, to_jsonable
from ..utils.ergast import first_frame, run_ergast
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.session_loader import require_session, tool_handler

logger = logging.getLogger(__name__)


@tool_handler
async def list_events(year: int) -> list[dict]:
    """
    List all events in a season.

    Data source: Ergast API (via FastF1)
    Coverage: 1950-present

    Args:
        year: Season year (1950-present)

    Returns:
        Events with: round, eventName, country, circuitName, date

    Example:
        list_events(2024) → [
            {"round": 1, "eventName": "Bahrain Grand Prix",
             "country": "Bahrain", "circuitName": "Bahrain International Circuit",
             "date": "2024-03-02"},
            ...
        ]

    Note:
        Minimal version of get_schedule — useful for discovering valid
        event names to pass to other tools.
    """
    logger.info(f"list_events: {year=}")
    schedule = await run_ergast(
        Ergast().get_race_schedule,
        what=f"schedule for {year}",
        season=year,
        limit=30,
    )

    return [
        {
            "round": r.get("round"),
            "eventName": r.get("raceName"),
            "country": r.get("country"),
            "circuitName": r.get("circuitName"),
            "date": str(r["raceDate"])[:10] if r.get("raceDate") else None,
        }
        for r in records_to_json(first_frame(schedule))
    ]


@tool_handler
async def list_drivers(
    year: int,
    event: str | int | None = None,
) -> list[dict]:
    """
    List all drivers in a season, optionally filtered to a specific event.

    Data source: Ergast API (season list) or FastF1 session (event filter)
    Coverage: 1950-present (season); 2018-present (event filter)

    Args:
        year: Season year
        event: Optional race name or round number to filter by event
               (returns only drivers who participated in that session)

    Returns:
        Drivers with: code, fullName, nationality, team, number

    Example:
        list_drivers(2024) → [
            {"code": "VER", "fullName": "Max Verstappen",
             "nationality": "Dutch", "team": "Red Bull Racing", "number": "1"},
            ...
        ]

    Note:
        When event is provided, data comes from FastF1 session results
        (requires year >= 2018). Without event, uses Ergast season data.
    """
    logger.info(f"list_drivers: {year=}, {event=}")

    if event is not None:
        if year < 2018:
            raise FastF1MCPError(
                ErrorCode.YEAR_OUT_OF_RANGE,
                f"Event-filtered driver list requires 2018+, got {year}",
                suggestions=["Omit the event parameter for pre-2018 seasons"],
            )

        session_obj = await require_session(year, event, "R", load_laps=False)
        results = session_obj.results
        drivers = []
        for _, row in results.iterrows():
            drivers.append(
                {
                    "code": row.get("Abbreviation", ""),
                    "fullName": row.get("FullName", ""),
                    "nationality": row.get("CountryCode", ""),
                    "team": row.get("TeamName", ""),
                    "number": str(row.get("DriverNumber", "")),
                }
            )
        return to_jsonable(drivers)

    # Season-level: use Ergast. Driver info gives biographical fields;
    # driver standings gives the team mapping. Fetch both in parallel and
    # merge so the output includes the team a driver actually raced for.
    ergast = Ergast()
    info_result, standings_result = await asyncio.gather(
        run_ergast(
            ergast.get_driver_info,
            what=f"driver list for {year}",
            season=year,
            limit=1000,
        ),
        run_ergast(
            ergast.get_driver_standings,
            what=f"driver standings for {year}",
            season=year,
        ),
        return_exceptions=True,
    )

    # Driver info is required; standings only add the team column.
    if isinstance(info_result, BaseException):
        raise info_result

    info_df = first_frame(info_result)

    # Build code → team lookup from standings; tolerate a standings failure.
    team_by_code: dict[str, str] = {}
    if not isinstance(standings_result, Exception):
        if hasattr(standings_result, "content") and len(standings_result.content) > 0:
            standings_df = standings_result.content[0]
            for _, row in standings_df.iterrows():
                code = row.get("driverCode", "") or ""
                if not code:
                    continue
                names = row.get("constructorNames", [])
                if isinstance(names, list) and names:
                    team_by_code[code] = names[0]
    else:
        logger.warning(
            f"Driver standings unavailable for {year}: {standings_result}. "
            "Returning driver list without team info."
        )

    drivers = []
    for row in records_to_json(info_df):
        code = row.get("driverCode") or ""
        number = row.get("permanentNumber")
        drivers.append(
            {
                "code": code,
                "fullName": f"{row.get('givenName') or ''} "
                f"{row.get('familyName') or ''}".strip(),
                "nationality": row.get("driverNationality") or "",
                "team": team_by_code.get(code, "") if code else "",
                "number": str(number) if number is not None else "",
            }
        )
    return drivers


@tool_handler
async def get_cache_status() -> dict:
    """
    Check server in-memory session cache status.

    Returns:
        {
            "sessions_cached": 3,
            "max_sessions": 10,
            "cached_sessions": [
                {"year": 2024, "event": "Monaco", "session": "R",
                 "loaded_at": "2024-05-26T14:00:00"},
                ...
            ],
            "fastf1_cache_path": "~/.fastf1_cache",
            "fastf1_cache_size_mb": 1234.5
        }

    Example:
        get_cache_status() → {"sessions_cached": 2, "max_sessions": 10, ...}

    Note:
        Reports in-memory LRU cache only. The FastF1 disk cache
        (used for raw timing data) is reported separately as size_mb.
    """
    logger.info("get_cache_status called")

    status = session_manager.get_cache_status()

    from ..config import settings

    cache_path = settings.fastf1_cache_path
    status["fastf1_cache_path"] = str(cache_path)

    if cache_path.exists():
        # Recursive scan can take seconds on multi-GB caches; run in executor
        # so we don't block the event loop.
        loop = asyncio.get_running_loop()
        total_size = await loop.run_in_executor(None, _disk_cache_size, cache_path)
        status["fastf1_cache_size_mb"] = round(total_size / (1024 * 1024), 2)
    else:
        status["fastf1_cache_size_mb"] = 0.0

    return status


def _disk_cache_size(path) -> int:
    """Sum file sizes under path. Blocking; run in an executor."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


@tool_handler
async def clear_cache(
    year: int | None = None,
    event: str | None = None,
) -> dict:
    """
    Clear cached sessions from in-memory storage.

    Args:
        year: Optional year filter — only clear sessions for this year
        event: Optional event filter — requires year to be set

    Returns:
        {"cleared": 3, "remaining": 2}

    Example:
        clear_cache() → {"cleared": 5, "remaining": 0}
        clear_cache(2024, "Monaco") → {"cleared": 1, "remaining": 4}

    Note:
        Clears the in-memory LRU cache only. The FastF1 disk cache
        (raw timing files) is preserved and unaffected.
    """
    logger.info(f"clear_cache: {year=}, {event=}")

    if event is not None and year is None:
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER,
            "clear_cache: event filter requires year to be set",
            suggestions=[
                "Pass year alongside event, e.g. clear_cache(2024, 'Monaco')",
            ],
        )

    cleared = session_manager.clear_cache(year, event)
    remaining = len(session_manager._cache)

    logger.info(f"Cache cleared: {cleared} sessions removed, {remaining} remaining")
    return {"cleared": cleared, "remaining": remaining}
