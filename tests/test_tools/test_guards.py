"""
Year-guard tests — every tool that depends on FastF1 session data
must reject year < 2018 with the YEAR_OUT_OF_RANGE error code.
"""

import pytest

from fastf1_mcp.tools.session import (
    get_fastest_laps,
    get_lap_times,
    get_pit_stops,
    get_qualifying_breakdown,
    get_race_pace,
    get_session_results,
    get_stint_analysis,
)
from fastf1_mcp.tools.telemetry import (
    compare_telemetry,
    get_lap_telemetry,
    get_sector_times,
    get_speed_trap_data,
)
from fastf1_mcp.tools.championship import get_championship_projection
from fastf1_mcp.tools.corners import get_corner_analysis
from fastf1_mcp.tools.track import get_drs_zones
from fastf1_mcp.tools.race_analysis import get_position_changes, get_tyre_degradation
from fastf1_mcp.tools.race_events import get_race_control_messages, get_weather
from fastf1_mcp.utils.errors import ErrorCode


YEAR_OUT_OF_RANGE = ErrorCode.YEAR_OUT_OF_RANGE.value


YEAR_GUARDED_CALLS = [
    pytest.param(
        lambda: get_session_results(2017, "Monaco", "R"), id="get_session_results"
    ),
    pytest.param(lambda: get_lap_times(2017, "Monaco", "R", "VER"), id="get_lap_times"),
    pytest.param(lambda: get_fastest_laps(2017, "Monaco", "R"), id="get_fastest_laps"),
    pytest.param(lambda: get_race_pace(2017, "Monaco"), id="get_race_pace"),
    pytest.param(lambda: get_stint_analysis(2017, "Monaco"), id="get_stint_analysis"),
    pytest.param(lambda: get_pit_stops(2017, "Monaco"), id="get_pit_stops"),
    pytest.param(
        lambda: get_qualifying_breakdown(2017, "Monaco"), id="get_qualifying_breakdown"
    ),
    pytest.param(
        lambda: get_lap_telemetry(2017, "Monaco", "Q", "VER"), id="get_lap_telemetry"
    ),
    pytest.param(
        lambda: compare_telemetry(2017, "Monaco", "Q", "VER", "LEC"),
        id="compare_telemetry",
    ),
    pytest.param(
        lambda: get_speed_trap_data(2017, "Monaco", "Q"), id="get_speed_trap_data"
    ),
    pytest.param(lambda: get_sector_times(2017, "Monaco", "Q"), id="get_sector_times"),
    pytest.param(
        lambda: get_corner_analysis(2017, "Monaco", "Q", "VER"),
        id="get_corner_analysis",
    ),
    pytest.param(
        lambda: get_race_control_messages(2017, "Monaco"),
        id="get_race_control_messages",
    ),
    pytest.param(lambda: get_weather(2017, "Monaco"), id="get_weather"),
    pytest.param(
        lambda: get_position_changes(2017, "Monaco"), id="get_position_changes"
    ),
    pytest.param(
        lambda: get_tyre_degradation(2017, "Monaco"), id="get_tyre_degradation"
    ),
    pytest.param(lambda: get_drs_zones(2017, "Monaco"), id="get_drs_zones"),
    pytest.param(
        lambda: get_championship_projection(2017, "Monaco"),
        id="get_championship_projection",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", YEAR_GUARDED_CALLS)
async def test_year_guard_rejects_pre_2018(call):
    result = await call()
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result.get("code") == YEAR_OUT_OF_RANGE


# ---------------------------------------------------------------------------
# Error dicts must satisfy each tool's advertised outputSchema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, args",
    [
        ("get_session_results", {"year": 2017, "event": "Monaco"}),
        ("get_pit_stops", {"year": 2017, "event": "Monaco"}),
        ("get_fastest_laps", {"year": 2017, "event": "Monaco"}),
        (
            "get_lap_times",
            {"year": 2017, "event": "Monaco", "session": "R", "driver": "VER"},
        ),
    ],
)
async def test_error_dict_passes_client_output_validation(tool, args):
    # Regression: tools annotated `-> list[dict]` advertised an outputSchema
    # that rejected the error dict, so MCP clients saw a validation failure
    # instead of the structured error.
    from fastmcp import Client

    from fastf1_mcp.server import mcp

    async with Client(mcp) as client:
        result = await client.call_tool(tool, args)

    assert result.structured_content is not None
    payload = result.structured_content.get("result", result.structured_content)
    assert payload["error"] is True
    assert payload["code"] == YEAR_OUT_OF_RANGE
