"""MCP prompts — guided multi-step workflows for AI assistants."""

import json

from fastmcp.prompts import Message


def _q(s: str) -> str:
    """JSON-encode a string so quotes/backslashes inside it don't break embedded snippets."""
    return json.dumps(s)


def race_recap(year: int, event: str) -> list[Message]:
    """Generate a comprehensive race summary."""
    e = _q(event)
    return [
        Message(f"""
Create a race recap for the {event} {year} Grand Prix. Follow these steps:

1. Call get_session_results({year}, {e}, "R") to get the final classification
2. Call get_position_changes({year}, {e}, include_laps=False) for lead changes,
   laps led and the biggest gainers/losers from the grid
3. Call get_race_control_messages({year}, {e}) for safety cars, red flags,
   penalties and investigations (use the `summary` block)
4. Call get_fastest_laps({year}, {e}, "R", 5) for top 5 fastest laps
5. Call get_stint_analysis({year}, {e}) for tire strategy details
6. Call get_weather({year}, {e}) if conditions may have mattered
7. Optionally call plot_position_chart({year}, {e}) to show the race unfolding

Then synthesize into a narrative covering:
- Race winner and podium
- How the lead changed hands and the key battles / biggest movers
- Strategy decisions that made the difference (and any SC/VSC that shaped them)
- Retirements, incidents and penalties
- Fastest lap and notable performances

Keep it engaging and informative for F1 fans.
""")
    ]


def qualifying_analysis(year: int, event: str) -> list[Message]:
    """Analyze qualifying session performance."""
    e = _q(event)
    return [
        Message(f"""
Analyze qualifying for the {event} {year} Grand Prix:

1. Call get_qualifying_breakdown({year}, {e}) for Q1/Q2/Q3 results
2. Call get_sector_times({year}, {e}, "Q") for sector analysis
3. Call get_fastest_laps({year}, {e}, "Q", 10) for the top times
4. Call get_corner_analysis({year}, {e}, "Q", <pole sitter>, driver2=<P2>) to
   see which corners decided pole
5. Call get_race_control_messages({year}, {e}, "Q") for deleted laps and
   red flags; get_weather({year}, {e}, "Q") if track conditions changed

Provide analysis covering:
- Pole position lap breakdown (which sectors and corners made the difference)
- Surprise eliminations in Q1/Q2
- Gaps between teammates
- Any notable improvements or struggles
""")
    ]


def driver_comparison(year: int, driver1: str, driver2: str) -> list[Message]:
    """Compare two drivers across a season."""
    return [
        Message(f"""
Compare {driver1} and {driver2} for the {year} season:

1. Call get_driver_standings({year}) for championship positions
2. For each completed race, call get_session_results to compare finishing positions
3. Call get_qualifying_breakdown for head-to-head qualifying comparison

Create a comparison covering:
- Championship standings and points
- Head-to-head in races (wins, podiums)
- Head-to-head in qualifying
- Average race pace when both finished
- Key moments that defined their rivalry
""")
    ]


def strategy_analysis(year: int, event: str) -> list[Message]:
    """Deep dive into race strategy."""
    e = _q(event)
    return [
        Message(f"""
Analyze race strategy for the {event} {year} Grand Prix:

1. Call get_stint_analysis({year}, {e}) for all driver stints
2. Call get_pit_stops({year}, {e}) for pit stop timing and duration
3. Call get_tyre_degradation({year}, {e}) for degradation per compound and
   the compound crossover points
4. Call get_race_control_messages({year}, {e}) — `summary.neutralisations`
   lists every SC/VSC/red flag with its laps
5. Call get_position_changes({year}, {e}, include_laps=False) to see who
   gained or lost places through the pit cycles
6. Call get_race_pace({year}, {e}) for clean air pace comparison
7. Optionally call plot_tyre_strategy({year}, {e}) to show the strategies

Analyze:
- Which strategy (1-stop vs 2-stop) worked best and why
- Key undercuts/overcuts that changed positions
- Tire degradation by compound (cite the deg rates and crossover laps)
- How safety cars / VSCs affected strategies (who pitted under them)
- Which teams made the best strategic calls
""")
    ]


def weekend_preview(year: int, event: str) -> list[Message]:
    """Generate a race weekend preview."""
    return [
        Message(f"""
Create a preview for the {event} {year} Grand Prix:

1. Call get_circuit_info() to get circuit details
2. Call get_race_results_historical for recent years at this circuit
3. Call get_driver_standings({year}) for current championship situation

Preview should cover:
- Circuit characteristics and key corners
- Recent winners and typical race patterns
- Championship implications
- Key storylines to watch
- Predicted tire strategies
""")
    ]


def replay_commentary(rounds: int = 6) -> list[Message]:
    """Live race-engineer commentary on a replay running in f1-race-replay."""
    return [
        Message(f"""
Act as a race engineer on the pit wall, commentating on the race that is
currently playing in f1-race-replay.

1. Call get_replay_state() to see the current lap, leader, track status and
   leaderboard. If it reports no stream, tell the user to start (or un-pause)
   a replay and stop.
2. Then call watch_replay(duration_sec=10) {rounds} times. After each call,
   give a short radio-style update (2-4 sentences): overtakes, pit stops,
   safety cars / flags, race-control decisions, and anything notable about
   tyres or gaps from the `state` leaderboard.
3. Don't repeat unchanged information; focus on what changed in each window.
   If a call shows a safety car or pit stop, briefly explain the strategic
   impact (e.g. a cheap pit stop under SC).
""")
    ]
