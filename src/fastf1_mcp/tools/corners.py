"""Corner-by-corner analysis using circuit corner markers + lap telemetry."""

import asyncio
import logging

import numpy as np
import pandas as pd

from ..utils.converters import build_driver_lookup, to_jsonable
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.session_loader import require_session, tool_handler
from .telemetry import _cumulative_time_at_distances, _get_driver_lap

logger = logging.getLogger(__name__)

# Half-width cap (metres) of the window searched for a corner's apex.
_MAX_HALF_WINDOW_M = 150.0
# How far before the apex to look for the approach (top) speed and brake point.
_APPROACH_M = 350.0


async def load_circuit_info(session_obj):
    """
    Fetch FastF1's CircuitInfo (corners, marshal sectors, map rotation).

    `Session.get_circuit_info()` makes a network request and computes marker
    distances from the fastest lap's telemetry, so it runs in an executor.
    """
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, session_obj.get_circuit_info)
    except Exception as e:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"Circuit corner data unavailable: {e}",
            suggestions=["Corner markers come from the MultiViewer API — retry later"],
        ) from e
    if info is None:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE, "Circuit corner data unavailable."
        )
    return info


async def load_corners(session_obj) -> pd.DataFrame:
    """Corner markers (Number, Letter, Distance, X, Y, Angle), sorted by distance."""
    info = await load_circuit_info(session_obj)
    corners = getattr(info, "corners", None)
    if corners is None or corners.empty or "Distance" not in corners.columns:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            "Circuit corner data unavailable for this session.",
        )
    return corners.dropna(subset=["Distance"]).sort_values("Distance")


def corner_label(row) -> str:
    letter = row.get("Letter")
    letter = letter if isinstance(letter, str) else ""
    return f"T{int(row['Number'])}{letter}"


def _prepare(tel: pd.DataFrame) -> pd.DataFrame:
    if "Distance" not in tel.columns:
        tel = tel.add_distance()
    return tel.dropna(subset=["Distance", "Speed"]).sort_values("Distance")


def _corner_metrics(tel: pd.DataFrame, lo: float, hi: float) -> dict | None:
    """Apex/approach/exit metrics for one driver inside [lo, hi] metres."""
    d = tel["Distance"].to_numpy(dtype=float)
    v = tel["Speed"].to_numpy(dtype=float)
    win = (d >= lo) & (d <= hi)
    if not win.any():
        return None
    idx = np.flatnonzero(win)
    apex = idx[np.argmin(v[idx])]
    apex_d = d[apex]

    approach = (d >= apex_d - _APPROACH_M) & (d <= apex_d)
    entry_speed = float(v[approach].max()) if approach.any() else None

    brake_start = None
    if "Brake" in tel.columns:
        brake = tel["Brake"].fillna(False).astype(bool).to_numpy()
        on = np.flatnonzero(approach & brake)
        if on.size:
            brake_start = round(float(d[on[0]]), 0)

    gear = None
    if "nGear" in tel.columns and pd.notna(tel["nGear"].iloc[apex]):
        gear = int(tel["nGear"].iloc[apex])

    return {
        "minSpeedKph": round(float(v[apex]), 1),
        "apexDistanceM": round(float(apex_d), 0),
        "gear": gear,
        "entrySpeedKph": round(entry_speed, 1) if entry_speed is not None else None,
        "exitSpeedKph": round(float(np.interp(hi, d, v)), 1),
        "brakeStartM": brake_start,
    }


def corner_windows(corners: pd.DataFrame, lap_length: float) -> list[tuple]:
    """(label, marker distance, lo, hi) per corner, split at neighbour midpoints."""
    dists = corners["Distance"].to_numpy(dtype=float)
    out = []
    for i, (_, row) in enumerate(corners.iterrows()):
        dist = dists[i]
        prev_gap = dist - dists[i - 1] if i > 0 else dist
        next_gap = dists[i + 1] - dist if i < len(dists) - 1 else lap_length - dist
        lo = dist - min(_MAX_HALF_WINDOW_M, max(prev_gap, 0) / 2)
        hi = dist + min(_MAX_HALF_WINDOW_M, max(next_gap, 0) / 2)
        out.append((corner_label(row), dist, max(lo, 0.0), hi))
    return out


