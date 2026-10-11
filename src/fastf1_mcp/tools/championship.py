"""Live championship projection — standings as if the race ended on a given lap."""

import logging

import pandas as pd
from fastf1.ergast import Ergast

from ..utils.converters import build_driver_lookup, to_jsonable
from ..utils.ergast import first_frame, run_ergast
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.session_loader import require_session, tool_handler

logger = logging.getLogger(__name__)

RACE_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]


def sprint_points(year: int) -> list[int]:
    """Sprint points table (2021: top 3; 2022 onwards: top 8)."""
    return [3, 2, 1] if year == 2021 else [8, 7, 6, 5, 4, 3, 2, 1]


def has_fastest_lap_point(year: int, session: str) -> bool:
    """+1 for the fastest lap (if classified top 10) in GP races 2019-2024."""
    return session == "R" and 2019 <= year <= 2024


def _points_for(position: int | None, table: list[int]) -> float:
    if position is None or position < 1 or position > len(table):
        return 0.0
    return float(table[position - 1])


_RUNNING_STATUSES = ("finished", "lap", "lapped")


def _is_running(status) -> bool:
    """FastF1 status for a classified finisher: Finished, +1 Lap, Lapped…"""
    s = str(status or "").lower()
    return any(k in s for k in _RUNNING_STATUSES)


def _positions_at_lap(laps, results, lap: int) -> dict[str, int]:
    """
    Running order at the end of `lap`, keyed by driver code.

    Uses each driver's latest completed lap up to `lap`, so lapped cars
    (who complete fewer laps) keep their place. A driver whose final lap
    is before `lap` and who did not finish is treated as retired.
    """
    df = laps.dropna(subset=["Driver", "LapNumber", "Position"])
    df = df[df["LapNumber"] <= lap]
    if df.empty:
        return {}
    latest = df.sort_values("LapNumber").groupby("Driver").tail(1)

    status = {}
    last_lap = laps.groupby("Driver")["LapNumber"].max().to_dict()
    if results is not None and "Abbreviation" in getattr(results, "columns", []):
        status = dict(zip(results["Abbreviation"], results.get("Status", "")))

    rows = []
    for _, r in latest.iterrows():
        drv = r["Driver"]
        retired_before = last_lap.get(drv, 0) < lap and not _is_running(status.get(drv))
        if retired_before and r["LapNumber"] < lap:
            continue
        # Primary: laps completed; secondary: running position on that lap.
        rows.append((-int(r["LapNumber"]), float(r["Position"]), drv))
    rows.sort()
    return {drv: i + 1 for i, (_, _, drv) in enumerate(rows)}


def _fastest_lap_holder(laps, lap: int) -> str | None:
    df = laps[(laps["LapNumber"] <= lap)].dropna(subset=["LapTime", "Driver"])
    if "Deleted" in df.columns:
        df = df[~df["Deleted"].fillna(False).astype(bool)]
    if df.empty:
        return None
    return df.loc[df["LapTime"].idxmin(), "Driver"]


