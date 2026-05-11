import logging

import numpy as np
import pandas as pd

from ..config import settings
from ..utils.converters import (
    build_driver_lookup,
    telemetry_summary,
    telemetry_to_json,
)
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.exports import apply_export
from ..utils.session_loader import require_session, tool_handler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_driver_lap(session_obj, driver: str, lap: int | str):
    """Return the target Lap row for a driver, or None if not found."""
    driver_laps = session_obj.laps.pick_drivers(driver)
    if len(driver_laps) == 0:
        return None
    if lap == "fastest":
        return driver_laps.pick_fastest()
    matching = driver_laps[driver_laps["LapNumber"] == int(lap)]
    return matching.iloc[0] if len(matching) > 0 else None


def _cumulative_time_at_distances(
    tel: pd.DataFrame, distances: np.ndarray
) -> np.ndarray:
    """
    Return elapsed lap time (seconds) at each requested distance (metres).

    Prefers the recorded Time channel; falls back to integrating Speed if absent.
    """
    d = tel["Distance"].to_numpy(dtype=float)
    if d.size == 0:
        return np.zeros_like(distances, dtype=float)

    # Sort by distance and drop duplicates first — both branches below need
    # monotonic-increasing xp, and the Speed-integration fallback also needs
    # to integrate in distance order so non-monotonic samples don't bake into
    # the cumulative-time curve.
    order = np.argsort(d, kind="stable")
    d_sorted = d[order]
    keep = np.concatenate(([True], np.diff(d_sorted) > 0))
    d_sorted = d_sorted[keep]

    cum_sorted: np.ndarray | None = None
    if "Time" in tel.columns:
        try:
            t = pd.to_timedelta(tel["Time"]).dt.total_seconds().to_numpy(dtype=float)
            t_sorted = t[order][keep]
            cum_sorted = t_sorted - t_sorted[0]
        except Exception:
            cum_sorted = None

    if cum_sorted is None:
        v = tel["Speed"].to_numpy(dtype=float)[order][keep]
        # km/h -> m/s; clamp speed to a sane floor so a single near-zero sample
        # at standing starts / pit lane can't blow up the cumulative integral.
        v_ms = np.clip(v * (1000.0 / 3600.0), 0.5, None)
        dd = np.diff(d_sorted, prepend=d_sorted[0])
        cum_sorted = np.cumsum(dd / v_ms)

    return np.interp(distances, d_sorted, cum_sorted)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool_handler
async def get_lap_telemetry(
    year: int,
    event: str | int,
    session: str,
    driver: str,
    lap: int | str = "fastest",
    sample_size: int = 200,
    export_path: bool | str = False,
) -> dict:
    """
    Get telemetry data for a specific lap.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Set `export_path=True` when the user mentions data analysis, notebooks,
    pandas, ML, "plot the telemetry", "save the trace", or downstream
    processing — the sampled per-distance trace is written to CSV.
    Telemetry responses with the default 200 sample points also auto-export
    so the user always gets a real file path in the project.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, FP1, FP2, FP3)
        driver: Driver code (e.g., "VER")
        lap: Lap number or "fastest" (default)
        sample_size: Number of telemetry points to return (default 200, max 500)
        export_path: If True, write the sampled `data` array to a CSV in
                     the configured export directory (default
                     `./fastf1-exports/`, override via FASTF1_MCP_EXPORT_DIR)
                     and omit `data` from the response. Pass a string for a
                     custom directory or `.csv` file path. The server also
                     auto-exports when `data` would exceed
                     FASTF1_MCP_AUTO_EXPORT_ROWS rows (default 50). Use a
                     larger sample_size if you want more detail in the
                     exported file.

    Returns:
        {
            "driver": "VER",
            "lapNumber": 42,
            "lapTime": "0:01:23.456",
            "summary": {
                "samplePoints": 200, "maxSpeedKph": 327.5,
                "minSpeedKph": 80.2, "avgSpeedKph": 218.1,
                "maxGear": 8, "brakingZones": 7,
                "fullThrottlePct": 64.5
            },
            "data": [
                {"distance": 0.0, "speed": 280.0, "throttle": 95.0,
                 "brake": false, "gear": 7, "drs": 0},
                ...
            ]
        }

    Example:
        get_lap_telemetry(2024, "Monaco", "Q", "VER") → fastest Q lap telemetry
        get_lap_telemetry(2024, "Monaco", "R", "VER", lap=45) → lap 45 telemetry

    Note:
        Raw telemetry has 5000+ points per lap. Response is sampled to
        sample_size evenly-spaced distance points (capped at 500).
        `summary` lets a caller answer top-speed / braking-zone questions
        without parsing the full per-distance array.
    """
    logger.info(
        f"get_lap_telemetry: {year=}, {event=}, {session=}, {driver=}, {lap=}, {sample_size=}"
    )

    sample_size = max(1, min(sample_size, settings.max_telemetry_samples))
    session_obj = await require_session(year, event, session, with_telemetry=True)

    target_lap = _get_driver_lap(session_obj, driver, lap)
    if target_lap is None:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"No lap data found for driver '{driver}' lap={lap}",
            suggestions=["Check the driver code (e.g. 'VER', 'HAM', 'LEC')"],
        )

    try:
        telemetry = target_lap.get_telemetry()
    except Exception as e:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"Telemetry unavailable for {driver} lap {lap}: {e}",
        ) from e

    data = telemetry_to_json(telemetry, sample_size)

    response: dict = {
        "driver": driver,
        "lapNumber": int(target_lap["LapNumber"])
        if pd.notna(target_lap.get("LapNumber"))
        else None,
        "lapTime": str(target_lap["LapTime"])
        if pd.notna(target_lap.get("LapTime"))
        else None,
        "summary": telemetry_summary(data),
    }
    apply_export(
        response,
        data,
        bulk_key="data",
        export_path=export_path,
        tool="get_lap_telemetry",
        parts=[
            year,
            event,
            session,
            driver,
            f"lap-{response['lapNumber']}" if response["lapNumber"] else lap,
        ],
    )
    return response


