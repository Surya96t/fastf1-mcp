"""
Behaviour tests for the session/telemetry tools that lacked coverage:
get_lap_times, get_pit_stops, get_qualifying_breakdown, get_speed_trap_data,
get_sector_times, get_stint_analysis.

These exercise filter logic, sort order, and shape of the returned dicts
using a DataFrame-backed FakeLaps mock.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from fastf1_mcp.tools.session import (
    get_lap_times,
    get_pit_stops,
    get_qualifying_breakdown,
    get_stint_analysis,
)
from fastf1_mcp.tools.telemetry import get_sector_times, get_speed_trap_data
from tests._helpers import FakeLaps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _td(seconds):
    return pd.Timedelta(seconds=seconds)


def _patch_session(mock_session):
    return patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=mock_session,
    )


def _make_session(laps_df: FakeLaps, drivers: list[str], results_df=None) -> MagicMock:
    sess = MagicMock()
    sess.drivers = drivers
    sess.laps = laps_df
    sess.results = results_df if results_df is not None else pd.DataFrame()
    return sess


# ---------------------------------------------------------------------------
# get_lap_times
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_lap_times_excludes_deleted_by_default():
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 4,
            "LapNumber": [1, 2, 3, 4],
            "LapTime": [_td(90.0), _td(89.5), _td(89.7), _td(89.9)],
            "Sector1Time": [_td(28)] * 4,
            "Sector2Time": [_td(32)] * 4,
            "Sector3Time": [_td(30)] * 4,
            "Compound": ["MEDIUM"] * 4,
            "TyreLife": [1, 2, 3, 4],
            "IsPersonalBest": [False, True, False, False],
            "Deleted": [False, False, True, False],
        }
    )
    session = _make_session(laps, drivers=["VER"])

    with _patch_session(session):
        result = await get_lap_times(2024, "Monaco", "R", "VER")

    # New shape: {"driver", "fullName", "teamName", "summary", "laps"}
    assert isinstance(result, dict)
    assert "summary" in result
    assert result["summary"]["totalLaps"] == 3, "Deleted lap should be filtered out"
    # fastestLap reflects the 89.5 lap (lapNumber 2)
    assert result["summary"]["fastestLap"]["lapNumber"] == 2
    assert all(lap["lapNumber"] != 3 for lap in result["laps"])


@pytest.mark.asyncio
async def test_get_lap_times_includes_deleted_when_requested():
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 2,
            "LapNumber": [1, 2],
            "LapTime": [_td(90.0), _td(89.5)],
            "Deleted": [False, True],
        }
    )
    session = _make_session(laps, drivers=["VER"])

    with _patch_session(session):
        result = await get_lap_times(2024, "Monaco", "R", "VER", include_deleted=True)

    assert isinstance(result, dict)
    assert len(result["laps"]) == 2


@pytest.mark.asyncio
async def test_get_lap_times_handles_missing_deleted_column():
    # M9 regression: some FastF1 session types omit the Deleted column.
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 2,
            "LapNumber": [1, 2],
            "LapTime": [_td(90.0), _td(89.5)],
        }
    )
    session = _make_session(laps, drivers=["VER"])

    with _patch_session(session):
        result = await get_lap_times(2024, "Monaco", "R", "VER")

    assert isinstance(result, dict)
    assert len(result["laps"]) == 2


@pytest.mark.asyncio
async def test_get_lap_times_unknown_driver():
    laps = FakeLaps({"Driver": ["VER"], "LapNumber": [1], "LapTime": [_td(90)]})
    session = _make_session(laps, drivers=["VER"])

    with _patch_session(session):
        result = await get_lap_times(2024, "Monaco", "R", "ZZZ")

    assert isinstance(result, dict)
    assert result["error"] is True
    assert result["code"] == "driver_not_found"


# ---------------------------------------------------------------------------
# get_pit_stops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pit_stops_assigns_per_driver_stop_numbers():
    # VER stops on lap 15 and 35; LEC stops on lap 20.
    laps = FakeLaps(
        {
            "Driver": ["VER", "VER", "VER", "VER", "LEC", "LEC", "LEC"],
            "LapNumber": [15, 16, 35, 36, 20, 21, 30],
            "PitInTime": [
                _td(900),
                pd.NaT,
                _td(2100),
                pd.NaT,
                _td(1200),
                pd.NaT,
                pd.NaT,
            ],
            "PitOutTime": [
                pd.NaT,
                _td(925),
                pd.NaT,
                _td(2125),
                pd.NaT,
                _td(1226),
                pd.NaT,
            ],
            "Compound": ["MEDIUM", "HARD", "HARD", "SOFT", "MEDIUM", "HARD", "HARD"],
        }
    )
    session = _make_session(laps, drivers=["VER", "LEC"])

    with _patch_session(session):
        result = await get_pit_stops(2024, "Monaco")

    assert isinstance(result, list)
    assert len(result) == 3

    ver_stops = sorted(
        (s for s in result if s["driver"] == "VER"), key=lambda s: s["lap"]
    )
    assert [s["stopNumber"] for s in ver_stops] == [1, 2]
    assert [s["lap"] for s in ver_stops] == [15, 35]
    # tyreFrom comes from the in-lap, tyreTo from the next lap
    assert ver_stops[0]["tyreFrom"] == "MEDIUM"
    assert ver_stops[0]["tyreTo"] == "HARD"
    # Duration: PitOutTime - PitInTime = 925 - 900 = 25.0
    assert ver_stops[0]["duration"] == 25.0

    lec_stops = [s for s in result if s["driver"] == "LEC"]
    assert lec_stops[0]["stopNumber"] == 1


@pytest.mark.asyncio
async def test_get_pit_stops_globally_sorted_by_lap():
    laps = FakeLaps(
        {
            "Driver": ["VER", "VER", "LEC", "LEC", "SAI", "SAI"],
            "LapNumber": [30, 31, 10, 11, 20, 21],
            "PitInTime": [_td(1800), pd.NaT, _td(600), pd.NaT, _td(1200), pd.NaT],
            "PitOutTime": [pd.NaT, _td(1825), pd.NaT, _td(625), pd.NaT, _td(1225)],
            "Compound": ["MEDIUM", "HARD", "MEDIUM", "HARD", "MEDIUM", "HARD"],
        }
    )
    session = _make_session(laps, drivers=["VER", "LEC", "SAI"])

    with _patch_session(session):
        result = await get_pit_stops(2024, "Monaco")

    laps_in_order = [s["lap"] for s in result]
    assert laps_in_order == sorted(laps_in_order)


# ---------------------------------------------------------------------------
# get_qualifying_breakdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_qualifying_breakdown_returns_q1_q2_q3():
    laps = FakeLaps(
        {
            "Driver": ["VER", "LEC", "SAI", "VER", "LEC", "VER"],
            "LapNumber": [3, 3, 3, 8, 8, 14],
            "LapTime": [
                _td(72.0),
                _td(72.5),
                _td(73.0),
                _td(71.5),
                _td(71.8),
                _td(71.0),
            ],
            "QSegment": [1, 1, 1, 2, 2, 3],
        }
    )
    session = _make_session(laps, drivers=["VER", "LEC", "SAI"])

    with _patch_session(session):
        result = await get_qualifying_breakdown(2024, "Monaco")

    assert set(result.keys()) == {"Q1", "Q2", "Q3", "eliminated_Q1", "eliminated_Q2"}

    # SAI did not advance to Q2; LEC did not advance to Q3
    assert "SAI" in result["eliminated_Q1"]
    assert "LEC" in result["eliminated_Q2"]

    # Q1 sorted ascending by lap time → VER fastest
    assert result["Q1"][0]["driver"] == "VER"


@pytest.mark.asyncio
async def test_get_qualifying_breakdown_sort_uses_raw_timedelta():
    # H7/M8 regression: sorting by stringified Timedelta could produce wrong
    # order across hour boundaries. Use times that differ in a way string
    # comparison would still get right, then assert the type-stable contract.
    laps = FakeLaps(
        {
            "Driver": ["A", "B", "C"],
            "LapNumber": [1, 1, 1],
            "LapTime": [_td(72.5), _td(72.1), _td(72.9)],
            "QSegment": [1, 1, 1],
        }
    )
    session = _make_session(laps, drivers=["A", "B", "C"])

    with _patch_session(session):
        result = await get_qualifying_breakdown(2024, "Monaco")

    assert [e["driver"] for e in result["Q1"]] == ["B", "A", "C"]
    # _bestRaw is an internal sort key — should be stripped from output
    assert all("_bestRaw" not in e for e in result["Q1"])


# ---------------------------------------------------------------------------
# get_speed_trap_data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_speed_trap_data_sorted_descending():
    results_df = pd.DataFrame(
        {
            "Abbreviation": ["VER", "LEC", "SAI"],
            "SpeedST": [298.5, 301.2, 295.0],
            "SpeedFL": [185.0, 187.5, 184.0],
            "SpeedI1": [None, 250.0, 248.0],
            "SpeedI2": [260.0, 262.0, 259.0],
        }
    )
    session = _make_session(FakeLaps({}), drivers=[], results_df=results_df)

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        result = await get_speed_trap_data(2024, "Monaco", "Q")

    # New shape: {"source": "results" | "laps", "drivers": [...]}
    assert isinstance(result, dict)
    assert result["source"] == "results"
    drivers = result["drivers"]
    assert [r["driver"] for r in drivers] == ["LEC", "VER", "SAI"]
    assert drivers[0]["speedI1"] == 250.0


@pytest.mark.asyncio
async def test_get_speed_trap_data_falls_back_to_laps():
    # All SpeedST/FL/I1/I2 are null on session.results → use per-lap max.
    results_df = pd.DataFrame(
        {
            "Abbreviation": ["VER", "LEC"],
            "FullName": ["Max Verstappen", "Charles Leclerc"],
            "TeamName": ["Red Bull", "Ferrari"],
            "SpeedST": [None, None],
            "SpeedFL": [None, None],
            "SpeedI1": [None, None],
            "SpeedI2": [None, None],
        }
    )
    laps = FakeLaps(
        {
            "Driver": ["VER", "VER", "LEC", "LEC"],
            "LapNumber": [1, 2, 1, 2],
            "SpeedST": [301.0, 305.5, 298.0, 302.0],
            "SpeedFL": [180.0, 182.0, 178.0, 181.0],
            "SpeedI1": [240.0, 242.0, 238.0, 241.0],
            "SpeedI2": [260.0, 262.0, 258.0, 261.0],
        }
    )
    session = _make_session(laps, drivers=["VER", "LEC"], results_df=results_df)

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        result = await get_speed_trap_data(2024, "Monaco", "Q")

    assert result["source"] == "laps"
    # max per driver across both laps, sorted descending by speedTrap
    assert [r["driver"] for r in result["drivers"]] == ["VER", "LEC"]
    assert result["drivers"][0]["speedTrap"] == 305.5
    assert result["drivers"][1]["speedTrap"] == 302.0
    # Driver enrichment came from results_df
    assert result["drivers"][0]["fullName"] == "Max Verstappen"
    assert result["drivers"][0]["teamName"] == "Red Bull"


# ---------------------------------------------------------------------------
# get_sector_times
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sector_times_includes_per_lap_array_when_requested():
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 3,
            "LapNumber": [1, 2, 3],
            "Sector1Time": [_td(28.0), _td(28.5), _td(28.2)],
            "Sector2Time": [_td(32.5), _td(32.0), _td(32.3)],
            "Sector3Time": [_td(30.3), _td(30.5), _td(30.0)],
            "LapTime": [_td(90.8), _td(91.0), _td(90.5)],
            "IsAccurate": [True, True, True],
        }
    )
    session = _make_session(laps, drivers=["VER"])

    with _patch_session(session):
        result = await get_sector_times(2024, "Monaco", "Q", include_laps=True)

    assert len(result) == 1
    row = result[0]
    assert "laps" in row
    assert len(row["laps"]) == 3
    # First lap by lapNumber, with all three sectors recorded
    assert row["laps"][0]["lapNumber"] == 1
    assert row["laps"][0]["s1"] == str(_td(28.0))


@pytest.mark.asyncio
async def test_get_sector_times_theoretical_best():
    # VER's best per sector come from different laps → theoretical < actual.
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 3,
            "LapNumber": [1, 2, 3],
            "Sector1Time": [_td(28.0), _td(28.5), _td(28.2)],
            "Sector2Time": [_td(32.5), _td(32.0), _td(32.3)],
            "Sector3Time": [_td(30.3), _td(30.5), _td(30.0)],
            "LapTime": [_td(90.8), _td(91.0), _td(90.5)],
            "IsAccurate": [True, True, True],
        }
    )
    session = _make_session(laps, drivers=["VER"])

    with _patch_session(session):
        result = await get_sector_times(2024, "Monaco", "Q")

    assert len(result) == 1
    row = result[0]
    assert row["driver"] == "VER"
    assert row["bestS1"] == str(_td(28.0))
    assert row["bestS2"] == str(_td(32.0))
    assert row["bestS3"] == str(_td(30.0))
    # theoretical = 28 + 32 + 30 = 90.0; actual best = 90.5 → gap = -0.5
    assert row["gapSec"] == -0.5
    # internal sort key must not leak into output
    assert "_theoreticalRaw" not in row


# ---------------------------------------------------------------------------
# get_stint_analysis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stint_analysis_groups_by_stint():
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 6,
            "LapNumber": [1, 2, 3, 4, 5, 6],
            "Stint": [1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
            "LapTime": [_td(91), _td(90), _td(90.5), _td(89), _td(88.5), _td(89.2)],
            "Compound": ["MEDIUM"] * 3 + ["HARD"] * 3,
            "IsAccurate": [True] * 6,
        }
    )
    session = _make_session(laps, drivers=["VER"])

    with _patch_session(session):
        result = await get_stint_analysis(2024, "Monaco", "VER")

    # New shape: {"summary": {...}, "stints": [...]}
    assert isinstance(result, dict)
    stints = result["stints"]
    assert len(stints) == 2
    s1, s2 = stints
    assert s1["compound"] == "MEDIUM"
    assert s1["startLap"] == 1
    assert s1["endLap"] == 3
    assert s1["lapCount"] == 3
    assert s2["compound"] == "HARD"
    assert s2["startLap"] == 4

    # Summary contains the per-driver strategy (1-stop MEDIUM→HARD)
    strategies = result["summary"]["strategies"]
    assert len(strategies) == 1
    assert strategies[0]["driver"] == "VER"
    assert strategies[0]["compoundSequence"] == ["MEDIUM", "HARD"]
    assert strategies[0]["pitStops"] == 1
    assert result["summary"]["totalStints"] == 2


@pytest.mark.asyncio
async def test_get_lap_times_export_writes_csv_and_omits_inline_array(tmp_path):
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 3,
            "LapNumber": [1, 2, 3],
            "LapTime": [_td(90.0), _td(89.5), _td(89.7)],
            "Compound": ["MEDIUM"] * 3,
        }
    )
    session = _make_session(laps, drivers=["VER"])

    target = tmp_path / "ver_laps.csv"
    with _patch_session(session):
        result = await get_lap_times(
            2024, "Monaco", "R", "VER", export_path=str(target)
        )

    assert "laps" not in result, "Inline array should be omitted when exporting"
    assert result["exportPath"] == str(target.resolve())
    assert result["rowCount"] == 3
    assert target.exists()
    # CSV has a header + one line per lap
    lines = target.read_text().strip().splitlines()
    assert len(lines) == 4
    assert "lapNumber" in lines[0]


@pytest.mark.asyncio
async def test_get_stint_analysis_export_writes_csv(tmp_path):
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 6,
            "LapNumber": [1, 2, 3, 4, 5, 6],
            "Stint": [1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
            "LapTime": [_td(91), _td(90), _td(90.5), _td(89), _td(88.5), _td(89.2)],
            "Compound": ["MEDIUM"] * 3 + ["HARD"] * 3,
            "IsAccurate": [True] * 6,
        }
    )
    session = _make_session(laps, drivers=["VER"])

    target = tmp_path / "ver_stints.csv"
    with _patch_session(session):
        result = await get_stint_analysis(
            2024, "Monaco", "VER", export_path=str(target)
        )

    assert "stints" not in result
    assert result["exportPath"] == str(target.resolve())
    assert result["rowCount"] == 2
    assert target.exists()
    # Summary still present even when exporting
    assert "summary" in result
    assert result["summary"]["totalStints"] == 2


@pytest.mark.asyncio
async def test_get_stint_analysis_unknown_driver():
    laps = FakeLaps(
        {
            "Driver": ["VER"],
            "LapNumber": [1],
            "Stint": [1.0],
            "LapTime": [_td(90)],
            "Compound": ["MEDIUM"],
        }
    )
    session = _make_session(laps, drivers=["VER"])

    with _patch_session(session):
        result = await get_stint_analysis(2024, "Monaco", "ZZZ")

    assert isinstance(result, dict)
    assert result["code"] == "driver_not_found"
