"""Chart tools: return a real PNG + caption, save on request, validate inputs."""

import base64
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import Client

from tests import _race_fixture as rf
from tests.conftest import make_mock_lap
from fastf1_mcp.server import mcp
from fastf1_mcp.utils.errors import ErrorCode

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@contextmanager
def _session(session=None):
    session = session or rf.make_race_session()

    def _lap(_s, driver, _l):
        lap = make_mock_lap(driver=driver)
        lap.get_telemetry.return_value = rf.make_lap_telemetry(
            {"VER": 0.0, "LEC": 8.0}.get(driver, 0.0)
        )
        return lap

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
        patch("fastf1_mcp.tools.charts._get_driver_lap", side_effect=_lap),
    ):
        yield session


async def _call(name: str, **args):
    async with Client(mcp) as client:
        return await client.call_tool(name, {"year": 2024, "event": "Test", **args})


def _png_bytes(result) -> bytes:
    image = result.content[0]
    assert image.type == "image" and image.mimeType == "image/png"
    data = base64.b64decode(image.data)
    assert data.startswith(PNG_MAGIC)
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, args",
    [
        ("plot_speed_trace", {"session": "Q", "driver": "VER"}),
        ("plot_speed_trace", {"session": "Q", "driver": "VER", "driver2": "LEC"}),
        ("plot_position_chart", {}),
        ("plot_tyre_strategy", {}),
        ("plot_lap_times", {"drivers": ["VER", "LEC"]}),
    ],
)
async def test_chart_tools_return_png_and_caption(tool, args):
    with _session():
        result = await _call(tool, **args)
    assert not result.is_error
    assert len(_png_bytes(result)) > 10_000
    assert result.content[1].type == "text" and result.content[1].text


@pytest.mark.asyncio
async def test_speed_trace_caption_reports_gap_sign():
    with _session():
        result = await _call(
            "plot_speed_trace", session="Q", driver="VER", driver2="LEC"
        )
    # VER is faster in every corner, so finishes ahead (negative gap).
    assert "VER vs LEC" in result.content[1].text
    assert "gap at the line -" in result.content[1].text
    assert result.structured_content["corners"] == len(rf.CORNERS)


@pytest.mark.asyncio
async def test_speed_trace_still_renders_without_corner_data():
    session = rf.make_race_session()
    session.get_circuit_info.side_effect = ConnectionError("offline")
    with _session(session):
        result = await _call("plot_speed_trace", session="Q", driver="VER")
    _png_bytes(result)
    assert result.structured_content["corners"] == 0


@pytest.mark.asyncio
async def test_tyre_strategy_structured_content():
    with _session():
        result = await _call("plot_tyre_strategy")
    sc = result.structured_content
    assert sc["stops"] == {"VER": 1, "LEC": 1, "HAM": 1}
    assert sc["strategies"]["LEC"] == ["SOFT", "HARD"]


@pytest.mark.asyncio
async def test_chart_save_path_writes_png(tmp_path):
    target = tmp_path / "positions.png"
    with _session():
        result = await _call("plot_position_chart", save_path=str(target))
    assert target.read_bytes() == _png_bytes(result)
    assert result.structured_content["savedTo"].endswith("positions.png")
    assert "Saved to" in result.content[1].text


@pytest.mark.asyncio
async def test_lap_times_rejects_more_than_three_drivers():
    with _session():
        result = await _call("plot_lap_times", drivers=["VER", "LEC", "HAM", "NOR"])
    assert result.structured_content["code"] == ErrorCode.INVALID_PARAMETER.value


@pytest.mark.asyncio
async def test_position_chart_unknown_highlight():
    with _session():
        result = await _call("plot_position_chart", highlight=["XXX"])
    assert result.structured_content["code"] == ErrorCode.DRIVER_NOT_FOUND.value


@pytest.mark.asyncio
async def test_chart_year_guard():
    result = await _call("plot_tyre_strategy", year=2017)
    assert result.structured_content["code"] == ErrorCode.YEAR_OUT_OF_RANGE.value
