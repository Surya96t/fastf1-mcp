"""DRS zones, track map and championship projection against the synthetic race."""

import base64
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from fastmcp import Client

from tests import _race_fixture as rf
from tests._helpers import FakeLaps
from tests.conftest import make_mock_lap
from fastf1_mcp.server import mcp
from fastf1_mcp.tools.championship import (
    _positions_at_lap,
    get_championship_projection,
)
from fastf1_mcp.tools.track import detect_drs_zones, get_drs_zones
from fastf1_mcp.utils.errors import ErrorCode


def _mock_lap(driver="VER", tel=None):
    lap = make_mock_lap(driver=driver)
    lap.get_telemetry.return_value = tel if tel is not None else rf.make_lap_telemetry()
    return lap


@contextmanager
def _session(session=None, tel_factory=rf.make_lap_telemetry):
    session = session or rf.make_race_session()
    with ExitStack() as stack:
        for target in ("get_session", "get_session_with_telemetry"):
            stack.enter_context(
                patch(
                    f"fastf1_mcp.utils.session_loader.session_manager.{target}",
                    new_callable=AsyncMock,
                    return_value=session,
                )
            )
        # Fastest laps used for DRS detection, and lap lookup for charts.
        stack.enter_context(
            patch.object(
                FakeLaps,
                "pick_fastest",
                lambda self: _mock_lap(tel=tel_factory()) if len(self) else None,
            )
        )
        stack.enter_context(
            patch(
                "fastf1_mcp.tools.charts._get_driver_lap",
                side_effect=lambda _s, d, _l: _mock_lap(d, tel_factory()),
            )
        )
        stack.enter_context(
            patch(
                "fastf1_mcp.tools.charts._lap_position_data",
                side_effect=rf.lap_position_data,
            )
        )
        yield session


# ---------------------------------------------------------------------------
# DRS zones
# ---------------------------------------------------------------------------


def test_detect_drs_zones_recovers_zones_and_merges_line_crossing():
    zones, lap_len = detect_drs_zones([rf.make_lap_telemetry() for _ in range(4)])
    assert lap_len == pytest.approx(rf.LAP_LENGTH)
    assert len(zones) == 2
    z1, z2 = zones
    assert (z1["startM"], z1["endM"]) == pytest.approx((700, 1300), abs=5)
    assert not z1["crossesLine"]
    assert (z2["startM"], z2["endM"]) == pytest.approx((3000, 200), abs=5)
    assert z2["crossesLine"] is True
    assert z2["lengthM"] == pytest.approx(500, abs=10)
    assert [z["zone"] for z in zones] == [1, 2]


def test_detect_drs_zones_ignores_one_off_activations():
    tels = [rf.make_lap_telemetry() for _ in range(5)]
    odd = rf.make_lap_telemetry()
    odd.loc[(odd["Distance"] > 2000) & (odd["Distance"] < 2200), "DRS"] = 12
    zones, _ = detect_drs_zones(tels + [odd])
    assert len(zones) == 2  # the 1-in-6 glitch is below the 30% threshold


@pytest.mark.asyncio
async def test_get_drs_zones_with_corner_context():
    with _session():
        result = await get_drs_zones(2024, "Test")
    zones = result["zones"]
    assert len(zones) == 2
    assert (zones[0]["afterCorner"], zones[0]["beforeCorner"]) == ("T1", "T2")
    assert (zones[1]["afterCorner"], zones[1]["beforeCorner"]) == ("T3", "T1")
    assert result["referenceLaps"] == 3


@pytest.mark.asyncio
async def test_get_drs_zones_none_found_mentions_2026_active_aero():
    def no_drs():
        tel = rf.make_lap_telemetry()
        tel["DRS"] = 0
        return tel

    with _session(tel_factory=no_drs):
        result = await get_drs_zones(2026, "Test")
    assert result["code"] == ErrorCode.DATA_UNAVAILABLE.value
    assert "active aerodynamics" in result["suggestions"][0]


# ---------------------------------------------------------------------------
# Track map
# ---------------------------------------------------------------------------


async def _call(name, **args):
    async with Client(mcp) as client:
        return await client.call_tool(name, {"year": 2024, "event": "Test", **args})


def _png(result) -> bytes:
    img = result.content[0]
    assert img.type == "image"
    data = base64.b64decode(img.data)
    assert data.startswith(b"\x89PNG")
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("color_by", ["speed", "gear", "none"])
async def test_track_map_trace(color_by):
    with _session():
        result = await _call("plot_track_map", driver="VER", color_by=color_by)
    _png(result)
    meta = result.structured_content
    assert meta["mode"] == "trace" and meta["colorBy"] == color_by
    assert meta["corners"] == len(rf.CORNERS)
    assert len(meta["drsZones"]) == 2


