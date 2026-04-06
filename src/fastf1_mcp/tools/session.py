import logging

import pandas as pd

from ..session_manager import session_manager
from ..utils.converters import laps_to_json, results_to_json
from ..utils.errors import ErrorCode, FastF1MCPError

logger = logging.getLogger(__name__)


async def get_session_results(
    year: int,
    event: str | int,
    session: str = "R",
) -> list[dict]:
    """
    Get session classification/results.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name (e.g., "Monaco") or round number
        session: Session type — R, Q, S, SQ, FP1, FP2, FP3

    Returns:
        Ordered classification with: position, driverCode, fullName,
        teamName, gridPosition, time/status, points

    Example:
        get_session_results(2024, "Monaco", "R") → [
            {"position": 1, "driverCode": "LEC", "fullName": "Charles Leclerc",
             "teamName": "Ferrari", "time": "1:45:12.345", ...},
            ...
        ]

    Note:
        Requires year >= 2018. For historical results use
        get_race_results_historical.
    """
    logger.info(f"get_session_results: {year=}, {event=}, {session=}")

    if year < 2018:
        return FastF1MCPError(
            ErrorCode.YEAR_OUT_OF_RANGE,
            f"FastF1 session data requires 2018+, got {year}",
            suggestions=["Use get_race_results_historical for pre-2018 data"],
        ).to_dict()

    try:
        session_obj = await session_manager.get_session(
            year, event, session, load_laps=False
        )
    except Exception as e:
        logger.error(f"Failed to load session {year} {event} {session}: {e}")
        return FastF1MCPError(
            ErrorCode.SESSION_NOT_FOUND,
            f"Could not load session: {year} {event} {session}. {e}",
            suggestions=[
                "Check that the event name is correct (e.g. 'Monaco', 'Bahrain')",
                "Use list_events(year) to see valid event names",
            ],
        ).to_dict()

    return results_to_json(session_obj.results)


async def get_lap_times(
    year: int,
    event: str | int,
    session: str,
    driver: str,
    include_deleted: bool = False,
) -> list[dict]:
    """
    Get all lap times for a driver in a session.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, FP1, FP2, FP3)
        driver: Driver code (e.g., "VER") or number (e.g., "1")
        include_deleted: Include deleted lap times (default False)

    Returns:
        List of laps with: lapNumber, lapTime, sector1, sector2, sector3,
        compound, tyreLife, isPersonalBest, deleted

    Example:
        get_lap_times(2024, "Monaco", "R", "VER") → [
            {"lapNumber": 1, "lapTime": "0:01:30.456", "compound": "MEDIUM", ...},
            ...
        ]

    Note:
        Deleted laps (e.g., track limits violations) are excluded by default.
        Set include_deleted=True to include them.
    """
    logger.info(
        f"get_lap_times: {year=}, {event=}, {session=}, {driver=}, {include_deleted=}"
    )

    if year < 2018:
        return FastF1MCPError(
            ErrorCode.YEAR_OUT_OF_RANGE,
            f"FastF1 session data requires 2018+, got {year}",
            suggestions=["Use get_race_results_historical for pre-2018 data"],
        ).to_dict()

    try:
        session_obj = await session_manager.get_session(year, event, session)
    except Exception as e:
        logger.error(f"Failed to load session: {e}")
        return FastF1MCPError(
            ErrorCode.SESSION_NOT_FOUND,
            f"Could not load session: {year} {event} {session}. {e}",
            suggestions=["Use list_events(year) to see valid event names"],
        ).to_dict()

    laps = session_obj.laps.pick_drivers(driver)

    if len(laps) == 0:
        return FastF1MCPError(
            ErrorCode.DRIVER_NOT_FOUND,
            f"No laps found for driver '{driver}' in {year} {event} {session}",
            suggestions=["Check the driver code (e.g. 'VER', 'HAM', 'LEC')"],
        ).to_dict()

    if not include_deleted:
        laps = laps[~laps["Deleted"].fillna(False)]

    return laps_to_json(laps)


