"""
Synthetic race with known ground truth, for the race-analysis and chart tests.

Lap times follow `base + deg[compound] * tyre_age - FUEL * (lap - 1)` plus
small deterministic noise, so tests can check that the tools *recover* the
degradation rates, safety-car window, pit laps and lead changes that were
built in — not just that they return something.
"""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from tests._helpers import FakeLaps
from tests.conftest import MockTelemetry

TOTAL_LAPS = 30
FUEL = 0.06
DEG = {"SOFT": 0.12, "MEDIUM": 0.06, "HARD": 0.03}
SC_LAPS = {12, 13, 14}
SC_SLOWDOWN = 25.0
SESSION_START = 300.0  # session clock (s) when lap 1 starts

# driver → (number, full name, team, grid, base pace, [(compound, first lap)])
DRIVERS = {
    "VER": (
        "1",
        "Max Verstappen",
        "Red Bull Racing",
        2,
        90.00,
        [("MEDIUM", 1), ("HARD", 16)],
    ),
    "LEC": ("16", "Charles Leclerc", "Ferrari", 1, 90.15, [("SOFT", 1), ("HARD", 10)]),
    "HAM": (
        "44",
        "Lewis Hamilton",
        "Mercedes",
        3,
        90.35,
        [("HARD", 1), ("MEDIUM", 20)],
    ),
}
PIT_LOSS = 20.0


def _noise(drv: str, lap: int) -> float:
    rng = np.random.default_rng(sum(map(ord, drv)) * 1000 + lap)
    return float(rng.normal(0, 0.05))


def build_laps() -> FakeLaps:
    rows = []
    for drv, (_num, _name, _team, grid, base, plan) in DRIVERS.items():
        t = SESSION_START + grid * 0.5  # grid order offset at the start
        pit_laps = {first - 1 for _, first in plan[1:]}
        for lap in range(1, TOTAL_LAPS + 1):
            stint = max(i for i, (_, first) in enumerate(plan) if first <= lap) + 1
            compound, first = plan[stint - 1]
            age = lap - first + 1
            sec = base + DEG[compound] * age - FUEL * (lap - 1) + _noise(drv, lap)
            if lap == 1:
                sec += 3.0
            if lap in SC_LAPS:
                sec += SC_SLOWDOWN
            in_lap = lap in pit_laps
            out_lap = (lap - 1) in pit_laps
            if in_lap:
                sec += PIT_LOSS / 2
            if out_lap:
                sec += PIT_LOSS / 2
            start = t
            t += sec
            rows.append(
                {
                    "Driver": drv,
                    "DriverNumber": DRIVERS[drv][0],
                    "LapNumber": float(lap),
                    "LapTime": pd.Timedelta(seconds=sec),
                    "LapStartTime": pd.Timedelta(seconds=start),
                    "Time": pd.Timedelta(seconds=t),
                    "Stint": float(stint),
                    "Compound": compound,
                    "TyreLife": float(age),
                    "TrackStatus": "4" if lap in SC_LAPS else "1",
                    "PitInTime": pd.Timedelta(seconds=t) if in_lap else pd.NaT,
                    "PitOutTime": pd.Timedelta(seconds=start) if out_lap else pd.NaT,
                    "IsAccurate": lap > 1 and not in_lap and not out_lap,
                    "Deleted": False,
                }
            )
    laps = FakeLaps(rows)
    # Position at the end of each lap = order of completion time.
    laps["Position"] = (
        laps.groupby("LapNumber")["Time"].rank(method="first").astype(float)
    )
    return laps


def build_results(laps: FakeLaps) -> pd.DataFrame:
    final = laps[laps["LapNumber"] == TOTAL_LAPS].sort_values("Time")
    rows = []
    for pos, (_, r) in enumerate(final.iterrows(), start=1):
        num, name, team, grid, _base, _plan = DRIVERS[r["Driver"]]
        rows.append(
            {
                "Position": float(pos),
                "Abbreviation": r["Driver"],
                "DriverNumber": num,
                "FullName": name,
                "TeamName": team,
                "GridPosition": float(grid),
                "Status": "Finished",
                "Points": [25.0, 18.0, 15.0][pos - 1],
                "Time": pd.NaT,
            }
        )
    return pd.DataFrame(rows)


def build_track_status(laps: FakeLaps) -> pd.DataFrame:
    lap_start = laps.groupby("LapNumber")["LapStartTime"].min()
    return pd.DataFrame(
        {
            "Time": [
                pd.Timedelta(0),
                lap_start[min(SC_LAPS)] + pd.Timedelta(seconds=5),
                lap_start[max(SC_LAPS)] + pd.Timedelta(seconds=60),
            ],
            "Status": ["1", "4", "1"],
            "Message": ["AllClear", "SCDeployed", "AllClear"],
        }
    )


