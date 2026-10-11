"""
get_replay_state / watch_replay against a local TCP server that speaks the
f1-race-replay telemetry protocol (newline-delimited JSON on a socket).
"""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from fastf1_mcp.config import settings
from fastf1_mcp.tools.replay import get_replay_state, watch_replay
from fastf1_mcp.utils.errors import ErrorCode
from fastf1_mcp.utils.replay_stream import read_snapshot


def make_message(i: int, big: bool = False) -> dict:
    """Frame i of a scripted replay: NOR passes LEC at i=20, HAM pits at i=30,
    safety car from i=40, a race-control message at i=40."""
    t = 1000.0 + i * 2.0
    lec_pos, nor_pos = (2, 3) if i < 20 else (3, 2)
    drivers = {
        "VER": {
            "position": 1,
            "lap": 12,
            "tyre": 1.0,
            "tyre_life": 11.0,
            "speed": 301.4,
            "gear": 8,
            "drs": 12,
            "in_pit": False,
        },
        "LEC": {
            "position": lec_pos,
            "lap": 12,
            "tyre": 2.0,
            "tyre_life": 4.0,
            "speed": 288.0,
            "gear": 7,
            "drs": 8,
            "in_pit": False,
        },
        "NOR": {
            "position": nor_pos,
            "lap": 12,
            "tyre": 0.0,
            "tyre_life": 20.0,
            "speed": 290.0,
            "gear": 7,
            "drs": 0,
            "in_pit": False,
        },
        "HAM": {
            "position": 4,
            "lap": 12,
            "tyre": 1.0,
            "tyre_life": 25.0,
            "speed": 80.0 if i >= 30 else 280.0,
            "gear": 2 if i >= 30 else 7,
            "drs": 0,
            "in_pit": i >= 30,
        },
    }
    rc = [
        {"time": 900.0, "category": "Flag", "message": "GREEN LIGHT", "flag": "GREEN"}
    ]
    if i >= 40:
        rc.append(
            {
                "time": t,
                "category": "SafetyCar",
                "message": "SAFETY CAR DEPLOYED",
                "flag": "",
            }
        )
    lap_times = {
        "VER": [
            {
                "lap": 11,
                "time_s": 92.345,
                "end_time_s": 990.0,
                "gap_s": 0.0,
                "interval_s": None,
            },
            {"lap": 12, "time_s": 92.0, "end_time_s": 99999.0},
        ],  # future
        "LEC": [
            {
                "lap": 11,
                "time_s": 92.9,
                "end_time_s": 992.0,
                "gap_s": 2.1,
                "interval_s": 2.1,
            }
        ],
    }
    if big:  # real payloads are ~1 MB because of the lap history
        lap_times["PAD"] = [
            {"lap": n, "time_s": 90.0, "end_time_s": 1e9, "note": "x" * 80}
            for n in range(20000)
        ]
    return {
        "frame_index": 100 + i,
        "frame": {
            "t": t,
            "lap": 12,
            "drivers": drivers,
            "weather": {"air_temp": 24.06, "track_temp": 38.5, "rain_state": "DRY"},
            "safety_car": {"phase": "on_track"} if i >= 40 else None,
        },
        "track_status": "4" if i >= 40 else "1",
        "playback_speed": 4.0,
        "is_paused": False,
        "total_frames": 1000,
        "race_control_events": rc,
        "session_data": {
            "time": f"00:{16 + i // 30:02d}:{(40 + 2 * i) % 60:02d}",
            "lap": 12,
            "leader": "VER",
            "total_laps": 57,
        },
        "lap_times": lap_times,
    }


@asynccontextmanager
async def replay_server(
    n_messages: int = 60, interval: float = 0.02, big=False, silent=False
):
    async def handle(reader, writer):
        try:
            if silent:  # like a paused replay: connected, but sends nothing
                await reader.read()  # returns once the client disconnects
                return
            for i in range(n_messages):
                writer.write(json.dumps(make_message(i, big)).encode() + b"\n")
                await writer.drain()
                await asyncio.sleep(interval)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    with (
        patch.object(settings, "replay_stream_host", "127.0.0.1"),
        patch.object(settings, "replay_stream_port", port),
    ):
        try:
            yield port
        finally:
            server.close()
            await server.wait_closed()


@pytest.mark.asyncio
async def test_get_replay_state_snapshot():
    async with replay_server():
        state = await get_replay_state(top_n=3)

    assert state["lap"] == 12 and state["totalLaps"] == 57
    assert state["leader"] == "VER"
    assert state["trackStatus"] == "GREEN"
    assert state["safetyCar"] is None
    assert state["progressPct"] == 10.0
    assert state["weather"] == {"airTempC": 24.1, "trackTempC": 38.5, "rain": "DRY"}
    board = state["leaderboard"]
    assert [r["driver"] for r in board] == ["VER", "LEC", "NOR"]  # top_n=3
    ver = board[0]
    assert ver["tyre"] == "MEDIUM" and ver["tyreLife"] == 11
    assert ver["drsOpen"] is True and board[1]["drsOpen"] is False
    # Only laps that finished before the current replay time count.
    assert ver["lastLapSec"] == 92.345 and ver["gapToLeaderSec"] == 0.0
    assert board[1]["intervalSec"] == 2.1
    assert board[2]["lastLapSec"] is None
    assert state["recentRaceControl"][0]["message"] == "GREEN LIGHT"


@pytest.mark.asyncio
async def test_get_replay_state_handles_megabyte_messages():
    async with replay_server(n_messages=3, big=True):
        state = await get_replay_state()
    # ~2 MB line: well past asyncio's default 64 KiB readline limit.
    assert [r["driver"] for r in state["leaderboard"]] == ["VER", "LEC", "NOR", "HAM"]


@pytest.mark.asyncio
async def test_watch_replay_reports_changes():
    async with replay_server(n_messages=60, interval=0.02):
        result = await watch_replay(duration_sec=2)

    assert result["positionChanges"] == [
        {"driver": "NOR", "from": 3, "to": 2},
        {"driver": "LEC", "from": 2, "to": 3},
    ]
    assert result["pitEntries"] == ["HAM"]
    assert result["pitExits"] == []
    assert [(c["from"], c["to"]) for c in result["trackStatusChanges"]] == [
        ("GREEN", "SC")
    ]
    assert [m["message"] for m in result["newRaceControl"]] == ["SAFETY CAR DEPLOYED"]
    assert result["state"]["safetyCar"] == "on_track"
    assert result["replaySecondsCovered"] > 0
    assert result["leaderNow"] == "VER"
    assert result["rewound"] is False


@pytest.mark.asyncio
async def test_no_replay_running():
    with (
        patch.object(settings, "replay_stream_host", "127.0.0.1"),
        patch.object(settings, "replay_stream_port", 1),  # nothing listens here
    ):
        result = await get_replay_state()
    assert result["code"] == ErrorCode.DATA_UNAVAILABLE.value
    assert "No f1-race-replay telemetry stream" in result["message"]


@pytest.mark.asyncio
async def test_paused_replay_times_out_with_hint():
    async with replay_server(silent=True):
        with pytest.raises(Exception) as exc:
            await read_snapshot(timeout=0.3)
    assert "un-pause" in exc.value.suggestions[0]
