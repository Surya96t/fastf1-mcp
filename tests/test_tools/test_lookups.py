"""Unit tests for tools/lookups.py (Ergast API tools)."""

import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from fastf1_mcp.tools.lookups import get_driver_standings


def _make_standings_mock(rows: list[dict]) -> MagicMock:
    """Return a mock Ergast response whose .content[0] is a DataFrame."""
    df = pd.DataFrame(rows)
    mock = MagicMock()
    mock.content = [df]
    return mock


@pytest.mark.asyncio
async def test_get_driver_standings_2024():
    """Full standings for 2024 returns ordered list with expected keys."""
    standings_data = [
        {
            "position": 1,
            "driverCode": "VER",
            "givenName": "Max",
            "familyName": "Verstappen",
            "constructorNames": ["Red Bull"],
            "points": 437.0,
            "wins": 19,
        },
        {
            "position": 2,
            "driverCode": "LEC",
            "givenName": "Charles",
            "familyName": "Leclerc",
            "constructorNames": ["Ferrari"],
            "points": 307.0,
            "wins": 3,
        },
    ]
    mock_response = _make_standings_mock(standings_data)

    with patch("fastf1_mcp.tools.lookups.Ergast") as MockErgast:
        MockErgast.return_value.get_driver_standings.return_value = mock_response
        result = await get_driver_standings(2024)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["position"] == 1
    assert result[0]["code"] == "VER"
    assert result[0]["points"] == 437.0
    assert result[0]["wins"] == 19


@pytest.mark.asyncio
async def test_get_driver_standings_with_round():
    """after_round is forwarded to the Ergast call."""
    standings_data = [
        {
            "position": 1,
            "driverCode": "VER",
            "givenName": "Max",
            "familyName": "Verstappen",
            "constructorNames": ["Red Bull"],
            "points": 125.0,
            "wins": 5,
        },
    ]
    mock_response = _make_standings_mock(standings_data)

    with patch("fastf1_mcp.tools.lookups.Ergast") as MockErgast:
        MockErgast.return_value.get_driver_standings.return_value = mock_response
        result = await get_driver_standings(2024, after_round=5)

    assert isinstance(result, list)
    MockErgast.return_value.get_driver_standings.assert_called_once_with(
        season=2024, round=5
    )


@pytest.mark.asyncio
async def test_get_driver_standings_historical():
    """Works for seasons before 2018 (Ergast covers 1950+)."""
    standings_data = [
        {
            "position": 1,
            "driverCode": "SEN",
            "givenName": "Ayrton",
            "familyName": "Senna",
            "constructorNames": ["McLaren"],
            "points": 78.0,
            "wins": 6,
        },
    ]
    mock_response = _make_standings_mock(standings_data)

    with patch("fastf1_mcp.tools.lookups.Ergast") as MockErgast:
        MockErgast.return_value.get_driver_standings.return_value = mock_response
        result = await get_driver_standings(1990)

    assert isinstance(result, list)
    assert len(result) > 0
    assert result[0]["position"] == 1
