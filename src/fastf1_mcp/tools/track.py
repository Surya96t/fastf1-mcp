"""DRS zone detection from lap telemetry."""

import logging
import math

import numpy as np
import pandas as pd

from ..utils.converters import to_jsonable
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.session_loader import require_session, tool_handler
from .corners import corner_label, load_corners

logger = logging.getLogger(__name__)

# FastF1 DRS channel: 0/1 off, 8 = eligible (detection), 10/12/14 = flap open.
DRS_OPEN_MIN = 10
# Ignore open runs shorter than this (sensor glitches).
_MIN_RUN_M = 50.0
# Runs from different laps whose starts are this close belong to one zone.
_CLUSTER_M = 250.0
# A zone must be seen on at least this share of the reference laps.
_MIN_SHARE = 0.3
# Number of reference laps used for detection.
_REFERENCE_LAPS = 8
# A race lap counts as a DRS lap with at least this many flap-open car-data
# samples (~4 Hz, so about two seconds).
_MIN_OPEN_SAMPLES = 8


def _open_runs(tel: pd.DataFrame) -> list[tuple[float, float]]:
    """Distance intervals where the DRS flap is open on one lap."""
    if "DRS" not in tel.columns:
        return []
    if "Distance" not in tel.columns:
        tel = tel.add_distance()
    tel = tel.dropna(subset=["Distance", "DRS"]).sort_values("Distance")
    d = tel["Distance"].to_numpy(dtype=float)
    is_open = tel["DRS"].to_numpy(dtype=float) >= DRS_OPEN_MIN
    runs = []
    start = None
    for i, flag in enumerate(is_open):
        if flag and start is None:
            start = d[i]
        elif not flag and start is not None:
            if d[i] - start >= _MIN_RUN_M:
                runs.append((float(start), float(d[i])))
            start = None
    if start is not None and d[-1] - start >= _MIN_RUN_M:
        runs.append((float(start), float(d[-1])))
    return runs


def detect_drs_zones(laps_tel: list[pd.DataFrame]) -> tuple[list[dict], float | None]:
    """
    Cluster DRS-open runs across several laps into zones.

    Returns (zones, lap_length_m). Each zone has median start/end distance
    and the number of reference laps it was seen on.
    """
    if not laps_tel:
        return [], None
    lengths = [
        float(t["Distance"].max())
        for t in laps_tel
        if "Distance" in t.columns and len(t)
    ]
    lap_length = float(np.median(lengths)) if lengths else None

    runs = []  # (start, end, lap_idx)
    for i, tel in enumerate(laps_tel):
        runs.extend((s, e, i) for s, e in _open_runs(tel))
    runs.sort()

    clusters: list[list[tuple[float, float, int]]] = []
    for run in runs:
        if clusters and run[0] - clusters[-1][0][0] <= _CLUSTER_M:
            clusters[-1].append(run)
        else:
            clusters.append([run])

    needed = max(1, math.ceil(len(laps_tel) * _MIN_SHARE))
    zones = []
    for cl in clusters:
        laps_seen = {r[2] for r in cl}
        if len(laps_seen) < needed:
            continue
        start = float(np.median([r[0] for r in cl]))
        end = float(np.median([r[1] for r in cl]))
        zones.append(
            {
                "startM": round(start),
                "endM": round(end),
                "lengthM": round(end - start),
                "seenOnLaps": len(laps_seen),
            }
        )
    # A zone on the main straight can straddle the start/finish line and
    # show up as two runs (end of lap + start of lap): merge them.
    if lap_length and len(zones) >= 2:
        first, last = zones[0], zones[-1]
        if last["endM"] >= lap_length - 30 and first["startM"] <= 30:
            last["endM"] = first["endM"]
            last["lengthM"] = round(lap_length - last["startM"] + first["endM"])
            last["crossesLine"] = True
            last["seenOnLaps"] = max(last["seenOnLaps"], first["seenOnLaps"])
            zones = zones[1:]
    for z in zones:
        z.setdefault("crossesLine", False)
    zones.sort(key=lambda z: z["startM"])
    for n, z in enumerate(zones, start=1):
        z["zone"] = n
    return zones, lap_length


def _fastest_laps(laps) -> list:
    """Each driver's fastest lap, quickest first."""
    fastest = []
    for drv in laps["Driver"].dropna().unique():
        lap = laps.pick_drivers(drv).pick_fastest()
        if lap is not None and pd.notna(lap.get("LapTime")):
            fastest.append(lap)
    fastest.sort(key=lambda lap: lap["LapTime"])
    return fastest[:_REFERENCE_LAPS]


