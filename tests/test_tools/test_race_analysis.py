"""
Tests for the race-analysis tools against a synthetic race with known
ground truth (see tests/_race_fixture.py).
"""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from tests import _race_fixture as rf
from tests.conftest import make_mock_lap
from fastf1_mcp.tools.corners import get_corner_analysis
from fastf1_mcp.tools.race_analysis import get_position_changes, get_tyre_degradation
from fastf1_mcp.tools.race_events import get_race_control_messages, get_weather
from fastf1_mcp.utils.errors import ErrorCode


@contextmanager
def _session(session=None):
    session = session or rf.make_race_session()
    with (
        patch(
            "fastf1_mcp.utils.session_loader.session_manager.get_session",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "fastf1_mcp.utils.session_loader.session_manager"
            ".get_session_with_telemetry",
            new_callable=AsyncMock,
            return_value=session,
        ),
    ):
        yield session


def _strict(obj):
    return json.loads(json.dumps(obj, allow_nan=False))


# ---------------------------------------------------------------------------
# get_race_control_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_race_control_summary_recovers_incidents():
    with _session():
        result = await get_race_control_messages(2024, "Test", "R")

    s = result["summary"]
    assert s["totalMessages"] == 9
    assert s["safetyCars"] == 1 and s["virtualSafetyCars"] == 0
    sc = s["neutralisations"][0]
    assert (sc["type"], sc["startLap"], sc["endLap"]) == (
        "SC",
        min(rf.SC_LAPS),
        max(rf.SC_LAPS),
    )
    assert sc["durationSec"] > 0

    assert len(s["penalties"]) == 1
    assert s["penalties"][0]["driver"] == "VER"
    assert s["penalties"][0]["penalty"] == "5 SECOND TIME PENALTY"
    assert s["investigations"][0]["drivers"] == ["VER", "LEC"]
    assert s["trackLimitsDeletions"] == {"HAM": 2}
    _strict(result)


@pytest.mark.asyncio
async def test_race_control_filters_keep_summary_session_wide():
    with _session():
        by_driver = await get_race_control_messages(2024, "Test", "R", driver="LEC")
        by_cat = await get_race_control_messages(2024, "Test", "R", category="flag")
        by_lap = await get_race_control_messages(
            2024, "Test", "R", lap_start=12, lap_end=14
        )

    assert [m["tag"] for m in by_driver["messages"]] == ["investigation"]
    assert by_driver["summary"]["totalMessages"] == 9
    assert {m["category"] for m in by_cat["messages"]} == {"Flag"}
    assert len(by_cat["messages"]) == 3
    assert {m["lap"] for m in by_lap["messages"]} == {12, 13, 14}
    sector_msg = next(m for m in by_lap["messages"] if m["flag"] == "YELLOW")
    assert sector_msg["sector"] == 7


@pytest.mark.asyncio
async def test_race_control_driver_filter_ignores_case_and_whitespace():
    # Regression (review): " ham" matched nothing and returned no messages.
    with _session():
        result = await get_race_control_messages(2024, "Test", "R", driver=" ham ")
    assert len(result["messages"]) == 2
    assert all(m["driver"] == "HAM" for m in result["messages"])


@pytest.mark.asyncio
async def test_race_control_resolves_racing_number_to_code():
    with _session():
        result = await get_race_control_messages(2024, "Test", "R", driver="44")
    assert len(result["messages"]) == 2
    assert all(m["driver"] == "HAM" for m in result["messages"])


