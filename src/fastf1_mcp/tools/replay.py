"""
Live tools for a running f1-race-replay session.

These read the replay's telemetry stream (see utils/replay_stream.py) so
Claude can follow along while a race is being replayed — "who's leading
now", "what just happened" — like a race engineer on the pit wall.
"""

import logging

from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.replay_stream import read_snapshot, read_window
from ..utils.session_loader import tool_handler
from ..utils.timing import TRACK_STATUS_LABELS

logger = logging.getLogger(__name__)

# f1-race-replay encodes compounds as integers (src/lib/tyres.py).
TYRES = {0: "SOFT", 1: "MEDIUM", 2: "HARD", 3: "INTERMEDIATE", 4: "WET"}
DRS_OPEN_MIN = 10


def _tyre(value) -> str:
    try:
        return TYRES.get(int(round(float(value))), "UNKNOWN")
    except (TypeError, ValueError):
        return "UNKNOWN"


def _status_label(code) -> str:
    return TRACK_STATUS_LABELS.get(str(code), str(code))


def _num(value, digits: int = 1):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _last_completed_laps(msg: dict) -> dict[str, dict]:
    """Latest lap entry per driver that finished before the current replay time."""
    now = (msg.get("frame") or {}).get("t")
    out = {}
    for code, entries in (msg.get("lap_times") or {}).items():
        done = [
            e
            for e in entries or []
            if isinstance(e, dict)
            and e.get("time_s") is not None
            and (now is None or (e.get("end_time_s") or 0) <= now)
        ]
        if done:
            out[code] = max(done, key=lambda e: e.get("lap") or 0)
    return out


def _leaderboard(msg: dict) -> list[dict]:
    drivers = ((msg.get("frame") or {}).get("drivers")) or {}
    last = _last_completed_laps(msg)
    rows = []
    for code, d in drivers.items():
        lap_entry = last.get(code, {})
        rows.append(
            {
                "position": d.get("position"),
                "driver": code,
                "lap": d.get("lap"),
                "tyre": _tyre(d.get("tyre")),
                "tyreLife": _num(d.get("tyre_life"), 0),
                "speedKph": _num(d.get("speed")),
                "gear": d.get("gear"),
                "drsOpen": (d.get("drs") or 0) >= DRS_OPEN_MIN,
                "inPit": bool(d.get("in_pit")),
                "lastLapSec": _num(lap_entry.get("time_s"), 3),
                "gapToLeaderSec": _num(lap_entry.get("gap_s"), 3),
                "intervalSec": _num(lap_entry.get("interval_s"), 3),
            }
        )
    rows.sort(key=lambda r: (r["position"] is None, r["position"] or 0))
    return rows


def _summarise(msg: dict, top_n: int) -> dict:
    frame = msg.get("frame") or {}
    sd = msg.get("session_data") or {}
    total_frames = msg.get("total_frames") or 0
    sc = frame.get("safety_car")
    rc = msg.get("race_control_events") or []
    weather = frame.get("weather") or {}
    return {
        "replayTime": sd.get("time"),
        "replayTimeSec": _num(frame.get("t"), 1),
        "lap": sd.get("lap") or frame.get("lap"),
        "totalLaps": sd.get("total_laps"),
        "leader": sd.get("leader"),
        "trackStatus": _status_label(msg.get("track_status")),
        "safetyCar": sc.get("phase") if isinstance(sc, dict) else None,
        "playbackSpeed": msg.get("playback_speed"),
        "paused": bool(msg.get("is_paused")),
        "progressPct": round(100 * (msg.get("frame_index") or 0) / total_frames, 1)
        if total_frames
        else None,
        "weather": {
            "airTempC": _num(weather.get("air_temp")),
            "trackTempC": _num(weather.get("track_temp")),
            "rain": weather.get("rain_state"),
        }
        if weather
        else None,
        "leaderboard": _leaderboard(msg)[:top_n],
        "recentRaceControl": [
            {
                "replayTimeSec": _num(e.get("time"), 1),
                "category": e.get("category"),
                "flag": e.get("flag") or None,
                "message": e.get("message"),
            }
            for e in rc[-5:]
        ],
    }


@tool_handler
async def get_replay_state(top_n: int = 20) -> dict:
    """
    Snapshot of a race currently playing in f1-race-replay: replay time,
    lap, leader, track status, safety car, weather, the live leaderboard
    (position, tyre, tyre age, speed, gear, DRS, pit, last lap, gaps) and
    the latest race-control messages.

    Data source: f1-race-replay telemetry stream (localhost:9999)

    Use when the user is watching a replay and asks "who's leading now",
    "what tyres are they on", "is there a safety car", or wants live
    commentary. The replay must be running and un-paused.

    Args:
        top_n: Number of leaderboard rows to return (default 20)

    Returns:
        {
            "replayTime": "00:41:12", "lap": 23, "totalLaps": 57,
            "leader": "VER", "trackStatus": "SC", "safetyCar": "on_track",
            "playbackSpeed": 4.0, "paused": false, "progressPct": 40.2,
            "weather": {"airTempC": 24.1, "trackTempC": 38.0, "rain": "DRY"},
            "leaderboard": [
                {"position": 1, "driver": "VER", "lap": 23, "tyre": "HARD",
                 "tyreLife": 9, "speedKph": 182.0, "gear": 5,
                 "drsOpen": false, "inPit": false, "lastLapSec": 95.12,
                 "gapToLeaderSec": 0.0, "intervalSec": null}, ...
            ],
            "recentRaceControl": [{"replayTimeSec": 2401.2,
                                   "category": "SafetyCar",
                                   "message": "SAFETY CAR DEPLOYED"}, ...]
        }

    Note:
        Positions come from the replay's own running order, which can be
        briefly wrong near the start/finish line or during pit stops (a
        known limitation of f1-race-replay). Gaps are from the last
        completed lap.
    """
    logger.info(f"get_replay_state: {top_n=}")
    msg = await read_snapshot()
    return _summarise(msg, max(1, int(top_n)))


