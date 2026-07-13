"""
Vega-Lite v5 chart tools.

Each tool returns a self-contained Vega-Lite spec. Data is always inlined
under ``data.values`` — no external fetches at render time.

The public response shape is uniform across all chart tools:

    {
        "vegaLiteSpec": { ... full v5 spec ... },
        "html": "<self-contained HTML page with spec already embedded>",
        "title": "<auto or user-supplied>",
        "rowCount": <number of inlined data rows>,
        "renderingInstructions": "<short instruction to paste 'html' verbatim>",
    }

The ``html`` field is a complete, valid HTML page built server-side — the
calling model just pastes it into an HTML artifact or code block.  No JSON
manipulation is required.  ``renderingInstructions`` explains this.
"""

import logging
import tempfile
import webbrowser
from typing import Any

import pandas as pd

from ..utils.converters import laps_to_json
from ..utils.session_loader import tool_handler
from ..utils.vega_lite import (
    RENDERING_INSTRUCTIONS,
    base_spec,
    compound_color_scale,
    driver_color_scale,
    spec_to_html,
    spec_to_png_b64,
    team_color_scale,
)
from .session import (
    _compute_lap_times,
    _compute_positions,
    _compute_race_pace,
    _compute_stints,
)
from .telemetry import _compute_telemetry_comparison

logger = logging.getLogger(__name__)