# ---------------------------------------------------------------------------
# get_weather
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weather_summary_and_rain_period():
    with _session():
        result = await get_weather(2024, "Test", "R", interval_minutes=5)

    s = result["summary"]
    assert s["rainfall"] is True
    assert len(s["rainPeriods"]) == 1
    rain = s["rainPeriods"][0]
    assert (rain["startMinute"], rain["endMinute"]) == (20.0, 29.0)
    assert rain["startLap"] is not None and rain["endLap"] >= rain["startLap"]
    assert s["airTempC"]["min"] == 20.0
    assert s["airTempC"]["max"] == pytest.approx(22.95, abs=0.06)
    assert s["trackTempC"]["min"] == 30.0
    assert s["durationMinutes"] == 59.0

    minutes = [x["sessionMinute"] for x in result["samples"]]
    assert minutes[:3] == [0.0, 5.0, 10.0]
    assert minutes[-1] == 59.0  # last sample always kept
    assert result["samples"][0]["lap"] is None  # before lights out
    _strict(result)


# ---------------------------------------------------------------------------
# get_position_changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_position_changes_matches_fixture():
    with _session() as session:
        result = await get_position_changes(2024, "Test")

    laps = session.laps
    leaders = (
        laps.loc[laps.groupby("LapNumber")["Time"].idxmin()]
        .sort_values("LapNumber")["Driver"]
        .tolist()
    )
    expected_changes = [
        {"lap": i + 1, "from": leaders[i - 1], "to": leaders[i]}
        for i in range(1, len(leaders))
        if leaders[i] != leaders[i - 1]
    ]

    assert result["totalLaps"] == rf.TOTAL_LAPS
    assert result["summary"]["leadChanges"] == expected_changes
    assert sum(result["summary"]["lapLeaders"].values()) == rf.TOTAL_LAPS

    by_code = {d["driver"]: d for d in result["drivers"]}
    for code, d in by_code.items():
        assert d["grid"] == rf.DRIVERS[code][3]
        assert d["positionsGained"] == d["grid"] - d["finish"]
        assert len(d["positions"]) == rf.TOTAL_LAPS
        assert d["lapsLed"] == leaders.count(code)
        # The leader has a zero gap on every lap it leads.
        for lap_idx, leader in enumerate(leaders):
            if leader == code:
                assert d["gapsToLeaderSec"][lap_idx] == 0.0
            else:
                assert d["gapsToLeaderSec"][lap_idx] > 0
    assert [d["finish"] for d in result["drivers"]] == [1, 2, 3]
    assert result["summary"]["winner"] == result["drivers"][0]["driver"]
    _strict(result)


@pytest.mark.asyncio
async def test_position_changes_driver_filter_and_compact_mode():
    with _session():
        result = await get_position_changes(
            2024, "Test", drivers="ham, ver", include_laps=False
        )
    assert {d["driver"] for d in result["drivers"]} == {"HAM", "VER"}
    assert "positions" not in result["drivers"][0]
    # Summary still covers the full field.
    assert sum(result["summary"]["lapLeaders"].values()) == rf.TOTAL_LAPS


@pytest.mark.asyncio
async def test_position_changes_unknown_driver():
    with _session():
        result = await get_position_changes(2024, "Test", drivers=["XXX"])
    assert result["code"] == ErrorCode.DRIVER_NOT_FOUND.value


@pytest.mark.asyncio
async def test_position_changes_export_writes_long_format(tmp_path):
    target = tmp_path / "positions.csv"
    with _session():
        result = await get_position_changes(2024, "Test", export_path=str(target))
    assert result["rowCount"] == rf.TOTAL_LAPS * len(rf.DRIVERS)
    assert "positions" not in result["drivers"][0]
    df = pd.read_csv(target)
    assert list(df.columns) == ["lap", "driver", "position", "gapToLeaderSec"]


# ---------------------------------------------------------------------------
# get_tyre_degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tyre_degradation_recovers_known_rates():
    with _session():
        result = await get_tyre_degradation(2024, "Test")

    compounds = result["summary"]["compounds"]
    assert set(compounds) == set(rf.DEG)
    for compound, true_deg in rf.DEG.items():
        assert compounds[compound]["medianDegSecPerLap"] == pytest.approx(
            true_deg, abs=0.03
        )

    # Every fitted stint excludes lap 1, SC laps and in/out laps.
    for s in result["stints"]:
        assert s["degSecPerLap"] == pytest.approx(rf.DEG[s["compound"]], abs=0.04)
        # Fuel burn hides degradation in the raw slope.
        assert s["rawSlopeSecPerLap"] == pytest.approx(
            s["degSecPerLap"] - rf.FUEL, abs=0.01
        )
    assert len(result["summary"]["compoundCrossovers"]) == 3
    _strict(result)


