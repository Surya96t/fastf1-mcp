"""Race control messages, track status and weather tools."""

import logging
import re

import pandas as pd

from ..utils.converters import build_driver_lookup, to_jsonable
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.exports import apply_export
from ..utils.session_loader import require_session, tool_handler
from ..utils.timing import LapClock, neutralisation_periods, to_seconds

logger = logging.getLogger(__name__)

_CAR_CODE_RE = re.compile(r"\b(\d{1,2}) \(([A-Z]{3})\)")
_CAR_RE = re.compile(r"\bCARS? (\d{1,2})\b")
_PENALTY_RE = re.compile(
    r"(\d+ SECOND (?:TIME |STOP/GO )?PENALTY|DRIVE[- ]THROUGH PENALTY|"
    r"STOP/GO PENALTY|GRID PENALTY|\d+ PLACE GRID PENALTY|REPRIMAND|"
    r"DISQUALIFIED|BLACK AND WHITE FLAG)"
)


def _message_drivers(row, lookup: dict) -> list[str]:
    """
    Driver codes a race-control message refers to, in order of appearance.

    Uses the `RacingNumber` field when present, then every "44 (HAM)" style
    reference in the text (incidents often name two or more cars), then a
    bare "CAR 44" resolved through the session's driver lookup.
    """
    codes: list[str] = []

    def _add(code: str | None) -> None:
        if code and code not in codes:
            codes.append(code)

    number = row.get("RacingNumber")
    if number is not None and pd.notna(number):
        info = lookup.get(str(number))
        _add(info["driverCode"] if info and info["driverCode"] else str(number))

    text = str(row.get("Message") or "")
    for _num, code in _CAR_CODE_RE.findall(text):
        _add(code)
    if not codes:
        m = _CAR_RE.search(text)
        if m:
            info = lookup.get(m.group(1))
            _add(info["driverCode"] if info and info["driverCode"] else m.group(1))
    return codes


def _classify(message: str) -> str | None:
    """Tag the incident-relevant messages users usually ask about."""
    if "UNDER INVESTIGATION" in message or "WILL BE INVESTIGATED" in message:
        return "investigation"
    if "NO FURTHER" in message and "INVESTIGAT" in message:
        return "no_further_action"
    if "NOTED" in message and "INCIDENT" in message:
        return "noted"
    if "DELETED" in message and ("TRACK LIMITS" in message or "TIME" in message):
        return "lap_deleted"
    if _PENALTY_RE.search(message):
        return "penalty"
    return None


