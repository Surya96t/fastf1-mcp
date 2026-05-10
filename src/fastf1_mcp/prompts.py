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
2. Call get_fastest_laps({year}, {e}, "R", 5) for top 5 fastest laps
3. Call get_pit_stops({year}, {e}) for pit strategy overview
4. Call get_stint_analysis({year}, {e}) for tire strategy details

Then synthesize into a narrative covering:
- Race winner and podium
- Key overtakes and battles
- Strategy decisions that made the difference
- Any retirements or incidents
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

Provide analysis covering:
- Pole position lap breakdown (which sectors made the difference)
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
3. Call get_race_pace({year}, {e}) for clean air pace comparison

Analyze:
- Which strategy (1-stop vs 2-stop) worked best and why
- Key undercuts/overcuts that changed positions
- Tire degradation patterns by compound
- How safety cars affected strategies
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
