from typing import Any

import numpy as np
import pandas as pd


def build_driver_lookup(session_obj: Any) -> dict[str, dict]:
    """
    Build a {key → driver info} map from session.results.

    The map is keyed by BOTH the driver code (Abbreviation, e.g. 'VER') and
    the driver number (e.g. '1'), so callers can look up using whichever the
    underlying FastF1 API hands them — `session.drivers` returns numbers,
    `laps['Driver']` returns codes.

    Each value is {"driverCode", "fullName", "teamName"}.
    """
    lookup: dict[str, dict] = {}
    results = getattr(session_obj, "results", None)
    if results is None or not hasattr(results, "iterrows"):
        return lookup

    for _, row in results.iterrows():
        code = (row.get("Abbreviation") or "") if hasattr(row, "get") else ""
        number = str(row.get("DriverNumber") or "") if hasattr(row, "get") else ""
        entry = {
            "driverCode": code,
            "fullName": row.get("FullName", "") or "",
            "teamName": row.get("TeamName", "") or "",
        }
        if code:
            lookup[code] = entry
        if number:
            lookup[number] = entry
    return lookup


def results_to_json(results: Any) -> list[dict]:
    """Convert SessionResults to JSON-serializable list."""
    records = []

    for _, row in results.iterrows():
        position = int(row["Position"]) if pd.notna(row["Position"]) else None
        time = row.get("Time")
        status = row.get("Status", "") or ""

        # `gap` synthesises a single human-readable result string so the
        # caller can always answer "what was the gap?" for any classified
        # driver — FastF1 leaves `Time` as NaT for lapped finishers and
        # only encodes their margin in `Status` ("+1 Lap", "+2 Laps").
        if pd.notna(time):
            gap = "leader" if position == 1 else f"+{time}"
        elif status and status not in {"Finished", ""}:
            gap = status
        else:
            gap = None

        records.append(
            {
                "position": position,
                "driverCode": row.get("Abbreviation", row.get("Driver")),
                "driverNumber": str(row.get("DriverNumber", "")),
                "fullName": row.get("FullName", ""),
                "teamName": row.get("TeamName", ""),
                "gridPosition": int(row["GridPosition"])
                if pd.notna(row.get("GridPosition"))
                else None,
                "status": status,
                "points": float(row["Points"]) if pd.notna(row.get("Points")) else 0,
                "time": str(time) if pd.notna(time) else None,
                "gap": gap,
            }
        )

    return records


def laps_to_json(laps: Any) -> list[dict]:
    """Convert Laps/Lap to JSON-serializable list."""
    if hasattr(laps, "iterrows"):
        return [_lap_row_to_dict(row) for _, row in laps.iterrows()]
    elif hasattr(laps, "__iter__"):
        return [_lap_row_to_dict(lap) for lap in laps]
    else:
        return [_lap_row_to_dict(laps)]


def _lap_row_to_dict(row: Any) -> dict:
    """Convert a single lap row to dict."""
    return {
        "driver": row.get("Driver") if pd.notna(row.get("Driver")) else None,
        "lapNumber": int(row["LapNumber"]) if pd.notna(row.get("LapNumber")) else None,
        "lapTime": str(row["LapTime"]) if pd.notna(row.get("LapTime")) else None,
        "sector1": str(row["Sector1Time"])
        if pd.notna(row.get("Sector1Time"))
        else None,
        "sector2": str(row["Sector2Time"])
        if pd.notna(row.get("Sector2Time"))
        else None,
        "sector3": str(row["Sector3Time"])
        if pd.notna(row.get("Sector3Time"))
        else None,
        "compound": row.get("Compound", "UNKNOWN"),
        "tyreLife": int(row["TyreLife"]) if pd.notna(row.get("TyreLife")) else None,
        "isPersonalBest": bool(row.get("IsPersonalBest", False)),
        "deleted": bool(row.get("Deleted", False)),
    }


