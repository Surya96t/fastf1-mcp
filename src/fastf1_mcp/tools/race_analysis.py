"""Race progression (positions, gaps, lead changes) and tyre degradation tools."""

import itertools
import logging

import numpy as np
import pandas as pd

from ..utils.converters import build_driver_lookup, to_jsonable
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.exports import apply_export
from ..utils.session_loader import require_session, tool_handler
from ..utils.timing import to_seconds

logger = logging.getLogger(__name__)


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None and pd.notna(value) else None
    except (TypeError, ValueError):
        return None


def _normalise_drivers(drivers: list[str] | str | None) -> list[str] | None:
    """Accept ["VER", "LEC"] or "VER,LEC" (LLMs send both)."""
    if drivers is None:
        return None
    if isinstance(drivers, str):
        drivers = [d for d in drivers.replace(" ", ",").split(",") if d]
    return [str(d).upper() for d in drivers] or None


# =============================================================================
# Race progression
# =============================================================================


@tool_handler
async def get_position_changes(
    year: int,
    event: str | int,
    session: str = "R",
    drivers: list[str] | None = None,
    include_laps: bool = True,
    export_path: bool | str = False,
) -> dict:
    """
    Get how a race unfolded: lap-by-lap positions, gaps to the leader,
    positions gained from the grid, laps led and lead changes.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Use for "how did the race unfold", "who gained the most places",
    "when did X take the lead", "how far behind was Y at half distance",
    or "how many places did Z make up on lap 1".

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: "R" (race, default) or "S" (sprint)
        drivers: Optional driver codes to include (e.g. ["VER", "NOR"]).
                 The session-wide `summary` always covers the full field.
        include_laps: Include the per-lap `positions` and `gapsToLeaderSec`
                      arrays for each driver (default True). Set False for
                      a compact grid→finish movement table.
        export_path: If True (or a directory / `.csv` path), write a long
                     format CSV (one row per driver per lap: lap, driver,
                     position, gapToLeaderSec) and omit the per-lap arrays.

    Returns:
        {
            "totalLaps": 57,
            "summary": {
                "winner": "VER",
                "lapLeaders": {"VER": 50, "LEC": 7},
                "leadChanges": [{"lap": 21, "from": "VER", "to": "LEC"}, ...],
                "biggestGainers": [{"driver": "HAM", "grid": 18, "finish": 6,
                                    "positionsGained": 12}, ...],
                "biggestLosers": [...]
            },
            "drivers": [
                {"driver": "VER", "fullName": "Max Verstappen",
                 "teamName": "Red Bull Racing", "grid": 1, "finish": 1,
                 "status": "Finished", "positionsGained": 0, "lap1Gain": 0,
                 "lapsLed": 50, "bestPosition": 1, "worstPosition": 2,
                 "positions": [1, 1, 1, ...],
                 "gapsToLeaderSec": [0.0, 0.0, ...]},
                ...
            ]
        }

    Note:
        `positions[i]` / `gapsToLeaderSec[i]` are for lap i+1. The gap is
        the time between the leader completing that lap and this driver
        completing the same lap (the classic "race history" gap, so lapped
        cars show their full deficit). Pit-lane starters have grid 0 in
        the source data; their gain is computed from the back of the grid.
    """
    logger.info(f"get_position_changes: {year=}, {event=}, {session=}, {drivers=}")
    session_obj = await require_session(year, event, session)
    laps = session_obj.laps
    required = {"Driver", "LapNumber", "Position", "Time"}
    if laps is None or not required.issubset(getattr(laps, "columns", [])):
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"Lap position data is unavailable for {year} {event} {session}.",
        )

    lookup = build_driver_lookup(session_obj)
    df = laps[["Driver", "LapNumber", "Position", "Time"]].dropna(
        subset=["Driver", "LapNumber"]
    )
    df = df.assign(
        LapNumber=df["LapNumber"].astype(int),
        _t=df["Time"].map(to_seconds),
    )
    total_laps = int(df["LapNumber"].max()) if len(df) else 0

    # Leader per lap = first car to complete it.
    timed = df.dropna(subset=["_t"])
    leader_time = timed.groupby("LapNumber")["_t"].min()
    leader_by_lap = (
        timed.loc[timed.groupby("LapNumber")["_t"].idxmin()]
        .set_index("LapNumber")["Driver"]
        .sort_index()
    )

    results = getattr(session_obj, "results", None)
    result_rows: dict[str, dict] = {}
    if results is not None and hasattr(results, "iterrows"):
        for _, row in results.iterrows():
            code = row.get("Abbreviation") or ""
            if code:
                result_rows[code] = row
    field_size = max(len(result_rows), df["Driver"].nunique())

    entries = []
    long_rows = []
    for drv, drv_laps in df.sort_values("LapNumber").groupby("Driver", sort=False):
        info = lookup.get(drv, {})
        code = info.get("driverCode") or drv
        res = result_rows.get(code)

        last_lap = int(drv_laps["LapNumber"].max())
        pos_by_lap = dict(zip(drv_laps["LapNumber"], drv_laps["Position"]))
        t_by_lap = dict(zip(drv_laps["LapNumber"], drv_laps["_t"]))
        positions = [_int_or_none(pos_by_lap.get(n)) for n in range(1, last_lap + 1)]
        gaps = []
        for n in range(1, last_lap + 1):
            t = t_by_lap.get(n)
            lead = leader_time.get(n)
            gaps.append(
                round(t - lead, 3)
                if t is not None and not pd.isna(t) and lead is not None
                else None
            )

        grid = _int_or_none(res.get("GridPosition")) if res is not None else None
        finish = _int_or_none(res.get("Position")) if res is not None else None
        effective_grid = field_size if grid == 0 else grid
        valid_pos = [p for p in positions if p is not None]

        entry = {
            "driver": code,
            "fullName": info.get("fullName", ""),
            "teamName": info.get("teamName", ""),
            "grid": grid,
            "finish": finish,
            "status": (res.get("Status") or None) if res is not None else None,
            "positionsGained": effective_grid - finish
            if effective_grid and finish
            else None,
            "lap1Gain": effective_grid - positions[0]
            if effective_grid and positions and positions[0]
            else None,
            "lapsLed": int((leader_by_lap == drv).sum()),
            "bestPosition": min(valid_pos) if valid_pos else None,
            "worstPosition": max(valid_pos) if valid_pos else None,
        }
        if include_laps and not export_path:
            entry["positions"] = positions
            entry["gapsToLeaderSec"] = gaps
        entries.append(entry)
        for n, (p, g) in enumerate(zip(positions, gaps), start=1):
            long_rows.append(
                {"lap": n, "driver": code, "position": p, "gapToLeaderSec": g}
            )

    entries.sort(
        key=lambda e: (
            e["finish"] is None,
            e["finish"] or 0,
            -(len(e.get("positions") or [])),
        )
    )

    # Session-wide summary
    lap_leaders: dict[str, int] = {}
    lead_changes = []
    prev = None
    for lap_no, drv in leader_by_lap.items():
        code = lookup.get(drv, {}).get("driverCode") or drv
        lap_leaders[code] = lap_leaders.get(code, 0) + 1
        if prev is not None and code != prev:
            lead_changes.append({"lap": int(lap_no), "from": prev, "to": code})
        prev = code

    movers = [e for e in entries if e["positionsGained"] is not None]

    def _mover(e: dict) -> dict:
        return {
            "driver": e["driver"],
            "grid": e["grid"],
            "finish": e["finish"],
            "positionsGained": e["positionsGained"],
        }

    gainers = sorted(movers, key=lambda e: -e["positionsGained"])
    losers = sorted(movers, key=lambda e: e["positionsGained"])
    winner = next((e["driver"] for e in entries if e["finish"] == 1), None)

    summary = {
        "winner": winner,
        "lapLeaders": dict(sorted(lap_leaders.items(), key=lambda kv: -kv[1])),
        "leadChanges": lead_changes,
        "biggestGainers": [_mover(e) for e in gainers[:3] if e["positionsGained"] > 0],
        "biggestLosers": [_mover(e) for e in losers[:3] if e["positionsGained"] < 0],
    }

    wanted = _normalise_drivers(drivers)
    if wanted:
        entries = [e for e in entries if e["driver"] in wanted]
        long_rows = [r for r in long_rows if r["driver"] in wanted]
        if not entries:
            raise FastF1MCPError(
                ErrorCode.DRIVER_NOT_FOUND,
                f"None of {wanted} took part in {year} {event} {session}.",
                suggestions=["Use list_drivers(year, event) for valid codes"],
            )

    response: dict = {
        "totalLaps": total_laps,
        "summary": summary,
        "drivers": entries,
    }
    if export_path:
        apply_export(
            response,
            long_rows,
            bulk_key="laps",
            export_path=export_path,
            tool="get_position_changes",
            parts=[year, event, session, *(wanted or [])],
        )
    return to_jsonable(response)


