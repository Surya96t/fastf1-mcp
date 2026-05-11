import logging

import pandas as pd

from ..utils.converters import (
    build_driver_lookup,
    lap_times_summary,
    laps_to_json,
    results_to_json,
    stint_analysis_summary,
)
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.exports import apply_export
from ..utils.session_loader import require_session, tool_handler

logger = logging.getLogger(__name__)


@tool_handler
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
    session_obj = await require_session(year, event, session, load_laps=False)
    return results_to_json(session_obj.results)


@tool_handler
async def get_lap_times(
    year: int,
    event: str | int,
    session: str,
    driver: str,
    include_deleted: bool = False,
    export_path: bool | str = False,
) -> dict:
    """
    Get all lap times for a driver in a session.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Set `export_path=True` when the user mentions data analysis, notebooks,
    pandas, ML, "save as CSV", "export the data", or any downstream
    processing — the full per-lap array is then written to a CSV file the
    user can open directly. Large responses (>50 laps) also auto-export so
    the user always gets a real file path in the project rather than the
    MCP client silently spilling the response to a temp file.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, FP1, FP2, FP3)
        driver: Driver code (e.g., "VER") or number (e.g., "1")
        include_deleted: Include deleted lap times (default False)
        export_path: If True, write the full per-lap array to a CSV in the
                     configured export directory (default `./fastf1-exports/`,
                     override via FASTF1_MCP_EXPORT_DIR) and omit `laps` from
                     the response. Pass a string for a custom directory or
                     `.csv` file path. The server also auto-exports when
                     the lap count exceeds FASTF1_MCP_AUTO_EXPORT_ROWS
                     (default 50).

    Returns:
        Default (no export):
        {
            "driver": "VER", "fullName": "Max Verstappen",
            "teamName": "Red Bull Racing",
            "summary": {...},
            "laps": [{"lapNumber": 1, "lapTime": "0:01:30.456", ...}, ...]
        }

        With export_path:
        {
            "driver": "VER", "fullName": ..., "teamName": ...,
            "summary": {...},
            "exportPath": "/abs/path/to/get_lap_times_2024_monaco_r_ver_<ts>.csv",
            "rowCount": 52
        }

    Note:
        Deleted laps (e.g., track limits violations) are excluded by default.
        Set include_deleted=True to include them.
        `summary` lets a caller answer fastest/avg/compound questions without
        re-parsing the full per-lap array. Use `export_path=True` to grab the
        full dataset as CSV for downstream analysis / ML work.
    """
    logger.info(
        f"get_lap_times: {year=}, {event=}, {session=}, {driver=}, {include_deleted=}"
    )
    session_obj = await require_session(year, event, session)
    laps = session_obj.laps.pick_drivers(driver)

    if len(laps) == 0:
        raise FastF1MCPError(
            ErrorCode.DRIVER_NOT_FOUND,
            f"No laps found for driver '{driver}' in {year} {event} {session}",
            suggestions=["Check the driver code (e.g. 'VER', 'HAM', 'LEC')"],
        )

    if not include_deleted and "Deleted" in laps.columns:
        laps = laps[~laps["Deleted"].fillna(False)]

    laps_json = laps_to_json(laps)

    # Driver enrichment via session.results lookup (input `driver` may be a
    # code OR a number).
    driver_lookup = build_driver_lookup(session_obj)
    info = driver_lookup.get(driver, {})

    response: dict = {
        "driver": info.get("driverCode") or driver,
        "fullName": info.get("fullName", ""),
        "teamName": info.get("teamName", ""),
        "summary": lap_times_summary(laps_json),
    }
    apply_export(
        response,
        laps_json,
        bulk_key="laps",
        export_path=export_path,
        tool="get_lap_times",
        parts=[year, event, session, info.get("driverCode") or driver],
    )
    return response


