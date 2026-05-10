import logging

import numpy as np
import pandas as pd

from ..config import settings
from ..utils.converters import telemetry_to_json
from ..utils.errors import ErrorCode, FastF1MCPError
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

    cum_time: np.ndarray | None = None
    if "Time" in tel.columns:
        try:
            t = pd.to_timedelta(tel["Time"]).dt.total_seconds().to_numpy(dtype=float)
            cum_time = t - t[0]
        except Exception:
            cum_time = None

    if cum_time is None:
        v = tel["Speed"].to_numpy(dtype=float)
        # km/h -> m/s; clamp speed to a sane floor so a single near-zero sample
        # at standing starts / pit lane can't blow up the cumulative integral.
        v_ms = np.clip(v * (1000.0 / 3600.0), 0.5, None)
        dd = np.diff(d, prepend=d[0])
        cum_time = np.cumsum(dd / v_ms)

    # np.interp requires monotonic-increasing xp; sort by distance and drop dups.
    order = np.argsort(d, kind="stable")
    d_sorted = d[order]
    cum_sorted = cum_time[order]
    keep = np.concatenate(([True], np.diff(d_sorted) > 0))
    return np.interp(distances, d_sorted[keep], cum_sorted[keep])


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
) -> dict:
    """
    Get telemetry data for a specific lap.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, FP1, FP2, FP3)
        driver: Driver code (e.g., "VER")
        lap: Lap number or "fastest" (default)
        sample_size: Number of telemetry points to return (default 200, max 500)

    Returns:
        {
            "driver": "VER",
            "lapNumber": 42,
            "lapTime": "0:01:23.456",
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

    return {
        "driver": driver,
        "lapNumber": int(target_lap["LapNumber"])
        if pd.notna(target_lap.get("LapNumber"))
        else None,
        "lapTime": str(target_lap["LapTime"])
        if pd.notna(target_lap.get("LapTime"))
        else None,
        "data": telemetry_to_json(telemetry, sample_size),
    }


@tool_handler
async def compare_telemetry(
    year: int,
    event: str | int,
    session: str,
    driver1: str,
    driver2: str,
    lap: int | str = "fastest",
    sample_size: int = 200,
) -> dict:
    """
    Compare telemetry between two drivers on the same session.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, FP1, FP2, FP3)
        driver1: First driver code (e.g., "VER")
        driver2: Second driver code (e.g., "LEC")
        lap: Lap number or "fastest" — applied independently to each driver
        sample_size: Telemetry points per driver (default 200, max 500)

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
                }
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

    return {
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
        "comparison": comparison,
        "summary": {
            "lapTimeDeltaSec": lap_time_delta,
            "maxSpeedDelta": round(float(np.max(np.abs(speed1 - speed2))), 1),
            "sectors": sectors,
        },
    }


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
        Drivers sorted by speed trap speed (descending):
        driver, speedTrap, speedFL, speedI1, speedI2

    Example:
        get_speed_trap_data(2024, "Monaco", "Q") → [
            {"driver": "VER", "speedTrap": 298.5, "speedFL": 187.2, ...},
            ...
        ]

    Note:
        SpeedST = official speed trap measurement.
        SpeedFL = speed at the finish line.
        SpeedI1/I2 = sector intermediate speed measurements.
        Values are in km/h.
    """
    logger.info(f"get_speed_trap_data: {year=}, {event=}, {session=}")
    session_obj = await require_session(year, event, session, load_laps=False)

    results = session_obj.results
    rows = []
    for _, row in results.iterrows():
        rows.append(
            {
                "driver": row.get("Abbreviation", ""),
                "speedTrap": float(row["SpeedST"])
                if pd.notna(row.get("SpeedST"))
                else None,
                "speedFL": float(row["SpeedFL"])
                if pd.notna(row.get("SpeedFL"))
                else None,
                "speedI1": float(row["SpeedI1"])
                if pd.notna(row.get("SpeedI1"))
                else None,
                "speedI2": float(row["SpeedI2"])
                if pd.notna(row.get("SpeedI2"))
                else None,
            }
        )

    # Sort by speed trap descending; push None to end
    rows.sort(key=lambda x: (x["speedTrap"] is None, -(x["speedTrap"] or 0)))
    return rows


@tool_handler
async def get_sector_times(
    year: int,
    event: str | int,
    session: str,
    driver: str | None = None,
) -> list[dict]:
    """
    Get best sector times and theoretical best lap for each driver.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, FP1, FP2, FP3)
        driver: Optional driver code to filter (default: all drivers)

    Returns:
        For each driver: bestS1, bestS2, bestS3, theoreticalBest,
        actualBest, gapSec (theoretical vs actual best)

    Example:
        get_sector_times(2024, "Monaco", "Q") → [
            {"driver": "VER", "bestS1": "0:00:22.123", "bestS2": "0:00:24.456",
             "bestS3": "0:00:21.789", "theoreticalBest": "0:01:08.368",
             "actualBest": "0:01:08.570", "gapSec": -0.202},
            ...
        ]

    Note:
        A negative gapSec means the theoretical best (sum of individual
        sector bests) is faster than the actual best lap — typical, since
        sector bests usually come from different laps.
    """
    logger.info(f"get_sector_times: {year=}, {event=}, {session=}, {driver=}")
    session_obj = await require_session(year, event, session)

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

        results.append(
            {
                "driver": drv,
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
        )

    # Sort by raw Timedelta ascending (fastest first); push None to end.
    _max_td = pd.Timedelta.max
    results.sort(key=lambda x: x["_theoreticalRaw"] or _max_td)
    for r in results:
        del r["_theoreticalRaw"]
    return results
