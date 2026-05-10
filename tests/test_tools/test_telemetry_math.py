"""
Math tests for telemetry helpers.

Exercises the primitives used by compare_telemetry / get_lap_telemetry
without relying on the FastF1 session mock — keeps regressions in the
H3/H4/H5 fixes detectable.
"""

import numpy as np
import pandas as pd

from fastf1_mcp.tools.telemetry import _cumulative_time_at_distances


def _const_speed_telemetry(speed_kmh: float, length_m: float, n: int = 101):
    """Constant-speed telemetry without a Time channel — forces integration path."""
    return pd.DataFrame(
        {
            "Distance": np.linspace(0, length_m, n),
            "Speed": np.full(n, speed_kmh),
        }
    )


def test_cumulative_time_constant_speed_matches_analytic():
    # 36 km/h == 10 m/s, so 100 m takes 10 s and 50 m takes 5 s.
    tel = _const_speed_telemetry(speed_kmh=36.0, length_m=100.0)
    out = _cumulative_time_at_distances(tel, np.array([0.0, 50.0, 100.0]))
    np.testing.assert_allclose(out, [0.0, 5.0, 10.0], atol=0.05)


def test_cumulative_time_handles_zero_speed_at_standing_start():
    # H5 regression: a single Speed=0 sample at distance 0 must NOT inject
    # millions of seconds into the cumulative integral.
    tel = pd.DataFrame(
        {
            "Distance": [0.0, 1.0, 10.0, 50.0, 100.0],
            "Speed": [0.0, 5.0, 36.0, 36.0, 36.0],
        }
    )
    out = _cumulative_time_at_distances(tel, np.array([100.0]))
    assert out[0] < 60.0, f"standing-start clamp leaked: cum_time={out[0]}"


def test_cumulative_time_handles_non_monotonic_distance():
    # H3 regression: np.interp requires monotonic xp. FastF1 telemetry can have
    # decreasing samples near pit entry — the helper must sort/dedup internally.
    tel = pd.DataFrame(
        {
            "Distance": [0.0, 10.0, 25.0, 20.0, 50.0, 100.0],
            "Speed": [36.0] * 6,
        }
    )
    out = _cumulative_time_at_distances(tel, np.array([0.0, 50.0, 100.0]))
    assert not np.isnan(out).any()
    # Cumulative time must be monotonic in distance after the helper sorts it.
    assert out[0] <= out[1] <= out[2]


def test_cumulative_time_prefers_recorded_time_channel():
    # When the Time channel is present, helper should use it directly rather
    # than re-integrating Speed (which has compounding error).
    tel = pd.DataFrame(
        {
            "Distance": np.linspace(0, 100, 11),
            "Speed": np.full(11, 999.0),  # bogus — proves helper ignored it
            "Time": pd.to_timedelta(np.linspace(0, 10, 11), unit="s"),
        }
    )
    out = _cumulative_time_at_distances(tel, np.array([0.0, 50.0, 100.0]))
    np.testing.assert_allclose(out, [0.0, 5.0, 10.0], atol=0.01)


def test_cumulative_time_normalises_time_offset():
    # Time channel may start mid-session (e.g. 1000s session time at lap start).
    # Helper subtracts t[0] so the returned series begins at 0.
    tel = pd.DataFrame(
        {
            "Distance": np.linspace(0, 100, 11),
            "Speed": np.full(11, 36.0),
            "Time": pd.to_timedelta(1000.0 + np.linspace(0, 10, 11), unit="s"),
        }
    )
    out = _cumulative_time_at_distances(tel, np.array([0.0, 100.0]))
    assert out[0] == 0.0
    np.testing.assert_allclose(out[1], 10.0, atol=0.01)


def test_cumulative_time_empty_telemetry():
    tel = pd.DataFrame({"Distance": [], "Speed": []})
    out = _cumulative_time_at_distances(tel, np.array([0.0, 50.0]))
    assert out.shape == (2,)
    assert (out == 0.0).all()