@tool_handler
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
        sector2, sector3, compound

    Example:
        get_fastest_laps(2024, "Monaco", "R", 5) → [
            {"lapNumber": 67, "lapTime": "0:01:15.456", "compound": "SOFT", ...},
            ...
        ]

    Note:
        Returns one fastest lap per driver. Only accurate laps are included.
    """
    logger.info(f"get_fastest_laps: {year=}, {event=}, {session=}, {top_n=}")
    session_obj = await require_session(year, event, session)

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


@tool_handler
async def get_race_pace(
    year: int,
    event: str | int,
    exclude_first_laps: int = 2,
    exclude_sc_laps: bool = True,
    exclude_pit_laps: bool = True,
    min_laps: int = 10,
) -> dict:
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
        {
            "filters": {
                "excludeFirstLaps": 2, "excludeSafetyCarLaps": true,
                "excludePitLaps": true, "minLaps": 10
            },
            "drivers": [
                {"driver": "LEC", "fullName": "Charles Leclerc",
                 "teamName": "Ferrari", "avgLapTime": "0:01:15.678",
                 "lapCount": 52, "deltaToFastestSec": 0.0, ...},
                ...
            ]
        }

    Note:
        SC/VSC filter uses track status "1" (green flag only).
        Drivers with fewer than min_laps valid laps are excluded.
        The `filters` block echoes the applied filters so the caller can
        clearly state which conditions the pace was computed under.
    """
    logger.info(
        f"get_race_pace: {year=}, {event=}, {exclude_first_laps=}, "
        f"{exclude_sc_laps=}, {exclude_pit_laps=}, {min_laps=}"
    )
    session_obj = await require_session(year, event, "R")
    driver_lookup = build_driver_lookup(session_obj)

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
        info = driver_lookup.get(driver, {})

        pace_data.append(
            {
                "driver": info.get("driverCode") or driver,
                "fullName": info.get("fullName", ""),
                "teamName": info.get("teamName", ""),
                "_avgRaw": avg_time,
                "avgLapTime": str(avg_time),
                "lapCount": len(lap_times),
                "fastestLap": str(lap_times.min()),
                "slowestLap": str(lap_times.max()),
            }
        )

    # Sort by raw Timedelta (avoids unreliable string comparison)
    pace_data.sort(key=lambda x: x["_avgRaw"])

    if pace_data:
        fastest_raw = pace_data[0]["_avgRaw"]
        for item in pace_data:
            item["deltaToFastestSec"] = round(
                (item["_avgRaw"] - fastest_raw).total_seconds(), 3
            )
            del item["_avgRaw"]

    # Echo filter context so callers can disambiguate paces computed under
    # different settings (e.g. SC-laps excluded vs included).
    return {
        "filters": {
            "excludeFirstLaps": exclude_first_laps,
            "excludeSafetyCarLaps": exclude_sc_laps,
            "excludePitLaps": exclude_pit_laps,
            "minLaps": min_laps,
        },
        "drivers": pace_data,
    }