@pytest.mark.asyncio
async def test_tyre_degradation_driver_filter_and_fuel_toggle():
    with _session():
        ver = await get_tyre_degradation(2024, "Test", driver="VER")
        raw = await get_tyre_degradation(
            2024, "Test", driver="VER", fuel_correction_sec_per_lap=0
        )
    assert {s["driver"] for s in ver["stints"]} == {"VER"}
    assert [s["compound"] for s in ver["stints"]] == ["MEDIUM", "HARD"]
    for a, b in zip(ver["stints"], raw["stints"]):
        assert b["degSecPerLap"] == b["rawSlopeSecPerLap"]
        assert a["degSecPerLap"] > b["degSecPerLap"]


@pytest.mark.asyncio
async def test_tyre_degradation_too_few_laps():
    with _session():
        result = await get_tyre_degradation(2024, "Test", min_stint_laps=40)
    assert result["code"] == ErrorCode.DATA_UNAVAILABLE.value


# ---------------------------------------------------------------------------
# get_corner_analysis
# ---------------------------------------------------------------------------


def _patch_laps(offsets: dict[str, float]):
    def _lap(_session, driver, _lap):
        lap = make_mock_lap(driver=driver)
        lap.get_telemetry.return_value = rf.make_lap_telemetry(offsets[driver])
        return lap

    return patch("fastf1_mcp.tools.corners._get_driver_lap", side_effect=_lap)


@pytest.mark.asyncio
async def test_corner_analysis_single_driver():
    with _session(), _patch_laps({"VER": 0.0}):
        result = await get_corner_analysis(2024, "Test", "Q", "VER")

    corners = {c["corner"]: c for c in result["corners"]}
    assert list(corners) == ["T1", "T2", "T3"]
    for num, dist, apex in rf.CORNERS:
        c = corners[f"T{num}"]["driver"]
        assert c["minSpeedKph"] == pytest.approx(apex, abs=1.0)
        assert c["apexDistanceM"] == pytest.approx(dist, abs=5)
        assert c["entrySpeedKph"] == pytest.approx(300, abs=1)
        assert c["brakeStartM"] == pytest.approx(dist - 120, abs=5)
    assert result["summary"]["slowestCorner"]["corner"] == "T3"
    assert result["summary"]["fastestCorner"]["corner"] == "T2"
    assert result["driver2"] is None
    _strict(result)


@pytest.mark.asyncio
async def test_corner_analysis_two_drivers_attributes_time():
    # LEC carries 8 km/h less through every apex → slower in every corner.
    with _session(), _patch_laps({"VER": 0.0, "LEC": 8.0}):
        result = await get_corner_analysis(2024, "Test", "Q", "VER", driver2="LEC")

    for c in result["corners"]:
        assert c["minSpeedDeltaKph"] == pytest.approx(8.0, abs=1.0)
        assert c["timeDeltaSec"] < 0
    s = result["summary"]
    assert len(s["driverGainsMost"]) == 3
    assert s["driverLosesMost"] == []
    assert s["totalCornerDeltaSec"] < 0
    # Slowest corner loses the most relative time.
    assert s["driverGainsMost"][0]["corner"] == "T3"


@pytest.mark.asyncio
async def test_corner_analysis_without_circuit_info():
    session = rf.make_race_session()
    session.get_circuit_info.side_effect = ConnectionError("multiviewer down")
    with _session(session), _patch_laps({"VER": 0.0}):
        result = await get_corner_analysis(2024, "Test", "Q", "VER")
    assert result["code"] == ErrorCode.DATA_UNAVAILABLE.value
