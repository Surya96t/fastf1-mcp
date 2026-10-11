"""Helpers for mapping session time to laps and summarising track status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

# FastF1 track status codes (see fastf1.api.track_status_data).
TRACK_STATUS_LABELS = {
    "1": "GREEN",
    "2": "YELLOW",
    "4": "SC",
    "5": "RED",
    "6": "VSC",
    "7": "VSC_ENDING",
}

# Statuses that neutralise the race — the ones strategy questions care about.
# "7" (VSC ending) is folded into the preceding VSC period.
_NEUTRALISED = {"4": "SC", "5": "RED", "6": "VSC", "7": "VSC"}


def to_seconds(value: Any) -> float | None:
    """Timedelta-like → float seconds (None for NaT/None)."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timedelta):
        return value.total_seconds()
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())
    return float(value)


@dataclass
class LapClock:
    """
    Maps session time to the race lap the leader was on.

    Built from the earliest `LapStartTime` of each lap across all drivers —
    i.e. the moment the leader started that lap.
    """

    lap_numbers: np.ndarray
    start_secs: np.ndarray
    end_sec: float | None

    @classmethod
    def from_laps(cls, laps: Any) -> LapClock | None:
        if laps is None or not hasattr(laps, "columns"):
            return None
        if "LapNumber" not in laps.columns or "LapStartTime" not in laps.columns:
            return None
        df = laps[["LapNumber", "LapStartTime"]].dropna()
        if df.empty:
            return None
        starts = df.groupby("LapNumber")["LapStartTime"].min().sort_index()
        end_sec = None
        if "Time" in laps.columns and laps["Time"].notna().any():
            end_sec = to_seconds(laps["Time"].max())
        return cls(
            lap_numbers=starts.index.to_numpy(dtype=int),
            start_secs=np.array([to_seconds(t) for t in starts], dtype=float),
            end_sec=end_sec,
        )

    def lap_at(self, session_time: Any) -> int | None:
        """Lap number in progress at `session_time` (None before lap 1)."""
        t = to_seconds(session_time)
        if t is None or len(self.start_secs) == 0:
            return None
        idx = int(np.searchsorted(self.start_secs, t, side="right"))
        if idx == 0:
            return None
        return int(self.lap_numbers[idx - 1])


def neutralisation_periods(
    track_status: Any,
    clock: LapClock | None = None,
) -> list[dict]:
    """
    Collapse FastF1 track-status changes into SC / VSC / RED periods.

    Returns:
        [{"type": "SC", "startSessionSec": 3605.2, "endSessionSec": 3900.1,
          "durationSec": 294.9, "startLap": 34, "endLap": 37}, ...]
        `endSessionSec` is None when the session ended under that status.
    """
    if track_status is None or not hasattr(track_status, "columns"):
        return []
    if "Status" not in track_status.columns or "Time" not in track_status.columns:
        return []

    periods: list[dict] = []
    current: dict | None = None
    for _, row in track_status.sort_values("Time").iterrows():
        status = str(row["Status"])
        kind = _NEUTRALISED.get(status)
        t = to_seconds(row["Time"])
        if current is not None and kind != current["type"]:
            current["endSessionSec"] = t
            periods.append(current)
            current = None
        if kind is not None and current is None:
            current = {"type": kind, "startSessionSec": t, "endSessionSec": None}
    if current is not None:
        periods.append(current)

    for p in periods:
        start, end = p["startSessionSec"], p["endSessionSec"]
        p["durationSec"] = (
            round(end - start, 1) if start is not None and end is not None else None
        )
        p["startSessionSec"] = round(start, 1) if start is not None else None
        p["endSessionSec"] = round(end, 1) if end is not None else None
        p["startLap"] = clock.lap_at(start) if clock else None
        p["endLap"] = clock.lap_at(end) if clock and end is not None else None
    return periods