@tool_handler
async def get_stint_analysis(
    year: int,
    event: str | int,
    driver: str | None = None,
    export_path: bool | str = False,
) -> dict:
    """
    Analyze tire stints for a race.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Set `export_path=True` when the user mentions data analysis, notebooks,
    pandas, ML, "save as CSV", "export the strategy data", or any downstream
    processing — the full per-stint array is written to CSV. Large
    responses (>50 stints, typical for full-grid races) also auto-export.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        driver: Optional driver code to filter (default: all drivers)
        export_path: If True, write the full per-stint array to a CSV in
                     the configured export directory (default
                     `./fastf1-exports/`, override via FASTF1_MCP_EXPORT_DIR)
                     and omit `stints` from the response. Pass a string for
                     a custom directory or `.csv` file path. The server
                     also auto-exports when the stint count exceeds
                     FASTF1_MCP_AUTO_EXPORT_ROWS (default 50).

    Returns:
        Default (no export):
        {
            "summary": {...},
            "stints": [{"driver": "LEC", "stintNumber": 1, ...}, ...]
        }

        With export_path:
        {
            "summary": {...},
            "exportPath": "/abs/path/to/get_stint_analysis_<...>.csv",
            "rowCount": 45
        }

    Note:
        Only accurate laps are included in pace calculations.
        Stint numbers match FastF1's internal stint counter.
        Phantom lap-1 stints (single-lap entries with no recorded lap time,
        paired with the lap-1 pit-stop artifact) are filtered out.
        The `summary.strategies` array gives the 1-stop / 2-stop / compound
        sequence per driver in a compact form.
    """
    logger.info(f"get_stint_analysis: {year=}, {event=}, {driver=}")
    session_obj = await require_session(year, event, "R")
    driver_lookup = build_driver_lookup(session_obj)

    laps = session_obj.laps
    if driver is not None:
        laps = laps.pick_drivers(driver)
        if len(laps) == 0:
            raise FastF1MCPError(
                ErrorCode.DRIVER_NOT_FOUND,
                f"No laps found for driver '{driver}'",
                suggestions=["Check the driver code (e.g. 'VER', 'HAM', 'LEC')"],
            )

    results = []

    for drv in laps["Driver"].unique():
        driver_laps = laps[laps["Driver"] == drv]

        for stint_num in sorted(driver_laps["Stint"].dropna().unique()):
            stint_laps = driver_laps[driver_laps["Stint"] == stint_num]
            # Use all stint laps for range, but only accurate for pace stats
            accurate_laps = stint_laps.pick_accurate().dropna(subset=["LapTime"])

            compound = "UNKNOWN"
            if "Compound" in stint_laps.columns and len(stint_laps) > 0:
                compound = (
                    stint_laps["Compound"].dropna().iloc[0]
                    if not stint_laps["Compound"].dropna().empty
                    else "UNKNOWN"
                )

            start_lap = int(stint_laps["LapNumber"].min())
            end_lap = int(stint_laps["LapNumber"].max())
            lap_count = len(stint_laps)

            info = driver_lookup.get(drv, {})
            entry: dict = {
                "driver": info.get("driverCode") or drv,
                "fullName": info.get("fullName", ""),
                "teamName": info.get("teamName", ""),
                "stintNumber": int(stint_num),
                "compound": compound,
                "startLap": start_lap,
                "endLap": end_lap,
                "lapCount": lap_count,
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

            # Skip the phantom lap-1 stint that pairs with the lap-1 pit-stop
            # artifact: a single-lap stint starting at lap 1 with no recorded
            # lap time at all. Real 1-lap stints (e.g. early retirement) still
            # have a LapTime in the row, even if not flagged accurate.
            has_any_laptime = (
                "LapTime" in stint_laps.columns and stint_laps["LapTime"].notna().any()
            )
            is_phantom = start_lap == 1 and lap_count == 1 and not has_any_laptime
            if is_phantom:
                logger.debug(
                    f"Skipping phantom lap-1 stint: driver={drv} compound={compound}"
                )
                continue

            results.append(entry)

    results.sort(key=lambda x: (x["driver"], x["stintNumber"]))

    response: dict = {"summary": stint_analysis_summary(results)}
    apply_export(
        response,
        results,
        bulk_key="stints",
        export_path=export_path,
        tool="get_stint_analysis",
        parts=[year, event, driver],
    )
    return response


@tool_handler
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
        Pit stops sorted by lap: driver (code), fullName, teamName, lap,
        stopNumber, duration, tyreFrom, tyreTo

    Example:
        get_pit_stops(2024, "Monaco") → [
            {"driver": "LEC", "fullName": "Charles Leclerc",
             "teamName": "Ferrari", "lap": 28, "stopNumber": 1,
             "duration": 23.4, "tyreFrom": "MEDIUM", "tyreTo": "HARD"},
            ...
        ]

    Note:
        Duration is calculated from PitInTime (end of in-lap) to
        PitOutTime (start of out-lap), in seconds. Stops with implausibly
        long durations (>120s) are filtered as FastF1 data artifacts —
        commonly a phantom lap-1 entry tied to session start, not a real
        pit stop.
    """
    logger.info(f"get_pit_stops: {year=}, {event=}")
    session_obj = await require_session(year, event, "R")
    driver_lookup = build_driver_lookup(session_obj)

    pit_stops = []

    # Real F1 pit stops are well under a minute even in disaster cases.
    # FastF1 occasionally records a lap-1 PitInTime tied to the session
    # start (formation lap / pit-lane start) which yields a multi-thousand
    # second "stop" — filter those artifacts out by duration.
    MAX_PLAUSIBLE_PIT_DURATION_SEC = 120.0

    for driver in session_obj.drivers:
        driver_laps = session_obj.laps.pick_drivers(driver)

        # In-laps: laps where PitInTime is set (driver entered pits at lap end)
        in_laps = driver_laps[driver_laps["PitInTime"].notna()]

        # Order this driver's stops by lap so stopNumber is well-defined.
        in_laps_sorted = in_laps.sort_values("LapNumber")

        stop_idx = 0
        for _, in_lap in in_laps_sorted.iterrows():
            lap_num = in_lap["LapNumber"]
            if pd.isna(lap_num):
                continue

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

            if duration is not None and duration > MAX_PLAUSIBLE_PIT_DURATION_SEC:
                logger.debug(
                    f"Skipping pit-stop artifact: driver={driver} "
                    f"lap={int(lap_num)} duration={duration}s"
                )
                continue

            stop_idx += 1
            info = driver_lookup.get(driver, {})
            pit_stops.append(
                {
                    "driver": info.get("driverCode") or driver,
                    "fullName": info.get("fullName", ""),
                    "teamName": info.get("teamName", ""),
                    "lap": int(lap_num),
                    "stopNumber": stop_idx,
                    "duration": duration,
                    "tyreFrom": in_lap.get("Compound"),
                    "tyreTo": compound_to,
                }
            )

    # Stable global sort by (lap, driver) — preserves per-driver stopNumber
    pit_stops.sort(key=lambda x: (x["lap"], x["driver"]))
    return pit_stops


@tool_handler
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
    session_obj = await require_session(year, event, "Q")

    q1, q2, q3 = session_obj.laps.split_qualifying_sessions()

    _max_td = pd.Timedelta.max

    def _best_times(qlaps: pd.DataFrame) -> list[dict]:
        """Return best lap per driver, sorted by raw Timedelta."""
        if len(qlaps) == 0:
            return []
        entries = []
        for drv in qlaps["Driver"].unique():
            drv_laps = qlaps[qlaps["Driver"] == drv].dropna(subset=["LapTime"])
            if len(drv_laps) == 0:
                continue
            best_idx = drv_laps["LapTime"].idxmin()
            best = drv_laps.loc[best_idx]
            entries.append(
                {
                    "_bestRaw": best["LapTime"],
                    "driver": drv,
                    "bestTime": str(best["LapTime"]),
                    "lapNumber": int(best["LapNumber"])
                    if pd.notna(best.get("LapNumber"))
                    else None,
                }
            )
        entries.sort(key=lambda x: x["_bestRaw"] or _max_td)
        for e in entries:
            del e["_bestRaw"]
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