@tool_handler
async def get_corner_analysis(
    year: int,
    event: str | int,
    session: str,
    driver: str,
    driver2: str | None = None,
    lap: int | str = "fastest",
) -> dict:
    """
    Corner-by-corner breakdown of a lap: apex (minimum) speed, gear, approach
    speed, exit speed and brake point for every numbered corner — and, with
    a second driver, where each one gains or loses time.

    Data source: FastF1 Live Timing telemetry + MultiViewer circuit markers
    Coverage: 2018-present

    Use for "where is Leclerc faster than Verstappen", "what's the slowest
    corner at Monaco", "who brakes later into turn 1", or "how much time
    does Norris lose in the final sector corners".

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, SQ, FP1, FP2, FP3)
        driver: Driver code (e.g. "LEC")
        driver2: Optional second driver code to compare against
        lap: Lap number or "fastest" (default) — applied to each driver

    Returns:
        {
            "driver": {"code": "LEC", "lapNumber": 18, "lapTime": "..."},
            "driver2": {"code": "VER", ...} | null,
            "corners": [
                {"corner": "T1", "distanceM": 210.0,
                 "driver": {"minSpeedKph": 98.5, "gear": 3,
                            "entrySpeedKph": 290.1, "exitSpeedKph": 165.0,
                            "brakeStartM": 92.0, "apexDistanceM": 215.0},
                 "driver2": {...},
                 "minSpeedDeltaKph": 2.1,
                 "timeDeltaSec": -0.034},
                ...
            ],
            "summary": {
                "slowestCorner": {"corner": "T6", "minSpeedKph": 48.2},
                "fastestCorner": {...},
                "driverGainsMost": [{"corner": "T10", "timeDeltaSec": -0.081}, ...],
                "driverLosesMost": [...],
                "totalCornerDeltaSec": -0.112
            }
        }

    Note:
        Deltas are driver − driver2: negative timeDeltaSec = `driver` was
        quicker through that corner; positive minSpeedDeltaKph = `driver`
        carried more apex speed. Each corner's window runs halfway to its
        neighbours (max ±150 m), so the corner deltas don't sum exactly to
        the lap-time delta (straights are excluded). Corner positions are
        approximate (hand-annotated circuit data).
    """
    logger.info(
        f"get_corner_analysis: {year=}, {event=}, {session=}, {driver=}, "
        f"{driver2=}, {lap=}"
    )
    session_obj = await require_session(year, event, session, with_telemetry=True)
    lookup = build_driver_lookup(session_obj)

    codes = [driver] + ([driver2] if driver2 else [])
    laps, tels = [], []
    for code in codes:
        lap_row = _get_driver_lap(session_obj, code, lap)
        if lap_row is None:
            raise FastF1MCPError(
                ErrorCode.DRIVER_NOT_FOUND,
                f"No lap {lap!r} found for driver '{code}'",
                suggestions=["Check the driver code (e.g. 'VER', 'HAM', 'LEC')"],
            )
        try:
            tel = _prepare(lap_row.get_telemetry())
        except Exception as e:
            raise FastF1MCPError(
                ErrorCode.DATA_UNAVAILABLE,
                f"Telemetry unavailable for {code}: {e}",
            ) from e
        if tel.empty:
            raise FastF1MCPError(
                ErrorCode.DATA_UNAVAILABLE, f"Telemetry for {code} is empty."
            )
        laps.append(lap_row)
        tels.append(tel)

    corners = await load_corners(session_obj)
    lap_length = float(tels[0]["Distance"].max())

    rows = []
    for label, dist, lo, hi in corner_windows(corners, lap_length):
        m1 = _corner_metrics(tels[0], lo, hi)
        if m1 is None:
            continue
        entry: dict = {"corner": label, "distanceM": round(float(dist), 0)}
        entry["driver"] = m1
        if len(tels) == 2:
            m2 = _corner_metrics(tels[1], lo, hi)
            entry["driver2"] = m2
            if m2 is not None:
                t1 = _cumulative_time_at_distances(tels[0], np.array([lo, hi]))
                t2 = _cumulative_time_at_distances(tels[1], np.array([lo, hi]))
                entry["minSpeedDeltaKph"] = round(
                    m1["minSpeedKph"] - m2["minSpeedKph"], 1
                )
                entry["timeDeltaSec"] = round(
                    float((t1[1] - t1[0]) - (t2[1] - t2[0])), 3
                )
        rows.append(entry)

    if not rows:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            "Could not match any corner markers to the lap telemetry.",
        )

    by_speed = sorted(rows, key=lambda r: r["driver"]["minSpeedKph"])
    summary: dict = {
        "corners": len(rows),
        "slowestCorner": {
            "corner": by_speed[0]["corner"],
            "minSpeedKph": by_speed[0]["driver"]["minSpeedKph"],
        },
        "fastestCorner": {
            "corner": by_speed[-1]["corner"],
            "minSpeedKph": by_speed[-1]["driver"]["minSpeedKph"],
        },
    }
    deltas = [r for r in rows if r.get("timeDeltaSec") is not None]
    if deltas:
        ordered = sorted(deltas, key=lambda r: r["timeDeltaSec"])
        summary["driverGainsMost"] = [
            {"corner": r["corner"], "timeDeltaSec": r["timeDeltaSec"]}
            for r in ordered[:3]
            if r["timeDeltaSec"] < 0
        ]
        summary["driverLosesMost"] = [
            {"corner": r["corner"], "timeDeltaSec": r["timeDeltaSec"]}
            for r in reversed(ordered[-3:])
            if r["timeDeltaSec"] > 0
        ]
        summary["totalCornerDeltaSec"] = round(
            sum(r["timeDeltaSec"] for r in deltas), 3
        )

    def _lap_info(code: str, lap_row) -> dict:
        info = lookup.get(code, {})
        lap_time = lap_row.get("LapTime")
        lap_no = lap_row.get("LapNumber")
        return {
            "code": info.get("driverCode") or code,
            "fullName": info.get("fullName", ""),
            "teamName": info.get("teamName", ""),
            "lapNumber": int(lap_no) if pd.notna(lap_no) else None,
            "lapTime": str(lap_time) if pd.notna(lap_time) else None,
        }

    return to_jsonable(
        {
            "driver": _lap_info(driver, laps[0]),
            "driver2": _lap_info(driver2, laps[1]) if driver2 else None,
            "corners": rows,
            "summary": summary,
        }
    )