@tool_handler
async def compare_telemetry(
    year: int,
    event: str | int,
    session: str,
    driver1: str,
    driver2: str,
    lap: int | str = "fastest",
    sample_size: int = 200,
    export_path: bool | str = False,
) -> dict:
    """
    Compare telemetry between two drivers on the same session.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Set `export_path=True` when the user wants the comparison data for
    analysis (notebook, pandas, ML, "plot where one driver gains time",
    "save the comparison"). Comparisons at the default 200 sample size
    also auto-export.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, FP1, FP2, FP3)
        driver1: First driver code (e.g., "VER")
        driver2: Second driver code (e.g., "LEC")
        lap: Lap number or "fastest" — applied independently to each driver
        sample_size: Telemetry points per driver (default 200, max 500)
        export_path: If True, write the per-distance `comparison` array to
                     a CSV in the configured export directory (default
                     `./fastf1-exports/`, override via FASTF1_MCP_EXPORT_DIR)
                     and omit `comparison` from the response. Pass a string
                     for a custom directory or `.csv` file path.

    Returns:
        {
            "driver1": {"code": "VER", "lapNumber": 18, "lapTime": "1:10.123"},
            "driver2": {"code": "LEC", "lapNumber": 20, "lapTime": "1:10.456"},
            "comparison": [
                {"distance": 0.0, "speed1": 280.0, "speed2": 275.0,
                 "speedDelta": 5.0, "timeDelta": 0.0},
                ...
            ],
            "summary": {
                "lapTimeDeltaSec": 0.333,
                "maxSpeedDelta": 8.2,
                "sectors": {
                    "S1": {"driver1": "0:00:28.123", "driver2": "0:00:28.456", "deltaSec": -0.333},
                    "S2": {...},
                    "S3": {...}
                },
                "driver1Telemetry": {"maxSpeedKph": 325.0, "brakingZones": 7, ...},
                "driver2Telemetry": {"maxSpeedKph": 320.5, "brakingZones": 8, ...}
            }
        }

    Example:
        compare_telemetry(2024, "Monaco", "Q", "VER", "LEC")

    Note:
        timeDelta is the cumulative time gap at each distance point,
        computed from speed integration. Positive = driver1 is ahead.
        Comparison is aligned to driver1's distance axis.
    """
    logger.info(
        f"compare_telemetry: {year=}, {event=}, {session=}, {driver1=}, {driver2=}, {lap=}"
    )

    sample_size = max(1, min(sample_size, settings.max_telemetry_samples))
    session_obj = await require_session(year, event, session, with_telemetry=True)

    lap1 = _get_driver_lap(session_obj, driver1, lap)
    lap2 = _get_driver_lap(session_obj, driver2, lap)

    if lap1 is None:
        raise FastF1MCPError(
            ErrorCode.DRIVER_NOT_FOUND,
            f"No lap found for driver '{driver1}'",
            suggestions=["Check the driver code (e.g. 'VER', 'HAM', 'LEC')"],
        )
    if lap2 is None:
        raise FastF1MCPError(
            ErrorCode.DRIVER_NOT_FOUND,
            f"No lap found for driver '{driver2}'",
            suggestions=["Check the driver code (e.g. 'VER', 'HAM', 'LEC')"],
        )

    try:
        tel1 = lap1.get_telemetry().add_distance()
        tel2 = lap2.get_telemetry().add_distance()
    except Exception as e:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"Telemetry unavailable: {e}",
        ) from e

    # Distance grid aligned to driver1
    max_dist = float(tel1["Distance"].max())
    distances = np.linspace(0, max_dist, sample_size)

    # Interpolate speed for both drivers. np.interp requires xp to be monotonic
    # increasing, so sort+dedup and (for tel2) restrict to the driver1 window.
    def _interp_speed(tel: pd.DataFrame, max_d: float | None = None) -> np.ndarray:
        d = tel["Distance"].to_numpy(dtype=float)
        s = tel["Speed"].to_numpy(dtype=float)
        if max_d is not None:
            mask = d <= max_d
            d, s = d[mask], s[mask]
        order = np.argsort(d, kind="stable")
        d, s = d[order], s[order]
        keep = np.concatenate(([True], np.diff(d) > 0))
        return np.interp(distances, d[keep], s[keep])

    speed1 = _interp_speed(tel1)
    speed2 = _interp_speed(tel2, max_d=max_dist)

    cum_time1 = _cumulative_time_at_distances(tel1, distances)
    cum_time2 = _cumulative_time_at_distances(tel2, distances)
    time_delta = cum_time1 - cum_time2  # positive = driver1 ahead in elapsed time

    comparison = [
        {
            "distance": round(float(distances[i]), 1),
            "speed1": round(float(speed1[i]), 1),
            "speed2": round(float(speed2[i]), 1),
            "speedDelta": round(float(speed1[i] - speed2[i]), 1),
            "timeDelta": round(float(time_delta[i]), 3),
        }
        for i in range(sample_size)
    ]

    # Per-driver telemetry summary — built from the original telemetry frames
    # (not the aligned comparison grid) so braking-zone and max-speed counts
    # reflect each driver's actual lap.
    def _summary_for(tel: pd.DataFrame) -> dict:
        points = telemetry_to_json(tel, sample_size)
        return telemetry_summary(points)

    driver1_summary = _summary_for(tel1)
    driver2_summary = _summary_for(tel2)

    t1 = lap1.get("LapTime")
    t2 = lap2.get("LapTime")
    lap_time_delta = None
    if pd.notna(t1) and pd.notna(t2):
        lap_time_delta = round((t1 - t2).total_seconds(), 3)

    sectors = {}
    for s_key, col in [
        ("S1", "Sector1Time"),
        ("S2", "Sector2Time"),
        ("S3", "Sector3Time"),
    ]:
        v1 = lap1.get(col)
        v2 = lap2.get(col)
        if pd.notna(v1) and pd.notna(v2):
            sectors[s_key] = {
                "driver1": str(v1),
                "driver2": str(v2),
                "deltaSec": round((v1 - v2).total_seconds(), 3),
            }

    response: dict = {
        "driver1": {
            "code": driver1,
            "lapNumber": int(lap1["LapNumber"])
            if pd.notna(lap1.get("LapNumber"))
            else None,
            "lapTime": str(t1) if pd.notna(t1) else None,
        },
        "driver2": {
            "code": driver2,
            "lapNumber": int(lap2["LapNumber"])
            if pd.notna(lap2.get("LapNumber"))
            else None,
            "lapTime": str(t2) if pd.notna(t2) else None,
        },
        "summary": {
            "lapTimeDeltaSec": lap_time_delta,
            "maxSpeedDelta": round(float(np.max(np.abs(speed1 - speed2))), 1),
            "sectors": sectors,
            "driver1Telemetry": driver1_summary,
            "driver2Telemetry": driver2_summary,
        },
    }

    apply_export(
        response,
        comparison,
        bulk_key="comparison",
        export_path=export_path,
        tool="compare_telemetry",
        parts=[year, event, session, f"{driver1}-vs-{driver2}"],
    )
    return response


