"""Unit tests for tools/session.py (FastF1 session tools)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastf1_mcp.tools.session import get_session_results, get_race_pace
from fastf1_mcp.utils.errors import ErrorCode


@pytest.mark.asyncio
async def test_get_session_results_valid(mock_session):
    """Valid year returns ordered classification with expected fields."""
    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=mock_session,
    ):
        result = await get_session_results(2024, "Monaco", "R")

    assert isinstance(result, list)
    assert len(result) == 3
    assert result[0]["position"] == 1
    assert result[0]["driverCode"] == "VER"
    assert result[0]["teamName"] == "Red Bull"
    assert "points" in result[0]


@pytest.mark.asyncio
async def test_get_session_results_pre_2018_raises():
    """year < 2018 returns an error dict (never raises)."""
    result = await get_session_results(2017, "Monaco", "R")

    assert isinstance(result, dict)
    assert result["error"] is True
    assert result["code"] == ErrorCode.YEAR_OUT_OF_RANGE.value


@pytest.mark.asyncio
async def test_get_session_results_gap_for_lapped_finisher():
    """`gap` falls back to Status text when FastF1 leaves Time NaT.

    Q36 regression: lapped finishers had no gap data because the converter
    only surfaced the raw `Time` column, which FastF1 doesn't populate for
    drivers more than a lap behind the leader.
    """
    import pandas as pd

    results_df = pd.DataFrame(
        {
            "Position": [1.0, 2.0, 11.0, 20.0],
            "Abbreviation": ["VER", "LEC", "TSU", "BOT"],
            "DriverNumber": ["1", "16", "22", "77"],
            "FullName": [
                "Max Verstappen",
                "Charles Leclerc",
                "Yuki Tsunoda",
                "Valtteri Bottas",
            ],
            "TeamName": ["Red Bull", "Ferrari", "AlphaTauri", "Alfa Romeo"],
            "GridPosition": [1.0, 2.0, 14.0, 18.0],
            "Status": ["Finished", "Finished", "+1 Lap", "DNF"],
            "Points": [25.0, 18.0, 0.0, 0.0],
            "Time": [
                pd.Timedelta("1:30:00"),
                pd.Timedelta(seconds=7.152),
                pd.NaT,
                pd.NaT,
            ],
        }
    )
    session = MagicMock()
    session.results = results_df

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        result = await get_session_results(2024, "Monaco", "R")

    by_code = {r["driverCode"]: r for r in result}
    assert by_code["VER"]["gap"] == "leader"
    assert by_code["LEC"]["gap"].startswith("+")
    assert by_code["TSU"]["gap"] == "+1 Lap", "Lapped finisher gap from Status"
    assert by_code["BOT"]["gap"] == "DNF", "DNF surfaces in gap field"


@pytest.mark.asyncio
async def test_get_race_pace_excludes_sc(mock_session):
    """With exclude_sc_laps=True, pick_track_status is called for every driver."""
    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=mock_session,
    ):
        result = await get_race_pace(2024, "Monaco", exclude_sc_laps=True)

    # Verify SC filter was applied for each driver
    for driver in mock_session.drivers:
        mock_session.laps._per_driver[driver].pick_track_status.assert_called_once_with(
            "1", how="equals"
        )

    # New shape: {"filters": {...}, "drivers": [...]}
    assert isinstance(result, dict)
    assert result["filters"]["excludeSafetyCarLaps"] is True
    drivers = result["drivers"]
    assert isinstance(drivers, list)
    assert len(drivers) > 0
    assert "deltaToFastestSec" in drivers[0]
    assert drivers[0]["deltaToFastestSec"] == 0.0  # fastest driver has delta 0
    # Driver enrichment fields are present
    assert "fullName" in drivers[0]
    assert "teamName" in drivers[0]