@pytest.mark.asyncio
async def test_track_map_trace_caption_reports_speed_range():
    with _session():
        result = await _call("plot_track_map", driver="VER")
    assert "speed 70–300 km/h" in result.content[1].text


@pytest.mark.asyncio
async def test_track_map_without_circuit_info_still_renders():
    session = rf.make_race_session()
    session.get_circuit_info.side_effect = ConnectionError("offline")
    with _session(session):
        result = await _call("plot_track_map", driver="VER")
    _png(result)
    assert result.structured_content["corners"] == 0


@pytest.mark.asyncio
async def test_track_map_positions_mode_orders_cars():
    with _session() as session:
        result = await _call("plot_track_map", session="R", driver="VER", at_lap=10)
    _png(result)
    meta = result.structured_content
    laps = session.laps
    lap10 = laps[laps["LapNumber"] == 10]
    leader = lap10.loc[lap10["LapStartTime"].idxmin(), "Driver"]
    order = meta["order"]
    assert meta["mode"] == "positions" and meta["atLap"] == 10
    assert order[0]["driver"] == leader and order[0]["lap"] == 10
    assert [o["position"] for o in order] == [1, 2, 3]
    assert all(o["lap"] in (9, 10) for o in order)
    assert meta["notRunning"] == []


@pytest.mark.asyncio
async def test_track_map_rejects_bad_inputs():
    with _session():
        bad_colour = await _call("plot_track_map", driver="VER", color_by="rainbow")
        bad_lap = await _call("plot_track_map", session="R", driver="VER", at_lap=99)
    assert bad_colour.structured_content["code"] == ErrorCode.INVALID_PARAMETER.value
    assert bad_lap.structured_content["code"] == ErrorCode.INVALID_PARAMETER.value


# ---------------------------------------------------------------------------
# Championship projection
# ---------------------------------------------------------------------------

BASE_DRIVERS = {
    "LEC": (105.0, "Ferrari"),
    "VER": (100.0, "Red Bull"),
    "HAM": (95.0, "Mercedes"),
}
BASE_TEAMS = {"Ferrari": 160.0, "Red Bull": 150.0, "Mercedes": 120.0}


def _multi(df):
    m = MagicMock()
    m.content = [df] if df is not None else []
    return m


@contextmanager
def _ergast(sprint: pd.DataFrame | None = None, fail: bool = False):
    with patch("fastf1_mcp.tools.championship.Ergast") as E:
        api = E.return_value
        if fail:
            api.get_driver_standings.side_effect = ConnectionError("down")
        api.get_driver_standings.return_value = _multi(
            pd.DataFrame(
                {
                    "driverCode": list(BASE_DRIVERS),
                    "points": [v[0] for v in BASE_DRIVERS.values()],
                    "constructorNames": [[v[1]] for v in BASE_DRIVERS.values()],
                }
            )
        )
        api.get_constructor_standings.return_value = _multi(
            pd.DataFrame(
                {
                    "constructorName": list(BASE_TEAMS),
                    "points": list(BASE_TEAMS.values()),
                }
            )
        )
        api.get_sprint_results.return_value = _multi(sprint)
        yield api


def _finish_order(session):
    return session.results.sort_values("Position")["Abbreviation"].tolist()


@pytest.mark.asyncio
async def test_final_projection_uses_official_points():
    with _session() as session, _ergast():
        result = await get_championship_projection(2024, "Test")

    order = _finish_order(session)
    assert result["source"] == "official"
    assert result["lap"] == result["totalLaps"] == rf.TOTAL_LAPS
    by = {d["driver"]: d for d in result["drivers"]}
    for code, pts in zip(order, [25.0, 18.0, 15.0]):
        assert by[code]["racePoints"] == pts
        assert by[code]["projected"] == BASE_DRIVERS[code][0] + pts
    # Positions are re-ranked by projected points.
    projected = [d["projected"] for d in result["drivers"]]
    assert projected == sorted(projected, reverse=True)
    teams = {t["team"]: t for t in result["constructors"]}
    # Constructors are keyed by the Ergast names, not FastF1's TeamName.
    assert set(teams) == set(BASE_TEAMS)
    assert teams["Red Bull"]["racePoints"] == by["VER"]["racePoints"]
    assert result["baseline"] == {"afterRound": 4, "includesSprint": False}