@tool_handler
async def get_speed_trap_data(
    year: int,
    event: str | int,
    session: str,
) -> list[dict]:
    """
    Get speed trap and top-speed data for all drivers in a session.

    Data source: FastF1 Live Timing (session results)
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, FP1, FP2, FP3)

    Returns:
        {
            "source": "results" | "laps",
            "drivers": [
                {"driver": "VER", "fullName": "Max Verstappen",
                 "teamName": "Red Bull Racing", "speedTrap": 298.5,
                 "speedFL": 187.2, "speedI1": 245.0, "speedI2": 268.5},
                ...
            ]
        }

    Example:
        get_speed_trap_data(2024, "Monza", "Q") → {"source": "results", ...}

    Note:
        SpeedST = official speed trap measurement.
        SpeedFL = speed at the finish line.
        SpeedI1/I2 = sector intermediate speed measurements.
        Values are in km/h.

        FastF1 publishes per-driver speed columns on `session.results`, but
        for many sessions those columns are entirely empty. When the
        results-level data is missing, we fall back to the per-lap max
        across `session.laps` for the same columns. `source` indicates
        which path produced the response.
    """
    logger.info(f"get_speed_trap_data: {year=}, {event=}, {session=}")
    # Laps are required for the fallback path.
    session_obj = await require_session(year, event, session)
    driver_lookup = build_driver_lookup(session_obj)

    SPEED_COLS = ("SpeedST", "SpeedFL", "SpeedI1", "SpeedI2")
    TOOL_KEYS = ("speedTrap", "speedFL", "speedI1", "speedI2")

    def _enrich(code: str) -> dict:
        info = driver_lookup.get(code, {})
        return {
            "driver": info.get("driverCode") or code,
            "fullName": info.get("fullName", ""),
            "teamName": info.get("teamName", ""),
        }

    results = getattr(session_obj, "results", None)
    results_have_speeds = (
        results is not None
        and hasattr(results, "columns")
        and any(
            col in results.columns and results[col].notna().any() for col in SPEED_COLS
        )
    )

    rows: list[dict] = []
    source = "results" if results_have_speeds else "laps"

    if results_have_speeds:
        for _, row in results.iterrows():
            code = row.get("Abbreviation", "") or ""
            entry = _enrich(code)
            for tool_key, col in zip(TOOL_KEYS, SPEED_COLS):
                entry[tool_key] = float(row[col]) if pd.notna(row.get(col)) else None
            rows.append(entry)
    else:
        # Fallback: per-driver max across the laps DataFrame.
        laps = getattr(session_obj, "laps", None)
        if (
            laps is None
            or not hasattr(laps, "columns")
            or "Driver" not in laps.columns
            or not any(col in laps.columns for col in SPEED_COLS)
        ):
            raise FastF1MCPError(
                ErrorCode.DATA_UNAVAILABLE,
                f"Speed trap data is unavailable for {year} {event} {session}.",
                suggestions=[
                    "Speed measurements are sometimes not published for "
                    "certain sessions or older events.",
                ],
            )

        for drv in laps["Driver"].dropna().unique():
            drv_laps = laps[laps["Driver"] == drv]
            entry = _enrich(drv)
            for tool_key, col in zip(TOOL_KEYS, SPEED_COLS):
                if col in drv_laps.columns:
                    series = drv_laps[col].dropna()
                    entry[tool_key] = float(series.max()) if len(series) > 0 else None
                else:
                    entry[tool_key] = None
            rows.append(entry)

    # Sort by speed trap descending; push None to end
    rows.sort(key=lambda x: (x["speedTrap"] is None, -(x["speedTrap"] or 0)))
    return {"source": source, "drivers": rows}


