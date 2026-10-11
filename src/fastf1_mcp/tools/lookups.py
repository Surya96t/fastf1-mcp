import logging

import pandas as pd
from fastf1.ergast import Ergast

from ..utils.converters import records_to_json, standings_to_json, to_jsonable
from ..utils.ergast import first_frame, run_ergast
from ..utils.session_loader import tool_handler

logger = logging.getLogger(__name__)


@tool_handler
async def get_schedule(year: int) -> list[dict]:
    """
    Get the F1 race calendar for a season.

    Data source: Ergast API (via FastF1)
    Coverage: 1950-present

    Args:
        year: Season year (1950-present)

    Returns:
        List of events with: round, raceName, circuitName, country,
        date, time (if available)

    Example:
        get_schedule(2024) → [
            {"round": 1, "raceName": "Bahrain Grand Prix", ...},
            ...
        ]
    """
    logger.info(f"get_schedule: {year=}")
    schedule = await run_ergast(
        Ergast().get_race_schedule,
        what=f"schedule for {year}",
        season=year,
        limit=30,
    )

    return [
        {
            "round": r.get("round"),
            "raceName": r.get("raceName"),
            "circuitName": r.get("circuitName"),
            "country": r.get("country"),
            "date": _date_str(r.get("raceDate")),
        }
        for r in records_to_json(first_frame(schedule))
    ]


@tool_handler
async def get_driver_standings(
    year: int,
    after_round: int | None = None,
) -> list[dict]:
    """
    Get driver championship standings.

    Data source: Ergast API (via FastF1)
    Coverage: 1950-present

    Args:
        year: Season year
        after_round: Standings after specific round (default: latest)

    Returns:
        Ordered list of drivers with: position, driver code, full name,
        team, points, wins

    Example:
        get_driver_standings(2024) → [
            {"position": 1, "code": "VER", "name": "Max Verstappen",
             "team": "Red Bull", "points": 575, "wins": 19},
            ...
        ]
    """
    logger.info(f"get_driver_standings: {year=}, {after_round=}")
    standings = await run_ergast(
        Ergast().get_driver_standings,
        what=f"driver standings for {year}",
        season=year,
        round=after_round,
    )

    return standings_to_json(standings, "driver")


@tool_handler
async def get_constructor_standings(
    year: int,
    after_round: int | None = None,
) -> list[dict]:
    """
    Get constructor championship standings.

    Data source: Ergast API (via FastF1)
    Coverage: 1958-present (constructor championship started 1958)

    Args:
        year: Season year
        after_round: Standings after specific round (default: latest)

    Returns:
        Ordered list of constructors with: position, name, nationality,
        points, wins
    """
    logger.info(f"get_constructor_standings: {year=}, {after_round=}")
    standings = await run_ergast(
        Ergast().get_constructor_standings,
        what=f"constructor standings for {year}",
        season=year,
        round=after_round,
    )

    return standings_to_json(standings, "constructor")


@tool_handler
async def get_driver_info(
    driver_id: str | None = None,
    year: int | None = None,
) -> list[dict]:
    """
    Get driver information.

    Data source: Ergast API (via FastF1)
    Coverage: 1950-present

    Args:
        driver_id: Ergast driver ID (e.g., "max_verstappen", "hamilton")
                   If None, returns all drivers
        year: Filter to drivers who raced in this season

    Returns:
        Driver info: driverId, code, givenName, familyName, dateOfBirth,
        nationality, permanentNumber

    Note:
        Use get_session_results to find driver codes, then use this
        for biographical details.
    """
    logger.info(f"get_driver_info: {driver_id=}, {year=}")
    result = await run_ergast(
        Ergast().get_driver_info,
        what="driver info",
        season=year,
        driver=driver_id,
        limit=1000,
    )

    return records_to_json(first_frame(result))


@tool_handler
async def get_race_results_historical(year: int, round_num: int) -> list[dict]:
    """
    Get historical race results (pre-2018 or when session data unavailable).

    Data source: Ergast API (via FastF1)
    Coverage: 1950-present

    Args:
        year: Season year
        round_num: Round number

    Returns:
        Results with: position, driver, constructor, grid, laps, status,
        time (if finished), fastestLapTime, fastestLapRank

    Note:
        For 2018+ races, prefer get_session_results which has more detail.
    """
    logger.info(f"get_race_results_historical: {year=}, {round_num=}")
    results = await run_ergast(
        Ergast().get_race_results,
        what=f"race results for {year} round {round_num}",
        season=year,
        round=round_num,
    )

    records = []
    for _, row in first_frame(results).iterrows():
        finishing_time = row.get("finishingTime")
        records.append(
            to_jsonable(
                {
                    "position": row.get("position"),
                    "driverCode": row.get("driverCode", ""),
                    "givenName": row.get("givenName", ""),
                    "familyName": row.get("familyName", ""),
                    "constructorName": row.get("constructorName", ""),
                    "grid": row.get("grid"),
                    "laps": row.get("laps"),
                    "status": row.get("status", ""),
                    "points": row.get("points"),
                    # NaT is truthy, so check with notna — otherwise DNFs
                    # come back as the literal string "NaT".
                    "time": str(finishing_time)
                    if finishing_time is not None and pd.notna(finishing_time)
                    else None,
                }
            )
        )

    return records


@tool_handler
async def get_circuit_info(
    circuit_id: str | None = None,
    year: int | None = None,
) -> list[dict]:
    """
    Get circuit information.

    Data source: Ergast API (via FastF1)

    Args:
        circuit_id: Ergast circuit ID (e.g., "monaco", "silverstone")
                    If None, returns all circuits
        year: Filter to circuits used in this season

    Returns:
        Circuit info: circuitId, circuitName, locality, country, lat, long
    """
    logger.info(f"get_circuit_info: {circuit_id=}, {year=}")
    result = await run_ergast(
        Ergast().get_circuits,
        what="circuit info",
        season=year,
        limit=100,
    )

    records = records_to_json(first_frame(result))
    if circuit_id is not None:
        records = [r for r in records if r.get("circuitId") == circuit_id]
    return records


def _date_str(value) -> str | None:
    """Render an ISO timestamp/date as `YYYY-MM-DD` (None-safe)."""
    if not value:
        return None
    return str(value)[:10]
