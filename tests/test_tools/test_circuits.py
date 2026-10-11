"""Stored circuit outlines, plot_circuit, the track-map fallback, and load checks."""

import base64
import math
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import numpy as np
import pytest
from fastf1.exceptions import DataNotLoadedError
from fastmcp import Client

from fastf1_mcp.server import mcp
from fastf1_mcp.session_manager import SessionDataUnavailable, SessionManager
from fastf1_mcp.utils.circuits import (
    _calendar,
    calendar_years,
    load_circuits,
    resolve_circuit,
    smooth_loop,
)
from fastf1_mcp.utils.errors import ErrorCode, FastF1MCPError
from fastf1_mcp.utils.session_loader import require_session

# Every circuit on an F1 calendar 2018-2026 (2018/19 are not in the stored
# calendars, so list their extra venues explicitly).
MODERN = {cid for y in calendar_years() for cid in _calendar(y)} | {
    "de-1932",
    "fr-1969",
    "ru-2014",
}

# Direction of racing, from circuit guides (see data/circuits/README.md).
DIRECTION = {
    "mc-1929": "clockwise",
    "gb-1948": "clockwise",
    "be-1925": "clockwise",
    "it-1922": "clockwise",
    "hu-1986": "clockwise",
    "es-1991": "clockwise",
    "bh-2002": "clockwise",
    "at-1969": "clockwise",
    "nl-1948": "clockwise",
    "au-1953": "clockwise",
    "mx-1962": "clockwise",
    "cn-2004": "clockwise",
    "es-2026": "clockwise",
    "pt-2008": "clockwise",
    "fr-1969": "clockwise",
    "br-1940": "anticlockwise",
    "us-2012": "anticlockwise",
    "ae-2009": "anticlockwise",
    "az-2016": "anticlockwise",
    "it-1953": "anticlockwise",
    "tr-2005": "anticlockwise",
    "sa-2021": "anticlockwise",
    "sg-2008": "anticlockwise",
    "us-2023": "anticlockwise",
    "us-2022": "anticlockwise",
    "jp-1962": "figure-eight",
}
# Direction of Turn 1 (L/R) — checks both the start point and the direction.
TURN_ONE = {
    "it-1922": "R",
    "gb-1948": "R",
    "be-1925": "R",
    "mc-1929": "R",
    "hu-1986": "R",
    "bh-2002": "R",
    "nl-1948": "R",
    "cn-2004": "R",
    "us-2022": "R",
    "fr-1969": "L",
    "br-1940": "L",
    "us-2012": "L",
    "ae-2009": "L",
    "az-2016": "L",
    "it-1953": "L",
    "sa-2021": "L",
    "sg-2008": "L",
    "us-2023": "L",
}


def _first_turn(xy: np.ndarray) -> str:
    total = 0.0
    for i in range(1, len(xy) - 1):
        a = math.atan2(*(xy[i] - xy[i - 1])[::-1])
        b = math.atan2(*(xy[i + 1] - xy[i])[::-1])
        total += (b - a + math.pi) % (2 * math.pi) - math.pi
        if abs(total) > math.radians(50):
            return "L" if total > 0 else "R"
    return "?"


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------


def test_all_modern_circuits_present_with_accurate_lengths():
    circuits = load_circuits()
    assert MODERN <= set(circuits)
    for cid in MODERN:
        c = circuits[cid]
        err = abs(c.measured_length_m - c.length_m) / c.length_m
        assert err < 0.015, (
            f"{cid}: outline {c.measured_length_m:.0f} m vs {c.length_m}"
        )


@pytest.mark.parametrize("cid, expected", sorted(DIRECTION.items()))
def test_direction_of_racing(cid, expected):
    assert load_circuits()[cid].direction == expected


@pytest.mark.parametrize("cid, expected", sorted(TURN_ONE.items()))
def test_outline_starts_on_the_line_and_turns_the_right_way(cid, expected):
    assert _first_turn(load_circuits()[cid].xy) == expected


def test_smooth_loop_keeps_start_and_length():
    for cid in ("mc-1929", "jp-1962", "sg-2008"):
        c = load_circuits()[cid]
        s = smooth_loop(c.xy)
        assert np.allclose(s[0], c.xy[0], atol=1.0)
        loop = np.vstack([s, s[:1]])
        length = np.hypot(*np.diff(loop, axis=0).T).sum()
        assert length == pytest.approx(c.length_m, rel=0.02)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query, year, expected",
    [
        ("Monaco", None, "mc-1929"),
        ("Monaco Grand Prix", None, "mc-1929"),
        ("British GP", None, "gb-1948"),
        ("COTA", None, "us-2012"),
        ("United States Grand Prix", None, "us-2012"),
        ("Spa", None, "be-1925"),
        ("São Paulo Grand Prix", None, "br-1940"),
        ("Abu Dhabi", None, "ae-2009"),
        ("Emilia Romagna", None, "it-1953"),
        ("Zandvort", None, "nl-1948"),  # typo
        ("Spanish Grand Prix", 2025, "es-1991"),
        ("Spanish Grand Prix", 2026, "es-2026"),
        ("12", 2024, "gb-1948"),
        (1, 2026, "au-1953"),
    ],
)
def test_resolve_circuit(query, year, expected):
    assert resolve_circuit(query, year).id == expected