@tool_handler
async def get_sector_times(
    year: int,
    event: str | int,
    session: str,
    driver: str | None = None,
    include_laps: bool = False,
) -> list[dict]:
    """
    Get best sector times and theoretical best lap for each driver.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Set `include_laps=True` when the user wants per-lap sector breakdowns
    (e.g. "show me Antonelli's sector times each lap") rather than just
    the per-driver best/theoretical-best summary.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, FP1, FP2, FP3)
        driver: Optional driver code to filter (default: all drivers)
        include_laps: If True, include a `laps` array per driver with each
                      accurate lap's S1/S2/S3 and total lap time. Default
                      False keeps responses compact for the typical
                      "fastest sectors / theoretical best" question.

    Returns:
        For each driver: driver (code), fullName, teamName, bestS1, bestS2,
        bestS3, theoreticalBest, actualBest, gapSec. With `include_laps`,
        each entry also has `laps: [{lapNumber, s1, s2, s3, lapTime}, ...]`.

    Example:
        get_sector_times(2024, "Monaco", "Q") → [
            {"driver": "VER", "fullName": "Max Verstappen",
             "teamName": "Red Bull Racing",
             "bestS1": "0:00:22.123", "bestS2": "0:00:24.456",
             "bestS3": "0:00:21.789", "theoreticalBest": "0:01:08.368",
             "actualBest": "0:01:08.570", "gapSec": -0.202},
            ...
        ]

    Note:
        A negative gapSec means the theoretical best (sum of individual
        sector bests) is faster than the actual best lap — typical, since
        sector bests usually come from different laps.
    """
    logger.info(
        f"get_sector_times: {year=}, {event=}, {session=}, {driver=}, {include_laps=}"
    )
    session_obj = await require_session(year, event, session)
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
    for drv in laps["Driver"].dropna().unique():
        drv_laps = laps[laps["Driver"] == drv].pick_accurate()
        if len(drv_laps) == 0:
            continue

        s1_times = drv_laps["Sector1Time"].dropna()
        s2_times = drv_laps["Sector2Time"].dropna()
        s3_times = drv_laps["Sector3Time"].dropna()
        lap_times = drv_laps["LapTime"].dropna()

        best_s1 = s1_times.min() if len(s1_times) > 0 else None
        best_s2 = s2_times.min() if len(s2_times) > 0 else None
        best_s3 = s3_times.min() if len(s3_times) > 0 else None
        actual_best = lap_times.min() if len(lap_times) > 0 else None

        theoretical_best = None
        gap_sec = None
        if best_s1 is not None and best_s2 is not None and best_s3 is not None:
            theoretical_best = best_s1 + best_s2 + best_s3
            if actual_best is not None:
                gap_sec = round((theoretical_best - actual_best).total_seconds(), 3)

        info = driver_lookup.get(drv, {})
        entry = {
            "driver": info.get("driverCode") or drv,
            "fullName": info.get("fullName", ""),
            "teamName": info.get("teamName", ""),
            "_theoreticalRaw": theoretical_best,
            "bestS1": str(best_s1) if best_s1 is not None else None,
            "bestS2": str(best_s2) if best_s2 is not None else None,
            "bestS3": str(best_s3) if best_s3 is not None else None,
            "theoreticalBest": str(theoretical_best)
            if theoretical_best is not None
            else None,
            "actualBest": str(actual_best) if actual_best is not None else None,
            "gapSec": gap_sec,
        }

        if include_laps:
            per_lap = []
            for _, row in drv_laps.iterrows():
                per_lap.append(
                    {
                        "lapNumber": int(row["LapNumber"])
                        if pd.notna(row.get("LapNumber"))
                        else None,
                        "s1": str(row["Sector1Time"])
                        if pd.notna(row.get("Sector1Time"))
                        else None,
                        "s2": str(row["Sector2Time"])
                        if pd.notna(row.get("Sector2Time"))
                        else None,
                        "s3": str(row["Sector3Time"])
                        if pd.notna(row.get("Sector3Time"))
                        else None,
                        "lapTime": str(row["LapTime"])
                        if pd.notna(row.get("LapTime"))
                        else None,
                    }
                )
            per_lap.sort(key=lambda x: x["lapNumber"] or 0)
            entry["laps"] = per_lap

        results.append(entry)

    # Sort by raw Timedelta ascending (fastest first); push None to end.
    _max_td = pd.Timedelta.max
    results.sort(key=lambda x: x["_theoreticalRaw"] or _max_td)
    for r in results:
        del r["_theoreticalRaw"]
    return results