@tool_handler
async def watch_replay(duration_sec: float = 10.0) -> dict:
    """
    Watch a running f1-race-replay for a few seconds and report what
    happened: position changes, pit entries/exits, track-status changes
    (SC/VSC/red flag), new race-control messages and laps completed.

    Data source: f1-race-replay telemetry stream (localhost:9999)

    Use for live commentary ("tell me what's happening"), or call it
    repeatedly to follow a replay like a race engineer. Combine with a
    faster playback speed to cover more of the race per call.

    Args:
        duration_sec: How long to watch, in real seconds (1-60, default 10)

    Returns:
        {
            "from": {"replayTime": "00:41:12", "lap": 23},
            "to": {"replayTime": "00:42:52", "lap": 24},
            "replaySecondsCovered": 100.0,
            "positionChanges": [{"driver": "NOR", "from": 4, "to": 3}, ...],
            "pitEntries": ["HAM"], "pitExits": [],
            "trackStatusChanges": [{"from": "GREEN", "to": "SC",
                                    "replayTimeSec": 2401.2}],
            "newRaceControl": [{"category": "SafetyCar",
                                "message": "SAFETY CAR DEPLOYED", ...}],
            "leaderNow": "VER",
            "state": { ...same shape as get_replay_state (top 10)... }
        }

    Note:
        Messages are sampled four times a second, so a position swap that
        reverts within ~0.25 s of replay time can be missed. `rewound` is
        true if the user scrubbed backwards during the window.
    """
    duration_sec = max(1.0, min(float(duration_sec), 60.0))
    logger.info(f"watch_replay: {duration_sec=}")

    status_changes: list[dict] = []
    pit_entries: list[str] = []
    pit_exits: list[str] = []
    state = {"prev_status": None, "prev_pit": {}, "prev_t": None, "rewound": False}

    def _track(msg: dict) -> None:
        status = _status_label(msg.get("track_status"))
        t = _num((msg.get("frame") or {}).get("t"), 1)
        if state["prev_status"] is not None and status != state["prev_status"]:
            status_changes.append(
                {"from": state["prev_status"], "to": status, "replayTimeSec": t}
            )
        state["prev_status"] = status
        if t is not None:
            if state["prev_t"] is not None and t < state["prev_t"]:
                state["rewound"] = True
            state["prev_t"] = t
        for code, d in (((msg.get("frame") or {}).get("drivers")) or {}).items():
            in_pit = bool(d.get("in_pit"))
            prev = state["prev_pit"].get(code)
            if prev is False and in_pit and code not in pit_entries:
                pit_entries.append(code)
            if prev is True and not in_pit and code not in pit_exits:
                pit_exits.append(code)
            state["prev_pit"][code] = in_pit

    samples = await read_window(duration_sec, on_sample=_track)
    first, last = samples[0], samples[-1]
    if len(samples) > 1:
        _track(last)  # the tail sample may not have gone through on_sample

    def _positions(msg: dict) -> dict[str, int]:
        drivers = ((msg.get("frame") or {}).get("drivers")) or {}
        return {
            c: d["position"]
            for c, d in drivers.items()
            if d.get("position") is not None
        }

    p0, p1 = _positions(first), _positions(last)
    changes = sorted(
        (
            {"driver": c, "from": p0[c], "to": p1[c]}
            for c in p1
            if c in p0 and p0[c] != p1[c]
        ),
        key=lambda x: x["to"],
    )

    rc0 = len(first.get("race_control_events") or [])
    new_rc = [
        {
            "replayTimeSec": _num(e.get("time"), 1),
            "category": e.get("category"),
            "flag": e.get("flag") or None,
            "message": e.get("message"),
        }
        for e in (last.get("race_control_events") or [])[rc0:]
    ]

    def _when(msg: dict) -> dict:
        sd = msg.get("session_data") or {}
        return {"replayTime": sd.get("time"), "lap": sd.get("lap")}

    t0 = (first.get("frame") or {}).get("t")
    t1 = (last.get("frame") or {}).get("t")
    if t0 is None or t1 is None:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE, "Replay messages carried no frame time."
        )
    return {
        "from": _when(first),
        "to": _when(last),
        "replaySecondsCovered": round(float(t1) - float(t0), 1),
        "samples": len(samples),
        "positionChanges": changes,
        "pitEntries": pit_entries,
        "pitExits": pit_exits,
        "trackStatusChanges": status_changes,
        "newRaceControl": new_rc,
        "leaderNow": (last.get("session_data") or {}).get("leader"),
        # The user rewound/scrubbed during the window, so changes may not
        # be chronological.
        "rewound": state["rewound"],
        "state": _summarise(last, 10),
    }