def telemetry_to_json(telemetry: Any, sample_size: int = 200) -> list[dict]:
    """Convert Telemetry to JSON with distance-based sampling."""
    if "Distance" not in telemetry.columns:
        telemetry = telemetry.add_distance()

    n = len(telemetry)
    if n <= sample_size:
        positions = list(range(n))
    else:
        # Distance must be sorted (and NaN-free) for searchsorted to work.
        # pandas.sort_values puts NaN at the end, which would make max_dist NaN.
        sorted_tel = telemetry.sort_values("Distance").dropna(subset=["Distance"])
        if sorted_tel.empty:
            return []
        distance_array = sorted_tel["Distance"].to_numpy()
        valid_n = len(distance_array)
        max_dist = distance_array[-1]
        sample_distances = np.linspace(0, max_dist, sample_size)
        # Pick the closest of the two bracketing samples for each target.
        right = np.searchsorted(distance_array, sample_distances)
        right = np.clip(right, 1, valid_n - 1)
        left = right - 1
        choose_right = np.abs(distance_array[right] - sample_distances) < np.abs(
            distance_array[left] - sample_distances
        )
        positions = np.where(choose_right, right, left)
        telemetry = sorted_tel

    records = []
    for pos in positions:
        row = telemetry.iloc[pos]
        records.append(
            {
                "distance": round(float(row["Distance"]), 1),
                "speed": round(float(row["Speed"]), 1)
                if pd.notna(row.get("Speed"))
                else None,
                "throttle": round(float(row["Throttle"]), 1)
                if pd.notna(row.get("Throttle"))
                else None,
                "brake": bool(row["Brake"]) if pd.notna(row.get("Brake")) else None,
                "gear": int(row["nGear"]) if pd.notna(row.get("nGear")) else None,
                "drs": int(row["DRS"]) if pd.notna(row.get("DRS")) else None,
            }
        )

    return records


def lap_times_summary(laps_data: list[dict]) -> dict:
    """
    Build summary stats for a per-lap dict list (output of laps_to_json).

    The summary lets a caller answer "what's the fastest lap / what compound
    distribution" without re-parsing the full per-lap array — important when
    the full array is large enough that MCP clients spill it to a file.
    """
    if not laps_data:
        return {
            "totalLaps": 0,
            "validLaps": 0,
            "fastestLap": None,
            "slowestLap": None,
            "avgLapTime": None,
            "compoundDistribution": {},
        }

    timed = []
    for lap in laps_data:
        t = lap.get("lapTime")
        if not t or lap.get("deleted"):
            continue
        try:
            td = pd.to_timedelta(t)
        except (ValueError, TypeError):
            continue
        timed.append((td, lap))

    timed.sort(key=lambda x: x[0])
    fastest_td, fastest = timed[0] if timed else (None, None)
    slowest_td, slowest = timed[-1] if timed else (None, None)

    avg_lap_time = None
    if timed:
        total = sum((td for td, _ in timed), pd.Timedelta(0))
        avg_lap_time = str(total / len(timed))

    compounds: dict[str, int] = {}
    for lap in laps_data:
        c = lap.get("compound") or "UNKNOWN"
        compounds[c] = compounds.get(c, 0) + 1

    return {
        "totalLaps": len(laps_data),
        "validLaps": len(timed),
        "fastestLap": {
            "lapNumber": fastest["lapNumber"],
            "lapTime": fastest["lapTime"],
            "compound": fastest.get("compound"),
        }
        if fastest
        else None,
        "slowestLap": {
            "lapNumber": slowest["lapNumber"],
            "lapTime": slowest["lapTime"],
        }
        if slowest
        else None,
        "avgLapTime": avg_lap_time,
        "compoundDistribution": compounds,
    }