def _open_chart_in_browser(html: str) -> str:
    """Write *html* to a temp file and open it in the user's default browser.

    Returns the path so it can be included in the tool response. Uses
    ``tempfile.mkstemp`` (atomic create, no delete-on-close race) and
    ``webbrowser.open`` (cross-platform: ``open`` on macOS, ``xdg-open`` on
    Linux, ``start`` on Windows).
    """
    import os

    fd, path = tempfile.mkstemp(suffix=".html", prefix="fastf1_chart_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        os.close(fd)
        raise
    webbrowser.open(f"file://{path}")
    return path


def _filter_clean_laps(laps_df: Any, *, include_outliers: bool = False) -> Any:
    """Drop pit + SC/VSC laps from a Laps DataFrame for chart purposes.

    Mirrors ``get_race_pace``'s defaults so chart visuals and pace metrics
    agree on what counts as a "racing lap". With ``include_outliers=True``,
    returns the DataFrame unchanged. Deleted-lap filtering is already
    handled upstream by ``_compute_lap_times``.
    """
    if include_outliers:
        return laps_df
    out = laps_df
    if hasattr(out, "pick_wo_box"):
        out = out.pick_wo_box()
    if hasattr(out, "pick_track_status"):
        out = out.pick_track_status("1", how="equals")
    return out


def _finishing_order(session_obj: Any) -> list[str]:
    """Return driver codes from ``session.results`` in finishing-position order.

    Used to sort the y-axis of grid-wide charts. Empty list if results are
    missing — callers fall back to data-appearance order.
    """
    results = getattr(session_obj, "results", None)
    if results is None or not hasattr(results, "iterrows"):
        return []
    order: list[str] = []
    for _, row in results.iterrows():
        code = row.get("Abbreviation") if hasattr(row, "get") else None
        if not code:
            code = row.get("Driver") if hasattr(row, "get") else None
        if code:
            order.append(code)
    return order


@tool_handler
async def get_pace_delta_chart(
    year: int,
    event: str | int,
    exclude_first_laps: int = 2,
    exclude_sc_laps: bool = True,
    exclude_pit_laps: bool = True,
    title: str | None = None,
) -> dict:
    """
    Build a horizontal-bar Vega-Lite chart of race-pace delta to the
    fastest driver.

    Use this when the user asks for a chart, plot, graph, or visualisation
    of race pace, pace comparison, or how far each driver was off the pace.
    The returned ``vegaLiteSpec`` can be handed straight to a Claude Artifact.

    Data source: FastF1 Live Timing (race session)
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name (e.g., "Monaco") or round number
        exclude_first_laps: Opening laps to exclude (default 2)
        exclude_sc_laps: Exclude SC/VSC laps (default True)
        exclude_pit_laps: Exclude in-laps and out-laps (default True)
        title: Optional title override. Defaults to
               ``"{year} {event} — Race pace delta (fastest = 0s)"``.

    Returns:
        {
            "vegaLiteSpec": { ... Vega-Lite v5 spec ... },
            "title": "<resolved title>",
            "rowCount": <number of drivers in the chart>,
            "renderingInstructions": "<HTML wrapper template — follow it
              verbatim to surface the chart in any MCP client>",
        }

    Rendering:
        Read ``renderingInstructions`` in the response and follow it. Build
        an HTML artifact (or HTML code block) using the provided vega-embed
        template, with ``vegaLiteSpec`` pasted inline. Do NOT print the
        spec as visible text or invent custom tags — MCP clients will not
        auto-render Vega-Lite from tool output.

    Encoding:
        - y: driver (sorted by deltaToFastestSec ascending — fastest on top)
        - x: deltaToFastestSec in seconds
        - color: teamName, using fastf1's team colour palette

    Note:
        Filter defaults mirror ``get_race_pace`` so the chart and the raw
        pace numbers agree on what counts as a racing lap.
    """
    logger.info(
        f"get_pace_delta_chart: {year=}, {event=}, {exclude_first_laps=}, "
        f"{exclude_sc_laps=}, {exclude_pit_laps=}"
    )

    session_obj, pace_data, _filters = await _compute_race_pace(
        year,
        event,
        exclude_first_laps=exclude_first_laps,
        exclude_sc_laps=exclude_sc_laps,
        exclude_pit_laps=exclude_pit_laps,
    )

    resolved_title = (
        title
        if title is not None
        else f"{year} {event} — Race pace delta (fastest = 0s)"
    )

    rows = [
        {
            "driver": item["driver"],
            "fullName": item.get("fullName", ""),
            "teamName": item.get("teamName", ""),
            "deltaToFastestSec": item["deltaToFastestSec"],
            "avgLapTime": item.get("avgLapTime"),
            "lapCount": item.get("lapCount"),
        }
        for item in pace_data
    ]

    # Preserve appearance order from pace_data (already fastest-first) so
    # the legend mirrors the bar order.
    seen: list[str] = []
    for row in rows:
        t = row["teamName"]
        if t and t not in seen:
            seen.append(t)
    color_scale = team_color_scale(seen, session_obj)

    spec = base_spec(resolved_title, mark={"type": "bar", "tooltip": True})
    spec["data"] = {"values": rows}
    spec["encoding"] = {
        "y": {
            "field": "driver",
            "type": "nominal",
            "sort": {"field": "deltaToFastestSec", "order": "ascending"},
            "title": "Driver",
        },
        "x": {
            "field": "deltaToFastestSec",
            "type": "quantitative",
            "title": "Delta to fastest (s)",
        },
        "color": {
            "field": "teamName",
            "type": "nominal",
            "scale": color_scale,
            "title": "Team",
        },
        "tooltip": [
            {"field": "driver", "title": "Driver"},
            {"field": "fullName", "title": "Name"},
            {"field": "teamName", "title": "Team"},
            {"field": "deltaToFastestSec", "title": "Delta (s)"},
            {"field": "avgLapTime", "title": "Avg lap"},
            {"field": "lapCount", "title": "Laps"},
        ],
    }

    html = spec_to_html(spec, resolved_title)
    return {
        "vegaLiteSpec": spec,
        "html": html,
        "png": spec_to_png_b64(spec),
        "htmlPath": _open_chart_in_browser(html),
        "title": resolved_title,
        "rowCount": len(rows),
        "renderingInstructions": RENDERING_INSTRUCTIONS,
    }


@tool_handler
async def get_tyre_strategy_chart(
    year: int,
    event: str | int,
    driver: str | None = None,
    title: str | None = None,
) -> dict:
    """
    Build a horizontal-Gantt Vega-Lite chart of tyre strategy across a race.

    Use this when the user asks for a chart, plot, graph, or visualisation
    of tyre strategy, stints, compound usage, or pit strategy across the
    field. The returned ``vegaLiteSpec`` can be handed straight to a Claude
    Artifact.

    Data source: FastF1 Live Timing (race session)
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name (e.g., "Monaco") or round number
        driver: Optional driver code. ``None`` (default) shows all drivers —
                the canonical broadcast strategy view. Pass a driver to
                isolate a single driver's stints.
        title: Optional title override. Defaults to
               ``"{year} {event} — Tyre strategies"``.

    Returns:
        {
            "vegaLiteSpec": { ... Vega-Lite v5 spec ... },
            "title": "<resolved title>",
            "rowCount": <number of stints in the chart>,
            "renderingInstructions": "<HTML wrapper template — follow it
              verbatim to surface the chart in any MCP client>",
        }

    Rendering:
        Read ``renderingInstructions`` in the response and follow it. Build
        an HTML artifact (or HTML code block) using the provided vega-embed
        template, with ``vegaLiteSpec`` pasted inline. Do NOT print the
        spec as visible text or invent custom tags — MCP clients will not
        auto-render Vega-Lite from tool output.

    Encoding:
        - y: driver, sorted by finishing position (P1 at top)
        - x/x2: startLap → endLap (Gantt bars)
        - color: compound, using the F1 broadcast palette
    """
    logger.info(f"get_tyre_strategy_chart: {year=}, {event=}, {driver=}")

    session_obj, stints = await _compute_stints(year, event, driver=driver)

    resolved_title = title if title is not None else f"{year} {event} — Tyre strategies"

    rows = [
        {
            "driver": s["driver"],
            "fullName": s.get("fullName", ""),
            "teamName": s.get("teamName", ""),
            "stintNumber": s["stintNumber"],
            "compound": s.get("compound", "UNKNOWN"),
            "startLap": s["startLap"],
            "endLap": s["endLap"],
            "lapCount": s["lapCount"],
        }
        for s in stints
    ]

    # Sort y-axis by finishing position. Filter to drivers actually in the
    # data so Vega doesn't reserve empty rows. Fall back to data-appearance
    # order when session.results is missing.
    drivers_in_data: list[str] = []
    for row in rows:
        if row["driver"] not in drivers_in_data:
            drivers_in_data.append(row["driver"])

    finishing = _finishing_order(session_obj)
    if finishing:
        y_sort = [d for d in finishing if d in drivers_in_data]
        # Append any drivers that were in the data but not in results (rare
        # — e.g. DNS not classified) so they still render.
        for d in drivers_in_data:
            if d not in y_sort:
                y_sort.append(d)
    else:
        y_sort = drivers_in_data

    # Scope the compound palette to compounds present in the data.
    compounds_in_data: list[str] = []
    for row in rows:
        c = row["compound"] or "UNKNOWN"
        if c not in compounds_in_data:
            compounds_in_data.append(c)
    color_scale = compound_color_scale(compounds_in_data) if compounds_in_data else None

    spec = base_spec(resolved_title, mark={"type": "bar", "tooltip": True})
    spec["data"] = {"values": rows}
    color_enc: dict = {
        "field": "compound",
        "type": "nominal",
        "title": "Tyre",
    }
    if color_scale is not None:
        color_enc["scale"] = color_scale
    spec["encoding"] = {
        "y": {
            "field": "driver",
            "type": "nominal",
            "sort": y_sort,
            "title": "Driver",
        },
        "x": {
            "field": "startLap",
            "type": "quantitative",
            "title": "Lap",
        },
        "x2": {"field": "endLap"},
        "color": color_enc,
        "tooltip": [
            {"field": "driver", "title": "Driver"},
            {"field": "fullName", "title": "Name"},
            {"field": "teamName", "title": "Team"},
            {"field": "stintNumber", "title": "Stint"},
            {"field": "compound", "title": "Compound"},
            {"field": "startLap", "title": "Start lap"},
            {"field": "endLap", "title": "End lap"},
            {"field": "lapCount", "title": "Laps"},
        ],
    }

    html = spec_to_html(spec, resolved_title)
    return {
        "vegaLiteSpec": spec,
        "html": html,
        "png": spec_to_png_b64(spec),
        "htmlPath": _open_chart_in_browser(html),
        "title": resolved_title,
        "rowCount": len(rows),
        "renderingInstructions": RENDERING_INSTRUCTIONS,
    }


@tool_handler
async def get_position_chart(
    year: int,
    event: str | int,
    drivers: list[str] | None = None,
    title: str | None = None,
) -> dict:
    """
    Build a multi-line Vega-Lite chart of race positions by lap.

    Use this when the user asks for a chart, plot, graph, or visualisation
    of race positions, position changes across the race, or how drivers
    moved through the field. The returned ``vegaLiteSpec`` can be handed
    straight to a Claude Artifact.

    Data source: FastF1 Live Timing (race session)
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name (e.g., "Monaco") or round number
        drivers: Optional list of driver codes (e.g. ``["VER", "LEC"]``) to
                 restrict the chart. ``None`` (default) shows every classified
                 driver from ``session.results``.
        title: Optional title override. Defaults to
               ``"{year} {event} — Race positions"``.

    Returns:
        {
            "vegaLiteSpec": { ... Vega-Lite v5 spec ... },
            "title": "<resolved title>",
            "rowCount": <number of per-lap rows in the chart>,
            "renderingInstructions": "<HTML wrapper template — follow it
              verbatim to surface the chart in any MCP client>",
        }

    Rendering:
        Read ``renderingInstructions`` in the response and follow it. Build
        an HTML artifact (or HTML code block) using the provided vega-embed
        template, with ``vegaLiteSpec`` pasted inline. Do NOT print the
        spec as visible text or invent custom tags — MCP clients will not
        auto-render Vega-Lite from tool output.

    Encoding:
        - x: lapNumber
        - y: position (scale reversed so P1 sits at the top, integer ticks)
        - color: driver, using fastf1's driver palette
    """
    logger.info(f"get_position_chart: {year=}, {event=}, {drivers=}")

    session_obj, rows = await _compute_positions(year, event, drivers=drivers)

    resolved_title = title if title is not None else f"{year} {event} — Race positions"

    drivers_in_data: list[str] = []
    for row in rows:
        if row["driver"] not in drivers_in_data:
            drivers_in_data.append(row["driver"])
    color_scale = driver_color_scale(drivers_in_data, session_obj)

    spec = base_spec(
        resolved_title, mark={"type": "line", "point": False, "tooltip": True}
    )
    spec["data"] = {"values": rows}
    spec["encoding"] = {
        "x": {
            "field": "lapNumber",
            "type": "quantitative",
            "title": "Lap",
        },
        "y": {
            "field": "position",
            "type": "quantitative",
            "title": "Position",
            "scale": {"reverse": True},
            "axis": {"tickMinStep": 1, "format": "d"},
        },
        "color": {
            "field": "driver",
            "type": "nominal",
            "scale": color_scale,
            "title": "Driver",
        },
        "tooltip": [
            {"field": "driver", "title": "Driver"},
            {"field": "fullName", "title": "Name"},
            {"field": "lapNumber", "title": "Lap"},
            {"field": "position", "title": "Position"},
        ],
    }

    html = spec_to_html(spec, resolved_title)
    return {
        "vegaLiteSpec": spec,
        "html": html,
        "png": spec_to_png_b64(spec),
        "htmlPath": _open_chart_in_browser(html),
        "title": resolved_title,
        "rowCount": len(rows),
        "renderingInstructions": RENDERING_INSTRUCTIONS,
    }


def _lap_time_seconds(lap_time_str: str | None) -> float | None:
    """Convert FastF1's lap-time string to seconds, or None on failure."""
    if not lap_time_str:
        return None
    try:
        return round(pd.to_timedelta(lap_time_str).total_seconds(), 3)
    except (ValueError, TypeError):
        return None


def _format_lap_time(lap_time_str: str | None) -> str | None:
    """Format a pandas Timedelta string as ``"M:SS.mmm"`` (e.g. ``"1:40.683"``).

    pandas serialises Timedeltas as ``"0 days 00:01:40.683000"`` which is
    awkward in chart tooltips.  Returns the original string on parse failure.
    """
    if not lap_time_str:
        return None
    try:
        total = pd.to_timedelta(lap_time_str).total_seconds()
        minutes = int(total // 60)
        secs = total % 60
        return f"{minutes}:{secs:06.3f}"
    except (ValueError, TypeError):
        return lap_time_str


@tool_handler
async def get_lap_time_chart(
    year: int,
    event: str | int,
    session: str,
    driver: str,
    include_outliers: bool = False,
    title: str | None = None,
) -> dict:
    """
    Build a line+point Vega-Lite chart of a driver's lap times across a
    session, coloured by tyre compound.

    Use this when the user asks for a chart, plot, graph, or visualisation
    of lap times, pace over a stint, or how one driver's lap times evolved
    across a session. The returned ``vegaLiteSpec`` can be handed straight
    to a Claude Artifact.

    Data source: FastF1 Live Timing
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name (e.g., "Monaco") or round number
        session: Session type — R, Q, S, SQ, FP1, FP2, FP3
        driver: Driver code (e.g., "VER") or number (e.g., "1")
        include_outliers: When False (default) drops deleted, pit-in/out,
                          and SC/VSC laps so the trace shows clean racing
                          pace only. Set True to keep them.
        title: Optional title override. Defaults to
               ``"{year} {event} {session} — {driverCode} lap times"``.

    Returns:
        {
            "vegaLiteSpec": { ... Vega-Lite v5 spec ... },
            "title": "<resolved title>",
            "rowCount": <number of laps in the chart>,
            "renderingInstructions": "<HTML wrapper template — follow it
              verbatim to surface the chart in any MCP client>",
        }

    Rendering:
        Read ``renderingInstructions`` in the response and follow it. Build
        an HTML artifact (or HTML code block) using the provided vega-embed
        template, with ``vegaLiteSpec`` pasted inline. Do NOT print the
        spec as visible text or invent custom tags — MCP clients will not
        auto-render Vega-Lite from tool output.

    Encoding:
        - x: lapNumber
        - y: lapTimeSec (lap time in seconds; scale.zero=False keeps the
             range tight around actual pace)
        - color: compound, using the F1 broadcast palette
        - tooltip: lap, formatted time, compound, tyre life
    """
    logger.info(
        f"get_lap_time_chart: {year=}, {event=}, {session=}, {driver=}, "
        f"{include_outliers=}"
    )

    _, laps_df, info = await _compute_lap_times(
        year, event, session, driver, include_deleted=False
    )
    laps_df = _filter_clean_laps(laps_df, include_outliers=include_outliers)
    laps_json = laps_to_json(laps_df)

    driver_code = info.get("driverCode") or driver
    resolved_title = (
        title
        if title is not None
        else f"{year} {event} {session} — {driver_code} lap times"
    )

    rows = []
    for lap in laps_json:
        raw_time = lap.get("lapTime")
        rows.append(
            {
                "lapNumber": lap.get("lapNumber"),
                "lapTimeSec": _lap_time_seconds(raw_time),
                "lapTimeStr": _format_lap_time(raw_time),
                "compound": lap.get("compound") or "UNKNOWN",
                "tyreLife": lap.get("tyreLife"),
            }
        )

    # Scope the compound palette to compounds present in the data so the
    # legend doesn't list unused tyres.
    compounds_in_data: list[str] = []
    for row in rows:
        c = row["compound"]
        if c not in compounds_in_data:
            compounds_in_data.append(c)
    color_scale = compound_color_scale(compounds_in_data) if compounds_in_data else None

    spec = base_spec(
        resolved_title, mark={"type": "line", "point": True, "tooltip": True}
    )
    spec["data"] = {"values": rows}
    color_enc: dict = {
        "field": "compound",
        "type": "nominal",
        "title": "Compound",
    }
    if color_scale is not None:
        color_enc["scale"] = color_scale
    spec["encoding"] = {
        "x": {
            "field": "lapNumber",
            "type": "quantitative",
            "title": "Lap",
        },
        "y": {
            "field": "lapTimeSec",
            "type": "quantitative",
            "title": "Lap time",
            "scale": {"zero": False},
            "axis": {
                "labelExpr": (
                    "floor(datum.value / 60) + ':'"
                    " + (datum.value % 60 < 10 ? '0' : '')"
                    " + format(datum.value % 60, '.3f')"
                ),
            },
        },
        "color": color_enc,
        "tooltip": [
            {"field": "lapNumber", "title": "Lap"},
            {"field": "lapTimeStr", "title": "Time"},
            {"field": "compound", "title": "Compound"},
            {"field": "tyreLife", "title": "Tyre life"},
        ],
    }

    html = spec_to_html(spec, resolved_title)
    return {
        "vegaLiteSpec": spec,
        "html": html,
        "png": spec_to_png_b64(spec),
        "htmlPath": _open_chart_in_browser(html),
        "title": resolved_title,
        "rowCount": len(rows),
        "renderingInstructions": RENDERING_INSTRUCTIONS,
    }


@tool_handler
async def get_speed_trace_chart(
    year: int,
    event: str | int,
    session: str,
    driver1: str,
    driver2: str,
    lap: int | str = "fastest",
    title: str | None = None,
) -> dict:
    """
    Build a two-line Vega-Lite speed-trace chart comparing two drivers
    across the lap distance.

    Use this when the user asks for a chart, plot, graph, or visualisation
    of a speed trace, two drivers' speed compared along the lap, or where
    one driver gains/loses speed against another. The returned
    ``vegaLiteSpec`` can be handed straight to a Claude Artifact.

    Data source: FastF1 Live Timing (telemetry-loaded session)
    Coverage: 2018-present

    Args:
        year: Season year (2018+)
        event: Race name (e.g., "Monaco") or round number
        session: Session type — R, Q, S, SQ, FP1, FP2, FP3
        driver1: First driver code (e.g., "VER")
        driver2: Second driver code (e.g., "LEC")
        lap: Lap number or "fastest" — applied independently to each driver
        title: Optional title override. Defaults to
               ``"{year} {event} {session} — {driver1} vs {driver2} (lap {lap})"``.

    Returns:
        {
            "vegaLiteSpec": { ... Vega-Lite v5 spec ... },
            "title": "<resolved title>",
            "rowCount": <2 x sample_size rows in long format>,
            "renderingInstructions": "<HTML wrapper template — follow it
              verbatim to surface the chart in any MCP client>",
        }

    Rendering:
        Read ``renderingInstructions`` in the response and follow it. Build
        an HTML artifact (or HTML code block) using the provided vega-embed
        template, with ``vegaLiteSpec`` pasted inline. Do NOT print the
        spec as visible text or invent custom tags — MCP clients will not
        auto-render Vega-Lite from tool output.

    Encoding:
        - x: distance (m)
        - y: speed (km/h, scale.zero=False keeps the range tight)
        - color: driver, using fastf1's driver palette
    """
    logger.info(
        f"get_speed_trace_chart: {year=}, {event=}, {session=}, "
        f"{driver1=}, {driver2=}, {lap=}"
    )

    session_obj, d1_code, d2_code, comparison = await _compute_telemetry_comparison(
        year, event, session, driver1, driver2, lap=lap
    )

    resolved_title = (
        title
        if title is not None
        else f"{year} {event} {session} — {d1_code} vs {d2_code} (lap {lap})"
    )

    rows: list[dict] = []
    for r in comparison:
        rows.append(
            {"distance": r["distance"], "driver": d1_code, "speed": r["speed1"]}
        )
        rows.append(
            {"distance": r["distance"], "driver": d2_code, "speed": r["speed2"]}
        )

    color_scale = driver_color_scale([d1_code, d2_code], session_obj)

    spec = base_spec(resolved_title, mark={"type": "line", "tooltip": True})
    spec["data"] = {"values": rows}
    spec["encoding"] = {
        "x": {
            "field": "distance",
            "type": "quantitative",
            "title": "Distance (m)",
        },
        "y": {
            "field": "speed",
            "type": "quantitative",
            "title": "Speed (km/h)",
            "scale": {"zero": False},
        },
        "color": {
            "field": "driver",
            "type": "nominal",
            "scale": color_scale,
            "title": "Driver",
        },
        "tooltip": [
            {"field": "driver", "title": "Driver"},
            {"field": "distance", "title": "Distance (m)"},
            {"field": "speed", "title": "Speed (km/h)"},
        ],
    }

    html = spec_to_html(spec, resolved_title)
    return {
        "vegaLiteSpec": spec,
        "html": html,
        "png": spec_to_png_b64(spec),
        "htmlPath": _open_chart_in_browser(html),
        "title": resolved_title,
        "rowCount": len(rows),
        "renderingInstructions": RENDERING_INSTRUCTIONS,
    }