async def get_fastest_laps(
    year: int,
    event: str | int,
    session: str = "R",
    top_n: int = 10,
) -> list[dict]:
    """
    Get fastest laps in a session, one per driver.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (default "R")
        top_n: Number of fastest laps to return (default 10)

    Returns:
        Fastest laps sorted by time: lapNumber, lapTime, sector1,
        sector2, sector3, compound, driver

    Example:
        get_fastest_laps(2024, "Monaco", "R", 5) → [
            {"lapNumber": 67, "lapTime": "0:01:15.456", "compound": "SOFT", ...},
            ...
        ]

    Note:
        Returns one fastest lap per driver. Only accurate laps are included.
    """
    logger.info(f"get_fastest_laps: {year=}, {event=}, {session=}, {top_n=}")

    if year < 2018:
        return FastF1MCPError(
            ErrorCode.YEAR_OUT_OF_RANGE,
            f"FastF1 session data requires 2018+, got {year}",
            suggestions=["Use get_race_results_historical for pre-2018 data"],
        ).to_dict()

    try:
        session_obj = await session_manager.get_session(year, event, session)
    except Exception as e:
        logger.error(f"Failed to load session: {e}")
        return FastF1MCPError(
            ErrorCode.SESSION_NOT_FOUND,
            f"Could not load session: {year} {event} {session}. {e}",
            suggestions=["Use list_events(year) to see valid event names"],
        ).to_dict()

    fastest_laps = []
    for driver in session_obj.drivers:
        driver_laps = session_obj.laps.pick_drivers(driver).pick_accurate()
        if len(driver_laps) == 0:
            continue
        fastest = driver_laps.pick_fastest()
        if fastest is not None:
            fastest_laps.append(fastest)

    if not fastest_laps:
        return []

    results_df = (
        pd.DataFrame(fastest_laps)
        .dropna(subset=["LapTime"])
        .sort_values("LapTime")
        .head(top_n)
    )

    return laps_to_json(results_df)


async def get_race_pace(
    year: int,
    event: str | int,
    exclude_first_laps: int = 2,
    exclude_sc_laps: bool = True,
    exclude_pit_laps: bool = True,
    min_laps: int = 10,
) -> list[dict]:
    """
    Calculate average race pace for all drivers.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number
        exclude_first_laps: Number of opening laps to exclude (default 2)
        exclude_sc_laps: Exclude laps behind safety car or VSC (default True)
        exclude_pit_laps: Exclude in-laps and out-laps (default True)
        min_laps: Minimum valid laps required to include a driver (default 10)

    Returns:
        Drivers ranked by average pace: driver, avgLapTime, lapCount,
        deltaToFastestSec, fastestLap, slowestLap

    Example:
        get_race_pace(2024, "Monaco") → [
            {"driver": "LEC", "avgLapTime": "0:01:15.678",
             "lapCount": 52, "deltaToFastestSec": 0.0, ...},
            ...
        ]

    Note:
        SC/VSC filter uses track status "1" (green flag only).
        Drivers with fewer than min_laps valid laps are excluded.
    """
    logger.info(
        f"get_race_pace: {year=}, {event=}, {exclude_first_laps=}, "
        f"{exclude_sc_laps=}, {exclude_pit_laps=}, {min_laps=}"
    )

    if year < 2018:
        return FastF1MCPError(
            ErrorCode.YEAR_OUT_OF_RANGE,
            f"FastF1 session data requires 2018+, got {year}",
            suggestions=["Use get_race_results_historical for pre-2018 data"],
        ).to_dict()

    try:
        session_obj = await session_manager.get_session(year, event, "R")
    except Exception as e:
        logger.error(f"Failed to load session: {e}")
        return FastF1MCPError(
            ErrorCode.SESSION_NOT_FOUND,
            f"Could not load session: {year} {event} R. {e}",
            suggestions=["Use list_events(year) to see valid event names"],
        ).to_dict()

    pace_data = []

    for driver in session_obj.drivers:
        laps = session_obj.laps.pick_drivers(driver)

        if exclude_first_laps > 0:
            laps = laps[laps["LapNumber"] > exclude_first_laps]

        if exclude_sc_laps:
            laps = laps.pick_track_status("1", how="equals")

        if exclude_pit_laps:
            laps = laps.pick_wo_box()

        laps = laps.pick_accurate()

        if len(laps) < min_laps:
            continue

        lap_times = laps["LapTime"].dropna()
        if len(lap_times) == 0:
            continue

        avg_time = lap_times.mean()

        pace_data.append({
            "driver": driver,
            "_avgRaw": avg_time,
            "avgLapTime": str(avg_time),
            "lapCount": len(lap_times),
            "fastestLap": str(lap_times.min()),
            "slowestLap": str(lap_times.max()),
        })

    # Sort by raw Timedelta (avoids unreliable string comparison)
    pace_data.sort(key=lambda x: x["_avgRaw"])

    # Compute delta to leader in seconds, then strip the internal sort key
    if pace_data:
        fastest_raw = pace_data[0]["_avgRaw"]
        for item in pace_data:
            item["deltaToFastestSec"] = round(
                (item["_avgRaw"] - fastest_raw).total_seconds(), 3
            )
            del item["_avgRaw"]

    return pace_data