def stint_analysis_summary(stints: list[dict]) -> dict:
    """
    Build a "strategies" summary from the per-stint detail array.

    Per-driver compound sequence + stint lengths is what users actually ask
    about (1-stop vs 2-stop, who came out on top) and is far smaller than
    the full per-stint table.
    """
    if not stints:
        return {
            "totalStints": 0,
            "driversCount": 0,
            "compoundsUsed": {},
            "strategies": [],
        }

    by_driver: dict[str, list[dict]] = {}
    for s in stints:
        by_driver.setdefault(s["driver"], []).append(s)

    strategies = []
    for drv, drv_stints in by_driver.items():
        drv_stints_sorted = sorted(drv_stints, key=lambda x: x["stintNumber"])
        first = drv_stints_sorted[0]
        strategies.append(
            {
                "driver": drv,
                "fullName": first.get("fullName", ""),
                "teamName": first.get("teamName", ""),
                "compoundSequence": [s["compound"] for s in drv_stints_sorted],
                "stintLaps": [s["lapCount"] for s in drv_stints_sorted],
                "pitStops": max(len(drv_stints_sorted) - 1, 0),
            }
        )
    strategies.sort(key=lambda s: s["driver"])

    compounds: dict[str, int] = {}
    for s in stints:
        c = s.get("compound") or "UNKNOWN"
        compounds[c] = compounds.get(c, 0) + 1

    return {
        "totalStints": len(stints),
        "driversCount": len(by_driver),
        "compoundsUsed": compounds,
        "strategies": strategies,
    }


def telemetry_summary(data: list[dict]) -> dict:
    """
    Aggregate stats over a sampled telemetry array.

    Lets the agent answer "what was the max speed / how many braking zones"
    without iterating the full per-distance point array.
    """
    if not data:
        return {
            "samplePoints": 0,
            "maxSpeedKph": None,
            "minSpeedKph": None,
            "avgSpeedKph": None,
            "maxGear": None,
            "brakingZones": 0,
            "fullThrottlePct": None,
        }

    speeds = [p["speed"] for p in data if p.get("speed") is not None]
    throttles = [p["throttle"] for p in data if p.get("throttle") is not None]
    gears = [p["gear"] for p in data if p.get("gear") is not None]

    # Brake applications = rising-edge count on the boolean brake trace.
    brake_zones = 0
    prev = False
    for p in data:
        b = bool(p.get("brake")) if p.get("brake") is not None else False
        if b and not prev:
            brake_zones += 1
        prev = b

    full_throttle_pct = None
    if throttles:
        full_throttle_pct = round(
            100.0 * sum(1 for t in throttles if t >= 95.0) / len(throttles),
            1,
        )

    return {
        "samplePoints": len(data),
        "maxSpeedKph": round(max(speeds), 1) if speeds else None,
        "minSpeedKph": round(min(speeds), 1) if speeds else None,
        "avgSpeedKph": round(sum(speeds) / len(speeds), 1) if speeds else None,
        "maxGear": max(gears) if gears else None,
        "brakingZones": brake_zones,
        "fullThrottlePct": full_throttle_pct,
    }


def standings_to_json(standings: Any, standings_type: str) -> list[dict]:
    """Convert Ergast standings response to JSON."""
    if hasattr(standings, "content") and len(standings.content) > 0:
        df = standings.content[0]
    else:
        df = standings

    records = []
    for _, row in df.iterrows():
        if standings_type == "driver":
            records.append(
                {
                    "position": int(row["position"]),
                    "code": row.get("driverCode", ""),
                    "name": f"{row.get('givenName', '')} {row.get('familyName', '')}".strip(),
                    "team": row.get("constructorNames", [""])[0]
                    if isinstance(row.get("constructorNames"), list)
                    else "",
                    "points": float(row["points"]),
                    "wins": int(row["wins"]),
                }
            )
        else:  # constructor
            records.append(
                {
                    "position": int(row["position"]),
                    "name": row.get("constructorName", ""),
                    "nationality": row.get("constructorNationality", ""),
                    "points": float(row["points"]),
                    "wins": int(row["wins"]),
                }
            )

    return records