@pytest.mark.parametrize(
    "query, year", [("Narnia", None), ("Grand Prix", None), ("3", None), ("40", 2024)]
)
def test_resolve_circuit_errors(query, year):
    with pytest.raises(FastF1MCPError) as exc:
        resolve_circuit(query, year)
    assert exc.value.code == ErrorCode.INVALID_PARAMETER
    assert len(exc.value.suggestions) <= 6


# ---------------------------------------------------------------------------
# plot_circuit tool
# ---------------------------------------------------------------------------


async def _call(name, **args):
    async with Client(mcp) as client:
        return await client.call_tool(name, args)


def _png(result) -> bytes:
    img = result.content[0]
    assert img.type == "image"
    data = base64.b64decode(img.data)
    assert data.startswith(b"\x89PNG")
    return data


@pytest.mark.asyncio
async def test_plot_circuit_single():
    result = await _call("plot_circuit", circuit="Singapore")
    _png(result)
    meta = result.structured_content
    assert meta["circuitId"] == "sg-2008"
    assert meta["direction"] == "anticlockwise"
    assert meta["lengthM"] == 4928
    assert "Marina Bay" in result.content[1].text


@pytest.mark.asyncio
async def test_plot_circuit_season_sheet():
    result = await _call("plot_circuit", year=2024)
    _png(result)
    rounds = result.structured_content["rounds"]
    assert len(rounds) == 24
    assert rounds[0]["circuitId"] == "bh-2002"
    assert rounds[11]["name"] == "Silverstone Circuit"


@pytest.mark.asyncio
async def test_plot_circuit_bad_input():
    none = await _call("plot_circuit")
    year = await _call("plot_circuit", year=1999)
    name = await _call("plot_circuit", circuit="Narnia")
    for r in (none, year, name):
        assert r.structured_content["code"] == ErrorCode.INVALID_PARAMETER.value


@pytest.mark.asyncio
async def test_plot_circuit_save_path(tmp_path):
    target = tmp_path / "suzuka.png"
    result = await _call("plot_circuit", circuit="Suzuka", save_path=str(target))
    assert target.read_bytes() == _png(result)


# ---------------------------------------------------------------------------
# Failed FastF1 loads: clear errors, no caching, track-map fallback
# ---------------------------------------------------------------------------


def _unloaded_session():
    session = MagicMock()
    session.load = MagicMock(return_value=None)
    type(session).laps = PropertyMock(
        side_effect=DataNotLoadedError("The data you are trying to access...")
    )
    return session


@pytest.mark.asyncio
async def test_failed_load_raises_and_is_not_cached():
    manager = SessionManager(max_sessions=5)
    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session",
        return_value=_unloaded_session(),
    ):
        with pytest.raises(SessionDataUnavailable) as exc:
            await manager.get_session(2024, "Monza", "Q")
    assert exc.value.missing == ["laps"]
    assert manager.get_cache_status()["sessions_cached"] == 0


@pytest.mark.asyncio
async def test_require_session_maps_failed_load_to_data_unavailable():
    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        side_effect=SessionDataUnavailable(["laps"]),
    ):
        with pytest.raises(FastF1MCPError) as exc:
            await require_session(2024, "Monza", "Q")
    assert exc.value.code == ErrorCode.DATA_UNAVAILABLE
    assert "livetiming.formula1.com" in exc.value.suggestions[0]


@pytest.mark.asyncio
async def test_track_map_falls_back_to_stored_outline():
    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session_with_telemetry",
        new_callable=AsyncMock,
        side_effect=SessionDataUnavailable(["laps", "telemetry"]),
    ):
        trace = await _call("plot_track_map", year=2024, event="Monza")
        positions = await _call(
            "plot_track_map", year=2024, event="Monza", session="R", at_lap=5
        )
    _png(trace)
    meta = trace.structured_content
    assert meta["mode"] == "stored-outline"
    assert meta["circuitId"] == "it-1922"
    assert "could not download" in meta["fallbackReason"]
    # Car positions need live data — no fallback there.
    assert positions.structured_content["code"] == ErrorCode.DATA_UNAVAILABLE.value