async def get_stint_analysis(
    year: int,
    event: str | int,
    driver: str | None = None,
) -> list[dict]:
    """
    Analyze tire stints for a race.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number
        driver: Optional driver code to filter (default: all drivers)

    Returns:
        Stints with: driver, stintNumber, compound, startLap, endLap,
        lapCount, minLapTime, avgLapTime, maxLapTime

    Example:
        get_stint_analysis(2024, "Monaco", "LEC") → [
            {"driver": "LEC", "stintNumber": 1, "compound": "MEDIUM",
             "startLap": 1, "endLap": 28, "lapCount": 28, ...},
            ...
        ]

    Note:
        Only accurate laps are included in pace calculations.
        Stint numbers match FastF1's internal stint counter.
    """
    logger.info(f"get_stint_analysis: {year=}, {event=}, {driver=}")

    if year < 2018:
        return FastF1MCPError(
            ErrorCode.YEAR_OUT_OF_RANGE,
            f"FastF1 session data requires 2018+, got {year}",
            suggestions=["Use get_race_results_historical for pre-2018 data"],
        ).to_dict()

    try:
        session_obj = await session_manager.get_session(year, event, "R")
    except Exception as e:
        logger.error(f"Failed to load session: {e}")
        return FastF1MCPError(
            ErrorCode.SESSION_NOT_FOUND,
            f"Could not load session: {year} {event} R. {e}",
            suggestions=["Use list_events(year) to see valid event names"],
        ).to_dict()

    laps = session_obj.laps
    if driver is not None:
        laps = laps.pick_drivers(driver)
        if len(laps) == 0:
            return FastF1MCPError(
                ErrorCode.DRIVER_NOT_FOUND,
                f"No laps found for driver '{driver}'",
                suggestions=["Check the driver code (e.g. 'VER', 'HAM', 'LEC')"],
            ).to_dict()

    results = []

    for drv in laps["Driver"].unique():
        driver_laps = laps[laps["Driver"] == drv]

        for stint_num in sorted(driver_laps["Stint"].dropna().unique()):
            stint_laps = driver_laps[driver_laps["Stint"] == stint_num]
            # Use all stint laps for range, but only accurate for pace stats
            accurate_laps = stint_laps.pick_accurate().dropna(subset=["LapTime"])

            compound = "UNKNOWN"
            if "Compound" in stint_laps.columns and len(stint_laps) > 0:
                compound = stint_laps["Compound"].dropna().iloc[0] if not stint_laps["Compound"].dropna().empty else "UNKNOWN"

            entry: dict = {
                "driver": drv,
                "stintNumber": int(stint_num),
                "compound": compound,
                "startLap": int(stint_laps["LapNumber"].min()),
                "endLap": int(stint_laps["LapNumber"].max()),
                "lapCount": len(stint_laps),
            }

            if len(accurate_laps) > 0:
                lap_times = accurate_laps["LapTime"]
                entry["minLapTime"] = str(lap_times.min())
                entry["avgLapTime"] = str(lap_times.mean())
                entry["maxLapTime"] = str(lap_times.max())
            else:
                entry["minLapTime"] = None
                entry["avgLapTime"] = None
                entry["maxLapTime"] = None

            results.append(entry)

    results.sort(key=lambda x: (x["driver"], x["stintNumber"]))
    return results