def build_race_control() -> pd.DataFrame:
    ts = pd.Timestamp("2024-07-07 14:00:00")
    rows = [
        (0, "Flag", "GREEN LIGHT - PIT EXIT OPEN", "GREEN", "Track", None, None, 1),
        (
            5,
            "Other",
            "CAR 44 (HAM) TIME 1:31.200 DELETED - TRACK LIMITS AT TURN 4 "
            "LAP 5 14:08:00",
            None,
            None,
            None,
            "44",
            5,
        ),
        (
            7,
            "Other",
            "CAR 44 (HAM) TIME 1:31.900 DELETED - TRACK LIMITS AT TURN 4 "
            "LAP 7 14:11:00",
            None,
            None,
            None,
            "44",
            7,
        ),
        (
            11,
            "Other",
            "FIA STEWARDS: TURN 1 INCIDENT INVOLVING CARS 1 (VER) AND "
            "16 (LEC) UNDER INVESTIGATION - CAUSING A COLLISION",
            None,
            None,
            None,
            None,
            11,
        ),
        (12, "SafetyCar", "SAFETY CAR DEPLOYED", None, "Track", None, None, 12),
        (13, "Flag", "YELLOW IN TRACK SECTOR 7", "YELLOW", "Sector", 7.0, None, 13),
        (15, "SafetyCar", "SAFETY CAR IN THIS LAP", None, "Track", None, None, 14),
        (
            18,
            "Other",
            "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 1 (VER) - CAUSING A COLLISION",
            None,
            None,
            None,
            None,
            18,
        ),
        (30, "Flag", "CHEQUERED FLAG", "CHEQUERED", "Track", None, None, 30),
    ]
    return pd.DataFrame(
        [
            {
                "Time": ts + pd.Timedelta(minutes=m),
                "Category": cat,
                "Message": msg,
                "Flag": flag,
                "Scope": scope,
                "Sector": sector,
                "RacingNumber": num,
                "Lap": lap,
            }
            for m, cat, msg, flag, scope, sector, num, lap in rows
        ]
    )


def build_weather() -> pd.DataFrame:
    minutes = np.arange(0, 60)
    rain = (minutes >= 20) & (minutes < 30)
    return pd.DataFrame(
        {
            "Time": pd.to_timedelta(minutes, unit="min"),
            "AirTemp": 20.0 + minutes * 0.05,
            "TrackTemp": 35.0 - rain * 5.0,
            "Humidity": 50.0 + rain * 30.0,
            "Pressure": np.full(60, 1010.0),
            "Rainfall": rain,
            "WindDirection": np.full(60, 180),
            "WindSpeed": np.full(60, 2.5),
        }
    )


# Corners at these distances; the synthetic telemetry dips to the given
# apex speed at each, so corner analysis has a known answer.
CORNERS = [(1, 400.0, 90.0), (2, 1500.0, 150.0), (3, 2600.0, 70.0)]
LAP_LENGTH = 3300.0


def make_lap_telemetry(apex_offset_kph: float = 0.0, n: int = 1500) -> MockTelemetry:
    d = np.linspace(0, LAP_LENGTH, n)
    v = np.full(n, 300.0)
    for _num, dist, apex in CORNERS:
        dip = (300.0 - (apex - apex_offset_kph)) * np.exp(-(((d - dist) / 60.0) ** 2))
        v = np.minimum(v, 300.0 - dip)
    brake = np.zeros(n, dtype=bool)
    for _num, dist, _apex in CORNERS:
        brake |= (d > dist - 120) & (d < dist - 10)
    # Time channel consistent with speed.
    dt = np.diff(d, prepend=0) / np.maximum(v / 3.6, 1.0)
    return MockTelemetry(
        {
            "Distance": d,
            "Speed": v,
            "Throttle": np.where(brake, 0.0, 100.0),
            "Brake": brake,
            "nGear": np.where(v < 120, 3, 8),
            "DRS": np.zeros(n, dtype=int),
            "Time": pd.to_timedelta(np.cumsum(dt), unit="s"),
        }
    )


def make_corners() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "X": [0.0] * len(CORNERS),
            "Y": [0.0] * len(CORNERS),
            "Number": [c[0] for c in CORNERS],
            "Letter": [""] * len(CORNERS),
            "Angle": [0.0] * len(CORNERS),
            "Distance": [c[1] for c in CORNERS],
        }
    )


def make_race_session() -> MagicMock:
    laps = build_laps()
    session = MagicMock()
    session.laps = laps
    session.results = build_results(laps)
    session.drivers = [v[0] for v in DRIVERS.values()]
    session.track_status = build_track_status(laps)
    session.race_control_messages = build_race_control()
    session.weather_data = build_weather()
    session.event = {"EventName": "Test Grand Prix"}
    info = MagicMock()
    info.corners = make_corners()
    session.get_circuit_info = MagicMock(return_value=info)
    return session
