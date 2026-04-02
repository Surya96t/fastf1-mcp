"""Shared fixtures and helpers for the fastf1-mcp test suite."""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Telemetry helper
# ---------------------------------------------------------------------------

class MockTelemetry(pd.DataFrame):
    """DataFrame subclass with a no-op add_distance() so tests can call it."""

    @property
    def _constructor(self):
        return MockTelemetry

    def add_distance(self):
        return self


def _make_telemetry(n: int = 1000) -> MockTelemetry:
    dist = np.linspace(0, 3337.0, n)
    return MockTelemetry({
        "Distance": dist,
        "Speed": np.full(n, 200.0),
        "Throttle": np.full(n, 80.0),
        "Brake": np.zeros(n, dtype=bool),
        "nGear": np.full(n, 6, dtype=int),
        "DRS": np.zeros(n, dtype=int),
    })


# ---------------------------------------------------------------------------
# Lap & Laps helpers
# ---------------------------------------------------------------------------

def make_mock_lap(
    driver: str = "VER",
    lap_number: int = 5,
    lap_time_sec: float = 90.0,
    s1_sec: float = 28.0,
    s2_sec: float = 32.0,
    s3_sec: float = 30.0,
    telemetry_points: int = 1000,
) -> MagicMock:
    """Return a mock FastF1 Lap row."""
    data = {
        "LapNumber": lap_number,
        "LapTime": pd.Timedelta(seconds=lap_time_sec),
        "Sector1Time": pd.Timedelta(seconds=s1_sec),
        "Sector2Time": pd.Timedelta(seconds=s2_sec),
        "Sector3Time": pd.Timedelta(seconds=s3_sec),
        "Compound": "MEDIUM",
        "TyreLife": 10,
        "IsPersonalBest": True,
        "Deleted": False,
    }
    lap = MagicMock()
    lap.__getitem__ = MagicMock(side_effect=lambda key: data.get(key, pd.NaT))
    lap.get = MagicMock(side_effect=lambda key, default=None: data.get(key, default))
    lap.get_telemetry = MagicMock(return_value=_make_telemetry(telemetry_points))
    return lap


def make_mock_laps_for_driver(
    driver: str,
    n_laps: int = 15,
    lap_time_base: float = 90.0,
) -> MagicMock:
    """
    Return a mock Laps object for a single driver.

    Supports Boolean indexing (returns self) and the FastF1 filter chain:
    pick_track_status / pick_wo_box / pick_accurate.
    """
    times = [pd.Timedelta(seconds=lap_time_base + i * 0.1) for i in range(n_laps)]
    lap_series = pd.Series(times)
    lap_numbers = pd.Series(list(range(3, 3 + n_laps)))

    laps = MagicMock()
    laps.__len__ = MagicMock(return_value=n_laps)

    def _getitem(k):
        if isinstance(k, str):
            if k == "LapTime":
                return lap_series
            if k == "LapNumber":
                return lap_numbers
        # Boolean Series, slice, or other indexing — return self (no-op filter)
        return laps

    laps.__getitem__ = MagicMock(side_effect=_getitem)
    laps.pick_track_status = MagicMock(return_value=laps)
    laps.pick_wo_box = MagicMock(return_value=laps)
    laps.pick_accurate = MagicMock(return_value=laps)
    laps.pick_fastest = MagicMock(return_value=make_mock_lap(driver=driver))
    return laps


# ---------------------------------------------------------------------------
# Session fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_session() -> MagicMock:
    """Mock FastF1 session with results DataFrame, laps, and three drivers."""
    drivers = ["VER", "LEC", "SAI"]

    results_df = pd.DataFrame({
        "Position":     [1.0,   2.0,    3.0],
        "Abbreviation": ["VER", "LEC",  "SAI"],
        "DriverNumber": ["1",   "16",   "55"],
        "FullName":     ["Max Verstappen", "Charles Leclerc", "Carlos Sainz"],
        "TeamName":     ["Red Bull", "Ferrari", "Ferrari"],
        "GridPosition": [1.0, 3.0, 2.0],
        "Status":       ["Finished", "Finished", "Finished"],
        "Points":       [25.0, 18.0, 15.0],
        "Time":         [
            pd.Timedelta("1:30:00"),
            pd.Timedelta("1:30:10"),
            pd.Timedelta("1:30:20"),
        ],
    })

    per_driver = {d: make_mock_laps_for_driver(d) for d in drivers}

    all_laps = MagicMock()
    all_laps.pick_drivers = MagicMock(side_effect=lambda d: per_driver[d])
    all_laps._per_driver = per_driver  # expose for assertion in tests

    session = MagicMock()
    session.drivers = drivers
    session.results = results_df
    session.laps = all_laps
    session.load = MagicMock(return_value=None)
    return session