async def get_pit_stops(
    year: int,
    event: str | int,
) -> list[dict]:
    """
    Get all pit stops from a race.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number

    Returns:
        Pit stops sorted by lap: driver, lap, stopNumber, duration,
        tyreFrom, tyreTo

    Example:
        get_pit_stops(2024, "Monaco") → [
            {"driver": "LEC", "lap": 28, "stopNumber": 1,
             "duration": 23.4, "tyreFrom": "MEDIUM", "tyreTo": "HARD"},
            ...
        ]

    Note:
        Duration is calculated from PitInTime (end of in-lap) to
        PitOutTime (start of out-lap), in seconds.
    """
    logger.info(f"get_pit_stops: {year=}, {event=}")

    if year < 2018:
        return FastF1MCPError(
            ErrorCode.YEAR_OUT_OF_RANGE,
            f"FastF1 session data requires 2018+, got {year}",
            suggestions=["Use get_race_results_historical for pre-2018 data"],
        ).to_dict()

    try:
        session_obj = await session_manager.get_session(year, event, "R")
    except Exception as e:
        logger.error(f"Failed to load session: {e}")
        return FastF1MCPError(
            ErrorCode.SESSION_NOT_FOUND,
            f"Could not load session: {year} {event} R. {e}",
            suggestions=["Use list_events(year) to see valid event names"],
        ).to_dict()

    pit_stops = []

    for driver in session_obj.drivers:
        driver_laps = session_obj.laps.pick_drivers(driver)

        # In-laps: laps where PitInTime is set (driver entered pits at lap end)
        in_laps = driver_laps[driver_laps["PitInTime"].notna()]

        for _, in_lap in in_laps.iterrows():
            lap_num = in_lap["LapNumber"]

            # The out-lap is the very next lap for this driver
            out_laps = driver_laps[driver_laps["LapNumber"] == lap_num + 1]

            duration = None
            compound_to = None

            if len(out_laps) > 0:
                out_lap = out_laps.iloc[0]
                pit_in = in_lap.get("PitInTime")
                pit_out = out_lap.get("PitOutTime")
                if pd.notna(pit_in) and pd.notna(pit_out):
                    duration = round((pit_out - pit_in).total_seconds(), 1)
                compound_to = out_lap.get("Compound")

            pit_stops.append({
                "driver": driver,
                "lap": int(lap_num) if pd.notna(lap_num) else None,
                "stopNumber": None,  # filled below
                "duration": duration,
                "tyreFrom": in_lap.get("Compound"),
                "tyreTo": compound_to,
            })

    # Sort by lap number, then assign stop numbers per driver
    pit_stops.sort(key=lambda x: (x["lap"] or 0))
    driver_stop_count: dict[str, int] = {}
    for stop in pit_stops:
        drv = stop["driver"]
        driver_stop_count[drv] = driver_stop_count.get(drv, 0) + 1
        stop["stopNumber"] = driver_stop_count[drv]

    return pit_stops


async def get_qualifying_breakdown(
    year: int,
    event: str | int,
) -> dict:
    """
    Get qualifying results split by Q1/Q2/Q3.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number

    Returns:
        {
            "Q1": [{"driver": "VER", "bestTime": "1:10.123", "lapNumber": 3}, ...],
            "Q2": [...],
            "Q3": [...],
            "eliminated_Q1": ["driver1", "driver2", ...],
            "eliminated_Q2": ["driver3", "driver4", ...]
        }

    Example:
        get_qualifying_breakdown(2024, "Monaco") → {
            "Q1": [...20 drivers sorted by best time...],
            "Q2": [...15 drivers...],
            "Q3": [...10 drivers...],
            "eliminated_Q1": ["5 driver codes"],
            "eliminated_Q2": ["5 driver codes"]
        }

    Note:
        Uses laps.split_qualifying_sessions() to split by session time.
        Drivers with no recorded lap time in a segment are omitted from
        that segment's list.
    """
    logger.info(f"get_qualifying_breakdown: {year=}, {event=}")

    if year < 2018:
        return FastF1MCPError(
            ErrorCode.YEAR_OUT_OF_RANGE,
            f"FastF1 session data requires 2018+, got {year}",
            suggestions=["Use get_race_results_historical for pre-2018 data"],
        ).to_dict()

    try:
        session_obj = await session_manager.get_session(year, event, "Q")
    except Exception as e:
        logger.error(f"Failed to load qualifying session: {e}")
        return FastF1MCPError(
            ErrorCode.SESSION_NOT_FOUND,
            f"Could not load qualifying session: {year} {event} Q. {e}",
            suggestions=["Use list_events(year) to see valid event names"],
        ).to_dict()

    q1, q2, q3 = session_obj.laps.split_qualifying_sessions()

    def _best_times(qlaps: pd.DataFrame) -> list[dict]:
        """Return best lap per driver, sorted by time."""
        if len(qlaps) == 0:
            return []
        entries = []
        for drv in qlaps["Driver"].unique():
            drv_laps = qlaps[qlaps["Driver"] == drv].dropna(subset=["LapTime"])
            if len(drv_laps) == 0:
                continue
            best_idx = drv_laps["LapTime"].idxmin()
            best = drv_laps.loc[best_idx]
            entries.append({
                "driver": drv,
                "bestTime": str(best["LapTime"]),
                "lapNumber": int(best["LapNumber"]) if pd.notna(best.get("LapNumber")) else None,
            })
        entries.sort(key=lambda x: x["bestTime"])
        return entries

    q1_drivers = set(q1["Driver"].unique()) if len(q1) > 0 else set()
    q2_drivers = set(q2["Driver"].unique()) if len(q2) > 0 else set()
    q3_drivers = set(q3["Driver"].unique()) if len(q3) > 0 else set()

    return {
        "Q1": _best_times(q1),
        "Q2": _best_times(q2),
        "Q3": _best_times(q3),
        "eliminated_Q1": sorted(q1_drivers - q2_drivers),
        "eliminated_Q2": sorted(q2_drivers - q3_drivers),
    }