@tool_handler
async def get_championship_projection(
    year: int,
    event: str | int,
    session: str = "R",
    after_lap: int | None = None,
    include_history: bool = False,
) -> dict:
    """
    Project the drivers' and constructors' championships as if the race
    finished in the running order at a given lap ("live" standings).

    Data source: FastF1 Live Timing (running order) + Ergast (standings)
    Coverage: 2018-present

    Use for "who was leading the championship on the road at lap 40",
    "how many points did Norris gain on Verstappen in Brazil", "did the
    championship lead change during the race", or the final standings
    after a race.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: "R" (Grand Prix, default) or "S" (sprint)
        after_lap: Running order to use (default: the final lap). On the
                   final lap the official classification and points are
                   used, so penalties and the fastest-lap point are exact.
        include_history: Also return the projected championship leader and
                         margin after every lap (`leadByLap`).

    Returns:
        {
            "round": 21, "session": "R", "lap": 71, "totalLaps": 71,
            "source": "official" | "projected",
            "baseline": {"afterRound": 20, "includesSprint": true},
            "drivers": [
                {"driver": "VER", "fullName": "Max Verstappen",
                 "team": "Red Bull", "pointsBefore": 362.0,
                 "racePosition": 1, "racePoints": 25.0, "projected": 387.0,
                 "positionBefore": 1, "projectedPosition": 1, "change": 0},
                ...
            ],
            "constructors": [
                {"team": "McLaren", "pointsBefore": 566.0, "racePoints": 33.0,
                 "projected": 599.0, "positionBefore": 1,
                 "projectedPosition": 1, "change": 0},
                ...
            ],
            "summary": {"leader": "VER", "leaderBefore": "VER",
                        "marginToSecond": 62.0, "leadChanged": false,
                        "constructorsLeader": "McLaren"},
            "leadByLap": [{"lap": 1, "leader": "VER", "margin": 63.0}, ...]
        }

    Note:
        Baseline standings are the official ones after the previous round;
        for a Grand Prix on a sprint weekend the sprint points of the same
        round are added. Mid-race projections use the race points table
        (25-18-15-12-10-8-6-4-2-1, sprint 8…1 or 3-2-1 in 2021) and, for
        2019-2024 Grands Prix, +1 for the fastest lap so far if that driver
        is in the top 10. Half-points races (e.g. Spa 2021) are not modelled
        mid-race; the final-lap figures use the official points.
    """
    session = session.upper()
    if session not in {"R", "S"}:
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER,
            "Championship projection needs a points-scoring session: 'R' or 'S'.",
        )
    logger.info(
        f"get_championship_projection: {year=}, {event=}, {session=}, {after_lap=}"
    )
    session_obj = await require_session(year, event, session)
    laps, results = session_obj.laps, session_obj.results
    lookup = build_driver_lookup(session_obj)

    try:
        rnd = int(session_obj.event["RoundNumber"])
    except Exception as e:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE, f"Could not determine the round number: {e}"
        ) from e

    total_laps = int(laps["LapNumber"].max())
    lap = total_laps if after_lap is None else max(1, min(int(after_lap), total_laps))

    # ------------------------------------------------------------------
    # Baseline standings (after previous round [+ same-round sprint])
    # ------------------------------------------------------------------
    ergast = Ergast()
    base_drivers: dict[str, float] = {}
    base_teams: dict[str, float] = {}
    team_of: dict[str, str] = {}
    if rnd > 1:
        ds = first_frame(
            await run_ergast(
                ergast.get_driver_standings,
                what=f"driver standings after {year} round {rnd - 1}",
                season=year,
                round=rnd - 1,
            )
        )
        cs = first_frame(
            await run_ergast(
                ergast.get_constructor_standings,
                what=f"constructor standings after {year} round {rnd - 1}",
                season=year,
                round=rnd - 1,
            )
        )
        for _, r in ds.iterrows():
            code = r.get("driverCode")
            if isinstance(code, str) and code:
                base_drivers[code] = float(r["points"])
                names = r.get("constructorNames")
                if isinstance(names, list) and names:
                    team_of[code] = names[-1]  # current team if they switched
        for _, r in cs.iterrows():
            base_teams[r["constructorName"]] = float(r["points"])

    includes_sprint = False
    if session == "R":
        try:
            sp = first_frame(
                await run_ergast(
                    ergast.get_sprint_results,
                    what=f"sprint results for {year} round {rnd}",
                    season=year,
                    round=rnd,
                )
            )
        except FastF1MCPError:
            sp = pd.DataFrame()
        if not sp.empty and "points" in sp.columns:
            includes_sprint = True
            for _, r in sp.iterrows():
                code = r.get("driverCode")
                pts = float(r["points"]) if pd.notna(r.get("points")) else 0.0
                if isinstance(code, str) and code:
                    base_drivers[code] = base_drivers.get(code, 0.0) + pts
                    team = team_of.get(code) or r.get("constructorName")
                    if team:
                        team_of.setdefault(code, team)
                        base_teams[team] = base_teams.get(team, 0.0) + pts

    # Team for drivers without prior standings (debut round / new driver):
    # take the Ergast name used by a team-mate, else FastF1's TeamName.
    fastf1_team = {}
    if results is not None and "Abbreviation" in getattr(results, "columns", []):
        fastf1_team = dict(zip(results["Abbreviation"], results.get("TeamName", "")))
    for code, team in fastf1_team.items():
        if code in team_of:
            continue
        mate = next(
            (
                team_of[c]
                for c, t in fastf1_team.items()
                if t == team and c in team_of and c != code
            ),
            None,
        )
        team_of[code] = mate or team

    # ------------------------------------------------------------------
    # Race points at `lap`
    # ------------------------------------------------------------------
    official = (
        lap == total_laps
        and results is not None
        and "Points" in getattr(results, "columns", [])
        and results["Points"].notna().any()
    )
    table = RACE_POINTS if session == "R" else sprint_points(year)

    def _race_points(at_lap: int) -> tuple[dict[str, float], dict[str, int]]:
        if at_lap == total_laps and official:
            pts = {
                r["Abbreviation"]: float(r["Points"]) if pd.notna(r["Points"]) else 0.0
                for _, r in results.iterrows()
            }
            pos = {
                r["Abbreviation"]: int(r["Position"])
                for _, r in results.iterrows()
                if pd.notna(r.get("Position"))
            }
            return pts, pos
        pos = _positions_at_lap(laps, results, at_lap)
        pts = {d: _points_for(p, table) for d, p in pos.items()}
        if has_fastest_lap_point(year, session):
            fl = _fastest_lap_holder(laps, at_lap)
            if fl and pos.get(fl, 99) <= 10:
                pts[fl] += 1.0
        return pts, pos

    def _standings(race_pts: dict[str, float]) -> tuple[dict, dict]:
        drivers = dict(base_drivers)
        for d, p in race_pts.items():
            drivers[d] = drivers.get(d, 0.0) + p
        teams = dict(base_teams)
        for d, p in race_pts.items():
            t = team_of.get(d)
            if t:
                teams[t] = teams.get(t, 0.0) + p
        return drivers, teams

    def _rank(points: dict[str, float], tiebreak: dict[str, float]) -> dict[str, int]:
        order = sorted(points, key=lambda k: (-points[k], -tiebreak.get(k, 0.0), k))
        return {k: i + 1 for i, k in enumerate(order)}

    race_pts, race_pos = _race_points(lap)
    proj_drivers, proj_teams = _standings(race_pts)
    before_rank = _rank(base_drivers, {})
    after_rank = _rank(proj_drivers, base_drivers)
    team_before_rank = _rank(base_teams, {})
    team_after_rank = _rank(proj_teams, base_teams)

    drivers_out = []
    for d in sorted(proj_drivers, key=lambda k: after_rank[k]):
        info = lookup.get(d, {})
        before = base_drivers.get(d, 0.0)
        drivers_out.append(
            {
                "driver": d,
                "fullName": info.get("fullName", ""),
                "team": team_of.get(d, info.get("teamName", "")),
                "pointsBefore": before,
                "racePosition": race_pos.get(d),
                "racePoints": race_pts.get(d, 0.0),
                "projected": proj_drivers[d],
                "positionBefore": before_rank.get(d),
                "projectedPosition": after_rank[d],
                "change": (before_rank[d] - after_rank[d])
                if d in before_rank
                else None,
            }
        )

    teams_out = []
    for t in sorted(proj_teams, key=lambda k: team_after_rank[k]):
        teams_out.append(
            {
                "team": t,
                "pointsBefore": base_teams.get(t, 0.0),
                "racePoints": proj_teams[t] - base_teams.get(t, 0.0),
                "projected": proj_teams[t],
                "positionBefore": team_before_rank.get(t),
                "projectedPosition": team_after_rank[t],
                "change": (team_before_rank[t] - team_after_rank[t])
                if t in team_before_rank
                else None,
            }
        )

    leader = drivers_out[0]["driver"] if drivers_out else None
    leader_before = min(before_rank, key=before_rank.get) if before_rank else None
    margin = (
        drivers_out[0]["projected"] - drivers_out[1]["projected"]
        if len(drivers_out) > 1
        else None
    )
    response: dict = {
        "round": rnd,
        "session": session,
        "lap": lap,
        "totalLaps": total_laps,
        "source": "official" if lap == total_laps and official else "projected",
        "baseline": {
            "afterRound": rnd - 1,
            "includesSprint": includes_sprint,
        },
        "drivers": drivers_out,
        "constructors": teams_out,
        "summary": {
            "leader": leader,
            "leaderBefore": leader_before,
            "marginToSecond": margin,
            "leadChanged": bool(leader_before) and leader != leader_before,
            "constructorsLeader": teams_out[0]["team"] if teams_out else None,
        },
    }

    if include_history:
        history = []
        for n in range(1, total_laps + 1):
            pts, _ = _race_points(n)
            standings, _ = _standings(pts)
            ranked = sorted(standings.items(), key=lambda kv: (-kv[1], kv[0]))
            history.append(
                {
                    "lap": n,
                    "leader": ranked[0][0],
                    "margin": ranked[0][1] - ranked[1][1] if len(ranked) > 1 else None,
                }
            )
        response["leadByLap"] = history

    return to_jsonable(response)