@pytest.mark.asyncio
async def test_lead_change_detected_and_history():
    with _session() as session, _ergast():
        result = await get_championship_projection(2024, "Test", include_history=True)
    s = result["summary"]
    assert s["leaderBefore"] == "LEC"
    if _finish_order(session)[0] == "VER":  # VER wins → 125 vs LEC ≤ 123
        assert s["leader"] == "VER" and s["leadChanged"] is True
    history = result["leadByLap"]
    assert [h["lap"] for h in history] == list(range(1, rf.TOTAL_LAPS + 1))
    assert history[-1]["leader"] == s["leader"]
    assert history[-1]["margin"] == s["marginToSecond"]


@pytest.mark.asyncio
async def test_mid_race_projection_matches_running_order_plus_fastest_lap():
    with _session() as session, _ergast():
        result = await get_championship_projection(2024, "Test", after_lap=10)

    laps = session.laps
    at10 = laps[laps["LapNumber"] == 10].sort_values("Position")["Driver"].tolist()
    early = laps[laps["LapNumber"] <= 10].dropna(subset=["LapTime"])
    fl = early.loc[early["LapTime"].idxmin(), "Driver"]
    expected = {d: p for d, p in zip(at10, [25.0, 18.0, 15.0])}
    expected[fl] += 1.0  # 2024: fastest-lap point for a top-10 finisher

    assert result["source"] == "projected" and result["lap"] == 10
    by = {d["driver"]: d for d in result["drivers"]}
    for code, pts in expected.items():
        assert by[code]["racePoints"] == pts
        assert by[code]["racePosition"] == at10.index(code) + 1


@pytest.mark.asyncio
async def test_no_fastest_lap_point_from_2025():
    with _session(), _ergast():
        result = await get_championship_projection(2025, "Test", after_lap=10)
    assert sorted(d["racePoints"] for d in result["drivers"]) == [15.0, 18.0, 25.0]


@pytest.mark.asyncio
async def test_sprint_weekend_adds_sprint_points_to_baseline():
    sprint = pd.DataFrame(
        {
            "driverCode": ["HAM", "VER"],
            "points": [8.0, 7.0],
            "constructorName": ["Mercedes", "Red Bull"],
        }
    )
    with _session(), _ergast(sprint=sprint):
        result = await get_championship_projection(2024, "Test")
    by = {d["driver"]: d for d in result["drivers"]}
    assert by["HAM"]["pointsBefore"] == 95.0 + 8.0
    assert by["VER"]["pointsBefore"] == 100.0 + 7.0
    teams = {t["team"]: t for t in result["constructors"]}
    assert teams["Mercedes"]["pointsBefore"] == 128.0
    assert result["baseline"]["includesSprint"] is True


@pytest.mark.asyncio
async def test_sprint_session_uses_sprint_table():
    session = rf.make_race_session()
    session.results["Points"] = np.nan  # force the computed path at the end
    with _session(session), _ergast() as api:
        result = await get_championship_projection(2024, "Test", session="S")
    assert sorted(d["racePoints"] for d in result["drivers"]) == [6.0, 7.0, 8.0]
    api.get_sprint_results.assert_not_called()


@pytest.mark.asyncio
async def test_round_one_starts_from_zero():
    session = rf.make_race_session()
    session.event["RoundNumber"] = 1
    with _session(session), _ergast() as api:
        result = await get_championship_projection(2024, "Test")
    api.get_driver_standings.assert_not_called()
    assert all(d["pointsBefore"] == 0.0 for d in result["drivers"])
    # Without Ergast names, constructors fall back to FastF1 team names.
    assert {t["team"] for t in result["constructors"]} == {
        "Red Bull Racing",
        "Ferrari",
        "Mercedes",
    }


@pytest.mark.asyncio
async def test_championship_rejects_non_points_session_and_ergast_failure():
    with _session(), _ergast():
        q = await get_championship_projection(2024, "Test", session="Q")
    with _session(), _ergast(fail=True):
        down = await get_championship_projection(2024, "Test")
    assert q["code"] == ErrorCode.INVALID_PARAMETER.value
    assert down["code"] == ErrorCode.DATA_UNAVAILABLE.value


def test_positions_at_lap_keeps_lapped_finishers_and_drops_retirements():
    laps = FakeLaps(
        {
            "Driver": ["A", "A", "A", "B", "B", "C"],
            "LapNumber": [1.0, 2.0, 3.0, 1.0, 2.0, 1.0],
            "Position": [1.0, 1.0, 1.0, 2.0, 2.0, 3.0],
            "LapTime": pd.to_timedelta([90, 90, 90, 95, 95, 99], unit="s"),
        }
    )
    results = pd.DataFrame(
        {"Abbreviation": ["A", "B", "C"], "Status": ["Finished", "+1 Lap", "Engine"]}
    )
    assert _positions_at_lap(laps, results, 3) == {"A": 1, "B": 2}
    assert _positions_at_lap(laps, results, 1) == {"A": 1, "B": 2, "C": 3}