@tool_handler
async def get_race_control_messages(
    year: int,
    event: str | int,
    session: str = "R",
    category: str | None = None,
    driver: str | None = None,
    lap_start: int | None = None,
    lap_end: int | None = None,
    export_path: bool | str = False,
) -> dict:
    """
    Get race control messages, penalties, investigations and SC/VSC/red flag periods.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Use this to answer "why was there a safety car", "who got a penalty",
    "what happened on lap 34", "how many laps were deleted for track limits",
    or to explain strategy calls that were triggered by a neutralisation.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type — R, Q, S, SQ, FP1, FP2, FP3 (default "R")
        category: Optional filter on the FastF1 category — "Flag",
                  "SafetyCar", "Drs", "CarEvent", "Other" (case-insensitive)
        driver: Optional driver code or number — only messages about that car
        lap_start: Optional first lap to include (inclusive)
        lap_end: Optional last lap to include (inclusive)
        export_path: If True (or a directory / `.csv` path), write the full
                     message list to CSV and omit `messages` from the
                     response. Large lists (> FASTF1_MCP_AUTO_EXPORT_ROWS,
                     default 50) auto-export; narrow with the filters above
                     to keep them inline.

    Returns:
        {
            "summary": {
                "totalMessages": 142,
                "byCategory": {"Flag": 80, "Other": 40, ...},
                "neutralisations": [
                    {"type": "SC", "startLap": 34, "endLap": 37,
                     "durationSec": 294.9, ...}
                ],
                "redFlags": 0,
                "penalties": [
                    {"lap": 12, "driver": "SAR", "penalty": "5 SECOND TIME PENALTY",
                     "message": "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 2 ..."}
                ],
                "investigations": [...],
                "trackLimitsDeletions": {"HAM": 3, "VER": 1}
            },
            "messages": [
                {"time": "2024-07-07T14:35:12", "lap": 12, "category": "Flag",
                 "flag": "YELLOW", "scope": "Sector", "sector": 7,
                 "driver": null, "tag": null, "message": "YELLOW IN TRACK SECTOR 7"},
                ...
            ]
        }

    Note:
        `summary` is computed over the whole session, before the
        category/driver/lap filters are applied, so the neutralisation and
        penalty overview is always complete. `neutralisations` comes from
        the track-status feed (more reliable than parsing message text).
    """
    logger.info(
        f"get_race_control_messages: {year=}, {event=}, {session=}, "
        f"{category=}, {driver=}, {lap_start=}, {lap_end=}"
    )
    session_obj = await require_session(year, event, session)
    rcm = getattr(session_obj, "race_control_messages", None)
    if rcm is None or not hasattr(rcm, "columns") or rcm.empty:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"No race control messages for {year} {event} {session}.",
        )

    lookup = build_driver_lookup(session_obj)
    clock = LapClock.from_laps(getattr(session_obj, "laps", None))

    rows: list[dict] = []
    for _, r in rcm.iterrows():
        msg = str(r.get("Message") or "")
        lap = r.get("Lap")
        sector = r.get("Sector")
        drivers = _message_drivers(r, lookup)
        rows.append(
            {
                "time": r.get("Time"),
                "lap": int(lap) if lap is not None and pd.notna(lap) else None,
                "category": r.get("Category"),
                "flag": r.get("Flag"),
                "scope": r.get("Scope"),
                "sector": int(sector)
                if sector is not None and pd.notna(sector)
                else None,
                "driver": drivers[0] if drivers else None,
                "drivers": drivers,
                "tag": _classify(msg),
                "message": msg,
            }
        )
    rows = to_jsonable(rows)

    # ------------------------------------------------------------------
    # Session-wide summary (pre-filter)
    # ------------------------------------------------------------------
    by_category: dict[str, int] = {}
    for r in rows:
        key = r["category"] or "Unknown"
        by_category[key] = by_category.get(key, 0) + 1

    penalties = []
    investigations = []
    deletions: dict[str, int] = {}
    for r in rows:
        if r["tag"] == "penalty":
            m = _PENALTY_RE.search(r["message"])
            penalties.append(
                {
                    "lap": r["lap"],
                    "driver": r["driver"],
                    "penalty": m.group(1) if m else None,
                    "message": r["message"],
                }
            )
        elif r["tag"] in {"investigation", "no_further_action", "noted"}:
            investigations.append(
                {
                    "lap": r["lap"],
                    "drivers": r["drivers"],
                    "status": r["tag"],
                    "message": r["message"],
                }
            )
        elif r["tag"] == "lap_deleted" and r["driver"]:
            deletions[r["driver"]] = deletions.get(r["driver"], 0) + 1

    neutralisations = neutralisation_periods(
        getattr(session_obj, "track_status", None), clock
    )

    summary = {
        "totalMessages": len(rows),
        "byCategory": by_category,
        "neutralisations": neutralisations,
        "safetyCars": sum(1 for p in neutralisations if p["type"] == "SC"),
        "virtualSafetyCars": sum(1 for p in neutralisations if p["type"] == "VSC"),
        "redFlags": sum(1 for p in neutralisations if p["type"] == "RED"),
        "penalties": penalties,
        "investigations": investigations,
        "trackLimitsDeletions": dict(
            sorted(deletions.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
    }

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------
    filtered = rows
    if category:
        cat = category.lower()
        filtered = [r for r in filtered if (r["category"] or "").lower() == cat]
    if driver:
        want = lookup.get(driver, {}).get("driverCode") or driver.upper()
        filtered = [r for r in filtered if want in r["drivers"]]
    if lap_start is not None:
        filtered = [
            r for r in filtered if r["lap"] is not None and r["lap"] >= lap_start
        ]
    if lap_end is not None:
        filtered = [r for r in filtered if r["lap"] is not None and r["lap"] <= lap_end]

    response: dict = {
        "filters": {
            "category": category,
            "driver": driver,
            "lapStart": lap_start,
            "lapEnd": lap_end,
        },
        "summary": summary,
    }
    apply_export(
        response,
        filtered,
        bulk_key="messages",
        export_path=export_path,
        tool="get_race_control_messages",
        parts=[year, event, session, category, driver],
    )
    return response


_WEATHER_COLS = {
    "AirTemp": "airTempC",
    "TrackTemp": "trackTempC",
    "Humidity": "humidityPct",
    "Pressure": "pressureMbar",
    "WindSpeed": "windSpeedMs",
    "WindDirection": "windDirectionDeg",
    "Rainfall": "rainfall",
}


def _stats(series: pd.Series) -> dict | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    return {
        "min": round(float(s.min()), 1),
        "max": round(float(s.max()), 1),
        "avg": round(float(s.mean()), 1),
        "start": round(float(s.iloc[0]), 1),
        "end": round(float(s.iloc[-1]), 1),
    }


@tool_handler
async def get_weather(
    year: int,
    event: str | int,
    session: str = "R",
    interval_minutes: int = 5,
    export_path: bool | str = False,
) -> dict:
    """
    Get weather conditions over a session: air/track temperature, rain, wind.

    Data source: FastF1 Live Timing (sampled once per minute)
    Coverage: 2018-present

    Use for "was it raining", "how hot was the track", "did conditions
    change during qualifying", or to explain tyre behaviour and strategy.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type — R, Q, S, SQ, FP1, FP2, FP3 (default "R")
        interval_minutes: Spacing of the returned samples (default 5, min 1).
                          Summary stats always use every sample.
        export_path: If True (or a directory / `.csv` path), write the
                     sampled series to CSV and omit `samples`.

    Returns:
        {
            "summary": {
                "airTempC": {"min": 21.3, "max": 24.0, "avg": 22.6,
                             "start": 21.3, "end": 23.9},
                "trackTempC": {...}, "humidityPct": {...},
                "windSpeedMs": {...},
                "rainfall": true,
                "rainPeriods": [{"startMinute": 31.0, "endMinute": 48.0,
                                 "startLap": 18, "endLap": 27}],
                "durationMinutes": 96.0
            },
            "samples": [
                {"sessionMinute": 0.0, "lap": null, "airTempC": 21.3,
                 "trackTempC": 35.1, "rainfall": false, ...},
                ...
            ]
        }

    Note:
        `sessionMinute` is minutes since the session clock started (the
        timing feed starts before lights out, so lap 1 begins a few
        minutes in). `lap` is the lap the leader was on — races only.
    """
    logger.info(f"get_weather: {year=}, {event=}, {session=}, {interval_minutes=}")
    interval_minutes = max(1, int(interval_minutes))
    session_obj = await require_session(year, event, session)

    weather = getattr(session_obj, "weather_data", None)
    if weather is None or not hasattr(weather, "columns") or weather.empty:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"No weather data for {year} {event} {session}.",
        )

    weather = weather.sort_values("Time").reset_index(drop=True)
    clock = LapClock.from_laps(getattr(session_obj, "laps", None))
    minutes = weather["Time"].map(to_seconds) / 60.0

    summary: dict = {}
    for col, key in _WEATHER_COLS.items():
        if col in weather.columns and col not in {"Rainfall", "WindDirection"}:
            summary[key] = _stats(weather[col])

    rain = (
        weather["Rainfall"].fillna(False).astype(bool)
        if "Rainfall" in weather.columns
        else pd.Series(False, index=weather.index)
    )
    rain_periods = []
    start_idx = None
    for i, is_rain in enumerate(rain.tolist() + [False]):
        if is_rain and start_idx is None:
            start_idx = i
        elif not is_rain and start_idx is not None:
            end_idx = i - 1
            rain_periods.append(
                {
                    "startMinute": round(float(minutes.iloc[start_idx]), 1),
                    "endMinute": round(float(minutes.iloc[end_idx]), 1),
                    "startLap": clock.lap_at(weather["Time"].iloc[start_idx])
                    if clock
                    else None,
                    "endLap": clock.lap_at(weather["Time"].iloc[end_idx])
                    if clock
                    else None,
                }
            )
            start_idx = None
    summary["rainfall"] = bool(rain.any())
    summary["rainPeriods"] = rain_periods
    summary["durationMinutes"] = (
        round(float(minutes.iloc[-1] - minutes.iloc[0]), 1) if len(minutes) else None
    )

    samples = []
    next_minute = None
    for i, row in weather.iterrows():
        m = minutes.iloc[i]
        if pd.isna(m):
            continue
        if next_minute is not None and m < next_minute and i != len(weather) - 1:
            continue
        next_minute = m + interval_minutes
        entry = {
            "sessionMinute": round(float(m), 1),
            "lap": clock.lap_at(row["Time"]) if clock else None,
        }
        for col, key in _WEATHER_COLS.items():
            if col in weather.columns:
                val = row[col]
                if col == "Rainfall":
                    entry[key] = bool(val) if pd.notna(val) else None
                elif pd.notna(val):
                    entry[key] = round(float(val), 1)
                else:
                    entry[key] = None
        samples.append(entry)

    response: dict = {"summary": to_jsonable(summary)}
    apply_export(
        response,
        samples,
        bulk_key="samples",
        export_path=export_path,
        tool="get_weather",
        parts=[year, event, session],
    )
    return response
