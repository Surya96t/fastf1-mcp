"""
Regression tests for JSON safety and structured errors on the Ergast tools.

FastMCP rejects numpy ints, raises on NaT and emits non-standard `NaN`
tokens, so every Ergast-backed tool must normalise its output. Ergast
failures (network, bad season) must come back as the standard error dict,
not as a raw exception.
"""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fastf1_mcp.resources import drivers_resource
from fastf1_mcp.tools.lookups import (
    get_circuit_info,
    get_constructor_standings,
    get_driver_info,
    get_driver_standings,
    get_race_results_historical,
    get_schedule,
)
from fastf1_mcp.tools.utility import list_drivers, list_events
from fastf1_mcp.utils.converters import to_jsonable
from fastf1_mcp.utils.errors import ErrorCode

DATA_UNAVAILABLE = ErrorCode.DATA_UNAVAILABLE.value


def _multi(df: pd.DataFrame) -> MagicMock:
    resp = MagicMock()
    resp.content = [df]
    return resp


def _strict_dumps(obj) -> str:
    """json.dumps that fails on NaN/inf, like a strict JSON client would."""
    return json.dumps(obj, allow_nan=False)


# ---------------------------------------------------------------------------
# to_jsonable
# ---------------------------------------------------------------------------


def test_to_jsonable_normalises_pandas_and_numpy_values():
    out = to_jsonable(
        {
            "i": np.int64(3),
            "f": np.float64(1.5),
            "nan": float("nan"),
            "nat": pd.NaT,
            "na": pd.NA,
            "ts": pd.Timestamp("1960-03-21"),
            "td": pd.Timedelta(seconds=90.5),
            "nested": [np.int32(1), {"x": np.nan}],
        }
    )
    assert out == {
        "i": 3,
        "f": 1.5,
        "nan": None,
        "nat": None,
        "na": None,
        "ts": "1960-03-21T00:00:00",
        "td": "0 days 00:01:30.500000",
        "nested": [1, {"x": None}],
    }
    assert type(out["i"]) is int
    _strict_dumps(out)


# ---------------------------------------------------------------------------
# Ergast failures → structured error dict
# ---------------------------------------------------------------------------


