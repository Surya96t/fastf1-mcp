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
]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", YEAR_GUARDED_CALLS)
async def test_year_guard_rejects_pre_2018(call):
    result = await call()
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result.get("code") == YEAR_OUT_OF_RANGE