# =============================================================================
# Tyre degradation
# =============================================================================


def _fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float | None]:
    """Least-squares line → (slope, intercept, r²)."""
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    return float(slope), float(intercept), r2


@tool_handler
async def get_tyre_degradation(
    year: int,
    event: str | int,
    session: str = "R",
    driver: str | None = None,
    min_stint_laps: int = 6,
    fuel_correction_sec_per_lap: float = 0.06,
    export_path: bool | str = False,
) -> dict:
    """
    Estimate tyre degradation (seconds lost per lap of tyre age) per stint
    and per compound, with fuel-effect correction.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Use for "how fast did the softs degrade", "which compound was best",
    "why did X pit so early", "when does the medium overtake the soft",
    or long-run analysis in practice (session="FP2").

    Method: for each stint, keep accurate green-flag laps (no in/out laps,
    no lap 1), drop outliers slower than 107% of the stint median, add
    `fuel_correction_sec_per_lap × (lap − 1)` to remove the fuel-burn
    effect, then fit lap time against tyre age. The slope is the
    degradation rate.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: "R" (default), "S", or a practice session ("FP1"-"FP3")
        driver: Optional driver code to restrict to one driver
        min_stint_laps: Minimum clean laps a stint needs to be fitted (default 6)
        fuel_correction_sec_per_lap: Lap-time gain per lap from fuel burn
                                     (default 0.06 s/lap; 0 disables)
        export_path: If True (or a directory / `.csv` path), write the
                     per-stint table to CSV and omit `stints`.

    Returns:
        {
            "method": {"fuelCorrectionSecPerLap": 0.06, "minStintLaps": 6},
            "summary": {
                "compounds": {
                    "MEDIUM": {"stints": 14, "medianDegSecPerLap": 0.051,
                               "medianFreshTyrePaceSec": 93.42,
                               "avgStintLaps": 21.3},
                    "HARD": {...}
                },
                "compoundCrossovers": [
                    {"faster": "MEDIUM", "slower": "HARD",
                     "freshPaceGapSec": 0.42, "crossoverTyreAge": 18}
                ],
                "lowestDeg": [{"driver": "HAM", "compound": "HARD",
                               "degSecPerLap": 0.012}, ...]
            },
            "stints": [
                {"driver": "VER", "stintNumber": 2, "compound": "HARD",
                 "startLap": 18, "endLap": 57, "lapsUsed": 33,
                 "tyreAgeStart": 1, "tyreAgeEnd": 40,
                 "degSecPerLap": 0.031, "rawSlopeSecPerLap": -0.029,
                 "r2": 0.62, "freshTyrePaceSec": 93.1, "avgLapTimeSec": 94.2},
                ...
            ]
        }

    Note:
        Degradation is an estimate — traffic, dirty air and push/lift
        phases add noise; check `r2` (closer to 1 = cleaner trend) and
        `lapsUsed`. `freshTyrePaceSec` is the fitted, fuel-corrected lap
        time at tyre age 1, comparable across stints and drivers.
        `crossoverTyreAge` is the tyre age at which the faster-when-fresh
        compound's higher degradation makes it slower than the other.
    """
    logger.info(
        f"get_tyre_degradation: {year=}, {event=}, {session=}, {driver=}, "
        f"{min_stint_laps=}, {fuel_correction_sec_per_lap=}"
    )
    min_stint_laps = max(3, int(min_stint_laps))
    session_obj = await require_session(year, event, session)
    lookup = build_driver_lookup(session_obj)

    laps = session_obj.laps
    if driver is not None:
        laps = laps.pick_drivers(driver)
        if len(laps) == 0:
            raise FastF1MCPError(
                ErrorCode.DRIVER_NOT_FOUND,
                f"No laps found for driver '{driver}' in {year} {event} {session}",
                suggestions=["Check the driver code (e.g. 'VER', 'HAM', 'LEC')"],
            )

    clean = laps.pick_accurate().pick_wo_box()
    if "TrackStatus" in clean.columns:
        clean = clean.pick_track_status("1", how="equals")
    clean = clean[clean["LapNumber"] > 1].dropna(subset=["LapTime", "Stint"])

    stints = []
    for (drv, stint_no), grp in clean.groupby(["Driver", "Stint"]):
        secs = grp["LapTime"].map(to_seconds).astype(float)
        keep = secs <= secs.median() * 1.07
        grp, secs = grp[keep], secs[keep]
        if len(grp) < min_stint_laps:
            continue

        lap_no = grp["LapNumber"].astype(float).to_numpy()
        if "TyreLife" in grp.columns and grp["TyreLife"].notna().all():
            age = grp["TyreLife"].astype(float).to_numpy()
        else:
            age = lap_no - lap_no.min() + 1
        raw = secs.to_numpy()
        corrected = raw + fuel_correction_sec_per_lap * (lap_no - 1)

        slope, intercept, r2 = _fit(age, corrected)
        raw_slope, _, _ = _fit(age, raw)
        compound = (
            grp["Compound"].dropna().iloc[0]
            if "Compound" in grp.columns and grp["Compound"].notna().any()
            else "UNKNOWN"
        )
        all_stint = laps[(laps["Driver"] == drv) & (laps["Stint"] == stint_no)]
        info = lookup.get(drv, {})
        stints.append(
            {
                "driver": info.get("driverCode") or drv,
                "fullName": info.get("fullName", ""),
                "teamName": info.get("teamName", ""),
                "stintNumber": int(stint_no),
                "compound": compound,
                "startLap": int(all_stint["LapNumber"].min()),
                "endLap": int(all_stint["LapNumber"].max()),
                "lapsUsed": len(grp),
                "tyreAgeStart": int(age.min()),
                "tyreAgeEnd": int(age.max()),
                "degSecPerLap": round(slope, 3),
                "rawSlopeSecPerLap": round(raw_slope, 3),
                "r2": round(r2, 2) if r2 is not None else None,
                "freshTyrePaceSec": round(intercept + slope, 3),
                "avgLapTimeSec": round(float(raw.mean()), 3),
            }
        )

    if not stints:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"No stint had {min_stint_laps}+ clean laps in {year} {event} {session}.",
            suggestions=[
                "Lower min_stint_laps",
                "Practice sessions often have short runs — try FP2 long runs "
                "with min_stint_laps=5",
            ],
        )

    stints.sort(key=lambda s: (s["driver"], s["stintNumber"]))

    # Per-compound summary
    compounds: dict[str, dict] = {}
    for comp, group in itertools.groupby(
        sorted(stints, key=lambda s: s["compound"]), key=lambda s: s["compound"]
    ):
        group = list(group)
        compounds[comp] = {
            "stints": len(group),
            "medianDegSecPerLap": round(
                float(np.median([s["degSecPerLap"] for s in group])), 3
            ),
            "medianFreshTyrePaceSec": round(
                float(np.median([s["freshTyrePaceSec"] for s in group])), 3
            ),
            "avgStintLaps": round(
                float(np.mean([s["endLap"] - s["startLap"] + 1 for s in group])), 1
            ),
        }

    crossovers = []
    for a, b in itertools.combinations(compounds, 2):
        ca, cb = compounds[a], compounds[b]
        fast, slow = (
            (a, b)
            if ca["medianFreshTyrePaceSec"] <= cb["medianFreshTyrePaceSec"]
            else (b, a)
        )
        f, s = compounds[fast], compounds[slow]
        pace_gap = s["medianFreshTyrePaceSec"] - f["medianFreshTyrePaceSec"]
        deg_gap = f["medianDegSecPerLap"] - s["medianDegSecPerLap"]
        crossovers.append(
            {
                "faster": fast,
                "slower": slow,
                "freshPaceGapSec": round(pace_gap, 3),
                "crossoverTyreAge": int(np.ceil(pace_gap / deg_gap)) + 1
                if deg_gap > 0
                else None,
            }
        )

    lowest = sorted(
        (s for s in stints if s["r2"] is not None and s["r2"] >= 0.3),
        key=lambda s: s["degSecPerLap"],
    )[:5]

    response: dict = {
        "method": {
            "fuelCorrectionSecPerLap": fuel_correction_sec_per_lap,
            "minStintLaps": min_stint_laps,
            "outlierThreshold": "107% of stint median",
        },
        "summary": {
            "compounds": compounds,
            "compoundCrossovers": crossovers,
            "lowestDeg": [
                {
                    "driver": s["driver"],
                    "compound": s["compound"],
                    "stintNumber": s["stintNumber"],
                    "degSecPerLap": s["degSecPerLap"],
                }
                for s in lowest
            ],
        },
    }
    apply_export(
        response,
        stints,
        bulk_key="stints",
        export_path=export_path,
        tool="get_tyre_degradation",
        parts=[year, event, session, driver],
    )
    return to_jsonable(response)