ERGAST_CALLS = [
    pytest.param("get_race_schedule", lambda: get_schedule(2024), id="get_schedule"),
    pytest.param(
        "get_driver_standings",
        lambda: get_driver_standings(2024),
        id="get_driver_standings",
    ),
    pytest.param(
        "get_constructor_standings",
        lambda: get_constructor_standings(2024),
        id="get_constructor_standings",
    ),
    pytest.param("get_driver_info", lambda: get_driver_info(), id="get_driver_info"),
    pytest.param(
        "get_race_results",
        lambda: get_race_results_historical(1988, 1),
        id="get_race_results_historical",
    ),
    pytest.param("get_circuits", lambda: get_circuit_info(), id="get_circuit_info"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method, call", ERGAST_CALLS)
async def test_lookup_tools_return_error_dict_on_ergast_failure(method, call):
    with patch("fastf1_mcp.tools.lookups.Ergast") as MockErgast:
        getattr(MockErgast.return_value, method).side_effect = ConnectionError(
            "api.jolpi.ca unreachable"
        )
        result = await call()

    assert isinstance(result, dict)
    assert result["error"] is True
    assert result["code"] == DATA_UNAVAILABLE
    assert "unreachable" in result["message"]


@pytest.mark.asyncio
async def test_list_events_returns_error_dict_on_ergast_failure():
    with patch("fastf1_mcp.tools.utility.Ergast") as MockErgast:
        MockErgast.return_value.get_race_schedule.side_effect = ConnectionError("x")
        result = await list_events(2024)
    assert result["code"] == DATA_UNAVAILABLE


@pytest.mark.asyncio
async def test_list_drivers_season_returns_error_dict_when_driver_info_fails():
    with patch("fastf1_mcp.tools.utility.Ergast") as MockErgast:
        MockErgast.return_value.get_driver_info.side_effect = ConnectionError("x")
        MockErgast.return_value.get_driver_standings.side_effect = ConnectionError("x")
        result = await list_drivers(2024)
    assert result["code"] == DATA_UNAVAILABLE


# ---------------------------------------------------------------------------
# NaN / NaT / numpy values in Ergast data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_race_results_historical_dnf_time_is_null_not_nat():
    df = pd.DataFrame(
        {
            "position": [1, 2],
            "driverCode": ["SEN", np.nan],
            "givenName": ["Ayrton", "Nelson"],
            "familyName": ["Senna", "Piquet"],
            "constructorName": ["McLaren", "Lotus"],
            "grid": [1, 5],
            "laps": [60, 12],
            "status": ["Finished", "Engine"],
            "points": [9.0, 0.0],
            "finishingTime": [pd.Timedelta("1:30:00"), pd.NaT],
        }
    )
    with patch("fastf1_mcp.tools.lookups.Ergast") as MockErgast:
        MockErgast.return_value.get_race_results.return_value = _multi(df)
        result = await get_race_results_historical(1988, 1)

    assert result[0]["time"] == "0 days 01:30:00"
    assert result[1]["time"] is None
    assert result[1]["driverCode"] is None
    assert type(result[0]["position"]) is int
    _strict_dumps(result)


@pytest.mark.asyncio
async def test_get_driver_info_is_strict_json():
    df = pd.DataFrame(
        {
            "driverId": ["fangio"],
            "driverCode": [np.nan],
            "permanentNumber": [np.nan],
            "givenName": ["Juan"],
            "familyName": ["Fangio"],
            "dateOfBirth": [pd.Timestamp("1911-06-24")],
            "driverNationality": ["Argentine"],
        }
    )
    with patch("fastf1_mcp.tools.lookups.Ergast") as MockErgast:
        MockErgast.return_value.get_driver_info.return_value = _multi(df)
        result = await get_driver_info(year=1955)

    assert result[0]["driverCode"] is None
    assert result[0]["permanentNumber"] is None
    assert result[0]["dateOfBirth"] == "1911-06-24T00:00:00"
    _strict_dumps(result)


@pytest.mark.asyncio
async def test_get_circuit_info_filters_and_is_strict_json():
    df = pd.DataFrame(
        {
            "circuitId": ["monaco", "monza"],
            "circuitName": ["Circuit de Monaco", "Monza"],
            "lat": [np.float64(43.73), np.nan],
        }
    )
    with patch("fastf1_mcp.tools.lookups.Ergast") as MockErgast:
        MockErgast.return_value.get_circuits.return_value = df
        result = await get_circuit_info(circuit_id="monaco")

    assert result == [
        {"circuitId": "monaco", "circuitName": "Circuit de Monaco", "lat": 43.73}
    ]


@pytest.mark.asyncio
async def test_get_schedule_dates_are_iso_days():
    df = pd.DataFrame(
        {
            "round": [np.int64(1)],
            "raceName": ["Bahrain Grand Prix"],
            "circuitName": ["Bahrain International Circuit"],
            "country": ["Bahrain"],
            "raceDate": [pd.Timestamp("2024-03-02")],
        }
    )
    with patch("fastf1_mcp.tools.lookups.Ergast") as MockErgast:
        MockErgast.return_value.get_race_schedule.return_value = df
        result = await get_schedule(2024)

    assert result[0]["round"] == 1
    assert result[0]["date"] == "2024-03-02"


@pytest.mark.asyncio
async def test_get_driver_standings_without_code_is_strict_json():
    df = pd.DataFrame(
        {
            "position": [1],
            "driverCode": [np.nan],
            "givenName": ["Juan"],
            "familyName": ["Fangio"],
            "constructorNames": [["Mercedes"]],
            "points": [40.0],
            "wins": [4],
        }
    )
    with patch("fastf1_mcp.tools.lookups.Ergast") as MockErgast:
        MockErgast.return_value.get_driver_standings.return_value = _multi(df)
        result = await get_driver_standings(1955)

    assert result[0]["code"] is None
    assert result[0]["team"] == "Mercedes"
    _strict_dumps(result)


@pytest.mark.asyncio
async def test_list_drivers_season_handles_missing_code_and_number():
    info = pd.DataFrame(
        {
            "driverCode": [np.nan, "HAM"],
            "givenName": ["Juan", "Lewis"],
            "familyName": ["Fangio", "Hamilton"],
            "driverNationality": ["Argentine", "British"],
            "permanentNumber": [np.nan, "44"],
        }
    )
    standings = pd.DataFrame(
        {"driverCode": ["HAM"], "constructorNames": [["Mercedes"]]}
    )
    with patch("fastf1_mcp.tools.utility.Ergast") as MockErgast:
        MockErgast.return_value.get_driver_info.return_value = _multi(info)
        MockErgast.return_value.get_driver_standings.return_value = _multi(standings)
        result = await list_drivers(2024)

    assert result[0] == {
        "code": "",
        "fullName": "Juan Fangio",
        "nationality": "Argentine",
        "team": "",
        "number": "",
    }
    assert result[1]["team"] == "Mercedes"
    assert result[1]["number"] == "44"
    _strict_dumps(result)


@pytest.mark.asyncio
async def test_drivers_resource_returns_strict_json():
    df = pd.DataFrame(
        {"driverId": ["fangio"], "permanentNumber": [np.nan], "x": [np.int64(1)]}
    )
    with patch("fastf1_mcp.resources.Ergast") as MockErgast:
        MockErgast.return_value.get_driver_info.return_value = _multi(df)
        text = await drivers_resource(1955)

    assert json.loads(text) == [{"driverId": "fangio", "permanentNumber": None, "x": 1}]
