from typing import Any

import numpy as np
import pandas as pd


def results_to_json(results: Any) -> list[dict]:
    """Convert SessionResults to JSON-serializable list."""
    records = []

    for _, row in results.iterrows():
        records.append({
            "position": int(row["Position"]) if pd.notna(row["Position"]) else None,
            "driverCode": row.get("Abbreviation", row.get("Driver")),
            "driverNumber": str(row.get("DriverNumber", "")),
            "fullName": row.get("FullName", ""),
            "teamName": row.get("TeamName", ""),
            "gridPosition": int(row["GridPosition"]) if pd.notna(row.get("GridPosition")) else None,
            "status": row.get("Status", ""),
            "points": float(row["Points"]) if pd.notna(row.get("Points")) else 0,
            "time": str(row["Time"]) if pd.notna(row.get("Time")) else None,
        })

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
        "lapNumber": int(row["LapNumber"]) if pd.notna(row.get("LapNumber")) else None,
        "lapTime": str(row["LapTime"]) if pd.notna(row.get("LapTime")) else None,
        "sector1": str(row["Sector1Time"]) if pd.notna(row.get("Sector1Time")) else None,
        "sector2": str(row["Sector2Time"]) if pd.notna(row.get("Sector2Time")) else None,
        "sector3": str(row["Sector3Time"]) if pd.notna(row.get("Sector3Time")) else None,
        "compound": row.get("Compound", "UNKNOWN"),
        "tyreLife": int(row["TyreLife"]) if pd.notna(row.get("TyreLife")) else None,
        "isPersonalBest": bool(row.get("IsPersonalBest", False)),
        "deleted": bool(row.get("Deleted", False)),
    }


def telemetry_to_json(telemetry: Any, sample_size: int = 200) -> list[dict]:
    """Convert Telemetry to JSON with distance-based sampling."""
    if "Distance" not in telemetry.columns:
        telemetry = telemetry.add_distance()

    if len(telemetry) <= sample_size:
        indices = list(range(len(telemetry)))
    else:
        max_dist = telemetry["Distance"].max()
        sample_distances = np.linspace(0, max_dist, sample_size)
        indices = [
            (telemetry["Distance"] - d).abs().idxmin()
            for d in sample_distances
        ]

    records = []
    for idx in indices:
        row = telemetry.loc[idx]
        records.append({
            "distance": round(float(row["Distance"]), 1),
            "speed": round(float(row["Speed"]), 1) if pd.notna(row.get("Speed")) else None,
            "throttle": round(float(row["Throttle"]), 1) if pd.notna(row.get("Throttle")) else None,
            "brake": bool(row["Brake"]) if pd.notna(row.get("Brake")) else None,
            "gear": int(row["nGear"]) if pd.notna(row.get("nGear")) else None,
            "drs": int(row["DRS"]) if pd.notna(row.get("DRS")) else None,
        })

    return records


def standings_to_json(standings: Any, standings_type: str) -> list[dict]:
    """Convert Ergast standings response to JSON."""
    if hasattr(standings, "content") and len(standings.content) > 0:
        df = standings.content[0]
    else:
        df = standings

    records = []
    for _, row in df.iterrows():
        if standings_type == "driver":
            records.append({
                "position": int(row["position"]),
                "code": row.get("driverCode", ""),
                "name": f"{row.get('givenName', '')} {row.get('familyName', '')}".strip(),
                "team": row.get("constructorNames", [""])[0] if isinstance(row.get("constructorNames"), list) else "",
                "points": float(row["points"]),
                "wins": int(row["wins"]),
            })
        else:  # constructor
            records.append({
                "position": int(row["position"]),
                "name": row.get("constructorName", ""),
                "nationality": row.get("constructorNationality", ""),
                "points": float(row["points"]),
                "wins": int(row["wins"]),
            })

    return records
