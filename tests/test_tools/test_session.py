"""Unit tests for tools/session.py (FastF1 session tools)."""

import pytest
from unittest.mock import AsyncMock, patch

from fastf1_mcp.tools.session import get_session_results, get_race_pace
from fastf1_mcp.utils.errors import ErrorCode


@pytest.mark.asyncio
async def test_get_session_results_valid(mock_session):
    """Valid year returns ordered classification with expected fields."""
    with patch(
        "fastf1_mcp.tools.session.session_manager.get_session",
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
async def test_get_race_pace_excludes_sc(mock_session):
    """With exclude_sc_laps=True, pick_track_status is called for every driver."""
    with patch(
        "fastf1_mcp.tools.session.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=mock_session,
    ):
        result = await get_race_pace(2024, "Monaco", exclude_sc_laps=True)

    # Verify SC filter was applied for each driver
    for driver in mock_session.drivers:
        mock_session.laps._per_driver[driver].pick_track_status.assert_called_once_with(
            "1", how="equals"
        )

    assert isinstance(result, list)
    assert len(result) > 0
    assert "deltaToFastestSec" in result[0]
    assert result[0]["deltaToFastestSec"] == 0.0  # fastest driver has delta 0