def drs_lap_index(session_obj, laps) -> list:
    """
    Index labels of laps on which the DRS flap was open, most DRS use first
    and one lap per driver before any driver's second lap.

    In a race DRS only opens within one second of the car ahead, so the
    fastest laps (often set in clean air) can miss zones; laps with DRS
    use are better references there.
    """
    car_data = getattr(session_obj, "car_data", None)
    if not isinstance(car_data, dict) or not {
        "DriverNumber",
        "LapStartTime",
        "Time",
    }.issubset(getattr(laps, "columns", [])):
        return []
    scored = []  # (open samples, driver number, lap index)
    for num, car in car_data.items():
        if not isinstance(car, pd.DataFrame) or not {"DRS", "SessionTime"}.issubset(
            car.columns
        ):
            continue
        t_open = np.sort(
            car.loc[car["DRS"] >= DRS_OPEN_MIN, "SessionTime"]
            .dropna()
            .to_numpy(dtype="timedelta64[ns]")
        )
        if not len(t_open):
            continue
        drv_laps = laps[laps["DriverNumber"].astype(str) == str(num)].dropna(
            subset=["LapStartTime", "Time"]
        )
        starts = drv_laps["LapStartTime"].to_numpy(dtype="timedelta64[ns]")
        ends = drv_laps["Time"].to_numpy(dtype="timedelta64[ns]")
        counts = np.searchsorted(t_open, ends) - np.searchsorted(t_open, starts)
        scored.extend(
            (int(c), str(num), idx)
            for idx, c in zip(drv_laps.index, counts)
            if c >= _MIN_OPEN_SAMPLES
        )
    scored.sort(key=lambda s: -s[0])
    seen: set[str] = set()
    first, rest = [], []
    for item in scored:
        (rest if item[1] in seen else first).append(item)
        seen.add(item[1])
    return [idx for _, _, idx in (first + rest)[:_REFERENCE_LAPS]]


def _zones_from(ref_laps: list) -> tuple[list[dict], float | None, int]:
    tels = []
    for lap in ref_laps:
        try:
            tels.append(lap.get_telemetry())
        except Exception as e:  # one bad lap shouldn't sink the detection
            logger.debug(f"Skipping DRS reference lap: {e}")
    zones, lap_length = detect_drs_zones(tels)
    return zones, lap_length, len(tels)


def _lap_at(laps, idx):
    return laps.loc[idx]


def _is_race(session_obj) -> bool:
    return str(getattr(session_obj, "name", "")) in {"Race", "Sprint"}


async def session_drs_zones(session_obj) -> tuple[list[dict], float | None, int]:
    """
    Detect DRS zones from reference laps: the top drivers' fastest laps,
    or — in a race, or when those show no DRS use — laps on which the flap
    was actually opened. Whichever finds more zones wins.
    """
    laps = session_obj.laps
    zones, lap_length, n_ref = _zones_from(_fastest_laps(laps))
    if _is_race(session_obj) or not zones:
        idx = drs_lap_index(session_obj, laps)
        if idx:
            alt = _zones_from([_lap_at(laps, i) for i in idx])
            if len(alt[0]) > len(zones):
                return alt
    return zones, lap_length, n_ref


@tool_handler
async def get_drs_zones(
    year: int,
    event: str | int,
    session: str = "Q",
) -> dict:
    """
    Detect the DRS zones of a circuit: where each zone opens and closes,
    its length, and the corners around it.

    Data source: FastF1 Live Timing telemetry (DRS flap channel)
    Coverage: 2018-2025 (DRS was replaced by active aero from 2026)

    Zones are inferred from where the DRS flap was actually open on up to
    8 reference laps: the fastest laps of the top drivers, or in a race
    the laps with the most DRS use (DRS only opens within one second of
    the car ahead, so fastest race laps are often set without it).
    Qualifying (default) is the best source: drivers may use DRS in every
    zone on every flying lap.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (default "Q"; R, S, SQ, FP1-FP3 also work)

    Returns:
        {
            "lapLengthM": 5412,
            "referenceLaps": 8,
            "zones": [
                {"zone": 1, "startM": 4870, "endM": 5390, "lengthM": 520,
                 "afterCorner": "T14", "beforeCorner": "T1",
                 "seenOnLaps": 8},
                ...
            ]
        }

    Note:
        Distances are metres from the start/finish line along the
        reference laps. `startM` is where drivers opened the flap, which
        is at or just after the official activation point.
        `afterCorner` / `beforeCorner` need circuit corner data and are
        null when it is unavailable.
    """
    logger.info(f"get_drs_zones: {year=}, {event=}, {session=}")
    session_obj = await require_session(year, event, session, with_telemetry=True)
    zones, lap_length, n_ref = await session_drs_zones(session_obj)

    if not zones:
        suggestions = ["Try session='Q' — DRS use is unrestricted in qualifying"]
        if year >= 2026:
            suggestions.insert(
                0,
                "From 2026 DRS was replaced by active aerodynamics, so the "
                "DRS channel no longer marks overtaking zones",
            )
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"No DRS activations found in {year} {event} {session} "
            "(wet session, DRS disabled, or no DRS data).",
            suggestions=suggestions,
        )

    try:
        corners = await load_corners(session_obj)
        marks = [(corner_label(r), float(r["Distance"])) for _, r in corners.iterrows()]
    except FastF1MCPError:
        marks = []
    for z in zones:
        before = [lbl for lbl, d in marks if d <= z["startM"]]
        after = [lbl for lbl, d in marks if d >= z["endM"] - 50]
        z["afterCorner"] = before[-1] if before else (marks[-1][0] if marks else None)
        z["beforeCorner"] = after[0] if after else (marks[0][0] if marks else None)

    return to_jsonable(
        {
            "lapLengthM": round(lap_length) if lap_length else None,
            "referenceLaps": n_ref,
            "zones": zones,
        }
    )
