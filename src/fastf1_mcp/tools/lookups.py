import asyncio
import logging
from functools import partial

from fastf1.ergast import Ergast

from ..utils.converters import standings_to_json

logger = logging.getLogger(__name__)


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
    loop = asyncio.get_running_loop()
    ergast = Ergast()

    schedule = await loop.run_in_executor(
        None,
        partial(ergast.get_race_schedule, season=year, limit=30),
    )

    records = schedule.to_dict(orient="records")
    return [
        {
            "round": r.get("round"),
            "raceName": r.get("raceName"),
            "circuitName": r.get("circuitName"),
            "country": r.get("country"),
            "date": str(r.get("raceDate")) if r.get("raceDate") else None,
        }
        for r in records
    ]


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
    loop = asyncio.get_running_loop()
    ergast = Ergast()

    standings = await loop.run_in_executor(
        None,
        partial(ergast.get_driver_standings, season=year, round=after_round),
    )

    return standings_to_json(standings, "driver")


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
    loop = asyncio.get_running_loop()
    ergast = Ergast()

    standings = await loop.run_in_executor(
        None,
        partial(ergast.get_constructor_standings, season=year, round=after_round),
    )

    return standings_to_json(standings, "constructor")


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
    loop = asyncio.get_running_loop()
    ergast = Ergast()

    result = await loop.run_in_executor(
        None,
        partial(
            ergast.get_driver_info,
            season=year,
            driver=driver_id,
            limit=100,
        ),
    )

    if hasattr(result, "content") and len(result.content) > 0:
        df = result.content[0]
    else:
        df = result

    return df.to_dict(orient="records")


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
    loop = asyncio.get_running_loop()
    ergast = Ergast()

    results = await loop.run_in_executor(
        None,
        partial(ergast.get_race_results, season=year, round=round_num),
    )

    if hasattr(results, "content") and len(results.content) > 0:
        df = results.content[0]
    else:
        df = results

    records = []
    for _, row in df.iterrows():
        records.append({
            "position": row.get("position"),
            "driverCode": row.get("driverCode", ""),
            "givenName": row.get("givenName", ""),
            "familyName": row.get("familyName", ""),
            "constructorName": row.get("constructorName", ""),
            "grid": row.get("grid"),
            "laps": row.get("laps"),
            "status": row.get("status", ""),
            "points": row.get("points"),
            "time": str(row["finishingTime"]) if row.get("finishingTime") else None,
        })

    return records


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
    loop = asyncio.get_running_loop()
    ergast = Ergast()

    result = await loop.run_in_executor(
        None,
        partial(
            ergast.get_circuits,
            season=year,
            circuit=circuit_id,
            limit=100,
        ),
    )

    if hasattr(result, "content") and len(result.content) > 0:
        df = result.content[0]
    else:
        df = result

    return df.to_dict(orient="records")
