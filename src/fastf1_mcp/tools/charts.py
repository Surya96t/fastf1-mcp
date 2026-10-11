"""
Chart tools — render PNG images an MCP client can show inline.

Rendering uses matplotlib's object-oriented `Figure` API (no pyplot global
state, no GUI backend) inside an executor, so charts are thread-safe and
never block the event loop.

Colour system (validated with the dataviz palette checks):
  * Series colours come from a fixed categorical order and follow the
    *entity* (the driver's position in the request), never rank.
  * Tyre compounds keep the Pirelli convention (red/amber/grey/green/blue),
    stepped so they clear the lightness and CVD-separation checks on the
    light chart surface. HARD is grey by convention and amber/grey sit below
    3:1 contrast, so compound identity is never colour-alone: every bar
    carries its S/M/H/I/W letter and the chart has a legend.
"""

import asyncio
import io
import logging
from functools import partial

import numpy as np
import pandas as pd
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.types import Image
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, MaxNLocator
from mcp.types import TextContent

from ..utils.converters import build_driver_lookup
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.exports import _display_path, resolve_export_path
from ..utils.session_loader import require_session, tool_handler
from ..utils.timing import to_seconds
from .corners import corner_label, load_corners
from .telemetry import _cumulative_time_at_distances, _get_driver_lap

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Palette & chrome
# ---------------------------------------------------------------------------

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BACKGROUND_LINE = "#d6d5ce"

# Fixed categorical order. Line charts may use all eight; scatter-type charts
# cap at three (the first three slots are the ones that validate all-pairs).
SERIES = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#6250d6",  # violet
    "#e34948",  # red
]
MAX_LINE_SERIES = len(SERIES)
MAX_SCATTER_SERIES = 3

COMPOUNDS = {
    "SOFT": ("#da291c", "S"),
    "MEDIUM": ("#eda100", "M"),
    "HARD": ("#a8a69c", "H"),
    "INTERMEDIATE": ("#008300", "I"),
    "WET": ("#2a78d6", "W"),
}
UNKNOWN_COMPOUND = ("#e1e0d9", "?")
COMPOUND_MARKERS = {
    "SOFT": "o",
    "MEDIUM": "s",
    "HARD": "^",
    "INTERMEDIATE": "D",
    "WET": "v",
}


def _figure(
    nrows: int = 1, height: float = 5.5, height_ratios=None
) -> tuple[Figure, list]:
    fig = Figure(figsize=(11, height), dpi=120, facecolor=SURFACE)
    axes = fig.subplots(
        nrows,
        1,
        sharex=True,
        gridspec_kw={"height_ratios": height_ratios} if height_ratios else None,
        squeeze=False,
    )[:, 0]
    for ax in axes:
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(AXIS)
        ax.tick_params(colors=INK_2, labelsize=9, length=0)
        ax.xaxis.label.set_color(INK_2)
        ax.yaxis.label.set_color(INK_2)
    return fig, list(axes)


# Fixed header layout in inches so short and tall charts get the same spacing:
# title, subtitle, then a band for the legend that sits above the axes.
_TITLE_IN = 0.15
_SUBTITLE_IN = 0.45
_HEADER_IN = 1.05
_FOOTER_IN = 0.55


def _title(fig: Figure, title: str, subtitle: str) -> None:
    h = fig.get_figheight()
    fig.text(
        0.01, 1 - _TITLE_IN / h, title, fontsize=14, color=INK, weight="bold", va="top"
    )
    fig.text(0.01, 1 - _SUBTITLE_IN / h, subtitle, fontsize=10, color=INK_2, va="top")


def _png(fig: Figure) -> bytes:
    h = fig.get_figheight()
    fig.subplots_adjust(
        left=0.07,
        right=0.93,
        top=1 - _HEADER_IN / h,
        bottom=_FOOTER_IN / h,
        hspace=0.18,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    return buf.getvalue()


def _legend(ax, handles, ncol: int | None = None) -> None:
    leg = ax.legend(
        handles=handles,
        loc="lower left",
        bbox_to_anchor=(0, 1.01),
        ncol=ncol or len(handles),
        frameon=False,
        fontsize=9,
        handlelength=1.6,
        borderaxespad=0,
    )
    for text in leg.get_texts():
        text.set_color(INK_2)


def _fmt_laptime(sec: float, _pos=None) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"


async def _render(builder, *args) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(builder, *args))


def _result(
    png: bytes,
    caption: str,
    meta: dict,
    save_path: bool | str,
    tool: str,
    parts: list,
) -> ToolResult:
    if save_path:
        path = resolve_export_path(save_path, tool=tool, parts=parts, extension="png")
        path.write_bytes(png)
        meta["savedTo"] = _display_path(path)
        caption += f"\nSaved to {meta['savedTo']}"
    return ToolResult(
        content=[
            Image(data=png, format="png").to_image_content(),
            TextContent(type="text", text=caption),
        ],
        structured_content=meta,
    )


def _finishing_order(session_obj) -> list[str]:
    results = getattr(session_obj, "results", None)
    if results is None or not hasattr(results, "columns"):
        return []
    df = results
    if "Position" in df.columns:
        df = df.sort_values("Position", na_position="last")
    return [c for c in df.get("Abbreviation", pd.Series(dtype=str)).tolist() if c]


def _resolve_codes(lookup: dict, drivers) -> list[str]:
    if isinstance(drivers, str):
        drivers = [d for d in drivers.replace(" ", ",").split(",") if d]
    codes = []
    for d in drivers or []:
        code = lookup.get(str(d), {}).get("driverCode") or str(d).upper()
        if code not in codes:
            codes.append(code)
    return codes


# ---------------------------------------------------------------------------
# Speed trace
# ---------------------------------------------------------------------------


def _draw_speed_trace(traces, delta, corners, title, subtitle) -> bytes:
    two = len(traces) == 2
    fig, axes = _figure(
        nrows=2 if two else 1,
        height=6.5 if two else 5,
        height_ratios=[3, 1.2] if two else None,
    )
    ax = axes[0]
    handles = []
    for i, (label, d, v) in enumerate(traces):
        ax.plot(d, v, color=SERIES[i], linewidth=1.6, solid_capstyle="round")
        handles.append(Line2D([], [], color=SERIES[i], linewidth=2, label=label))
    ax.set_ylabel("Speed (km/h)")
    _legend(ax, handles)

    if corners:
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi + (hi - lo) * 0.08)  # headroom for the labels
        for lbl, dist in corners:
            for a in axes:
                a.axvline(dist, color=GRID, linewidth=0.8, zorder=0)
            ax.text(
                dist,
                0.985,
                lbl,
                transform=ax.get_xaxis_transform(),
                fontsize=7,
                color=MUTED,
                ha="center",
                va="top",
                backgroundcolor=SURFACE,
            )

    if two:
        dax = axes[1]
        d, dt = delta
        dax.axhline(0, color=AXIS, linewidth=1)
        dax.plot(d, dt, color=INK_2, linewidth=1.4)
        dax.set_ylabel("Gap (s)")
        a, b = traces[0][0].split()[0], traces[1][0].split()[0]
        dax.set_title(
            f"Gap {a} − {b} · below 0: {a} ahead · above 0: {b} ahead",
            loc="left",
            fontsize=8.5,
            color=INK_2,
            pad=4,
        )
    axes[-1].set_xlabel("Distance (m)")
    _title(fig, title, subtitle)
    return _png(fig)


@tool_handler
async def plot_speed_trace(
    year: int,
    event: str | int,
    session: str,
    driver: str,
    driver2: str | None = None,
    lap: int | str = "fastest",
    show_corners: bool = True,
    save_path: bool | str = False,
) -> ToolResult:
    """
    Plot a speed-vs-distance trace for one driver's lap, or overlay two
    drivers with a cumulative time-gap panel underneath. Returns a PNG image.

    Use when the user asks to "show", "plot", "chart" or "visualise"
    telemetry, or to see where one driver is faster than another.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (R, Q, S, SQ, FP1, FP2, FP3)
        driver: Driver code (e.g. "VER")
        driver2: Optional second driver to overlay
        lap: Lap number or "fastest" (default) — applied to each driver
        show_corners: Mark numbered corners (default True; skipped silently
                      if circuit data is unavailable)
        save_path: If True, also write the PNG to the export directory
                   (default `./fastf1-exports/`); a string is a directory or
                   a full `.png` path.

    Returns:
        An image plus a one-line caption. Structured content carries
        the lap numbers/times and `savedTo` when saved.
    """
    logger.info(
        f"plot_speed_trace: {year=}, {event=}, {session=}, {driver=}, {driver2=}"
    )
    session_obj = await require_session(year, event, session, with_telemetry=True)
    lookup = build_driver_lookup(session_obj)

    traces, tels, meta_laps = [], [], []
    for code in [driver] + ([driver2] if driver2 else []):
        lap_row = _get_driver_lap(session_obj, code, lap)
        if lap_row is None:
            raise FastF1MCPError(
                ErrorCode.DRIVER_NOT_FOUND,
                f"No lap {lap!r} found for driver '{code}'",
            )
        try:
            tel = lap_row.get_telemetry()
            if "Distance" not in tel.columns:
                tel = tel.add_distance()
        except Exception as e:
            raise FastF1MCPError(
                ErrorCode.DATA_UNAVAILABLE, f"Telemetry unavailable for {code}: {e}"
            ) from e
        tel = tel.dropna(subset=["Distance", "Speed"]).sort_values("Distance")
        name = lookup.get(code, {}).get("driverCode") or code
        lap_time = lap_row.get("LapTime")
        lap_no = lap_row.get("LapNumber")
        lt = _fmt_laptime(to_seconds(lap_time)) if pd.notna(lap_time) else "n/a"
        traces.append(
            (
                f"{name}  L{int(lap_no) if pd.notna(lap_no) else '?'}  {lt}",
                tel["Distance"].to_numpy(dtype=float),
                tel["Speed"].to_numpy(dtype=float),
            )
        )
        tels.append(tel)
        meta_laps.append(
            {
                "driver": name,
                "lapNumber": int(lap_no) if pd.notna(lap_no) else None,
                "lapTime": str(lap_time) if pd.notna(lap_time) else None,
            }
        )

    delta = None
    caption = f"Speed trace — {meta_laps[0]['driver']}"
    if len(tels) == 2:
        grid = np.linspace(0, float(tels[0]["Distance"].max()), 600)
        gap = _cumulative_time_at_distances(tels[0], grid) - (
            _cumulative_time_at_distances(tels[1], grid)
        )
        delta = (grid, gap)
        caption = (
            f"Speed trace — {meta_laps[0]['driver']} vs {meta_laps[1]['driver']}; "
            f"gap at the line {gap[-1]:+.3f}s (negative = "
            f"{meta_laps[0]['driver']} ahead)"
        )

    corners = []
    if show_corners:
        try:
            df = await load_corners(session_obj)
            corners = [
                (corner_label(r), float(r["Distance"])) for _, r in df.iterrows()
            ]
        except FastF1MCPError as e:
            logger.info(f"Skipping corner markers: {e.message}")

    title = f"{year} {_event_name(session_obj, event)} · {session}"
    png = await _render(
        _draw_speed_trace,
        traces,
        delta,
        corners,
        title,
        " vs ".join(m["driver"] for m in meta_laps) + " — speed by lap distance",
    )
    return _result(
        png,
        caption,
        {"laps": meta_laps, "corners": len(corners)},
        save_path,
        "plot_speed_trace",
        [year, event, session, driver, driver2],
    )


def _has_event(session_obj) -> bool:
    try:
        return bool(session_obj.event["EventName"])
    except Exception:
        return False


def _event_name(session_obj, event) -> str:
    return session_obj.event["EventName"] if _has_event(session_obj) else str(event)


# ---------------------------------------------------------------------------
# Position chart
# ---------------------------------------------------------------------------


def _draw_positions(series, highlight, n_drivers, title, subtitle) -> bytes:
    fig, (ax,) = _figure(height=6.5)
    colour = {code: SERIES[i] for i, code in enumerate(highlight)}
    # Background drivers first so highlighted lines sit on top.
    ordered = sorted(series.items(), key=lambda kv: kv[0] in colour)
    for code, (laps, pos) in ordered:
        hl = code in colour
        ax.plot(
            laps,
            pos,
            color=colour.get(code, BACKGROUND_LINE),
            linewidth=2 if hl else 1,
            zorder=3 if hl else 2,
            solid_capstyle="round",
        )
        ax.text(
            laps[-1] + 0.6,
            pos[-1],
            code,
            fontsize=8,
            va="center",
            color=INK if hl else MUTED,
            weight="bold" if hl else "normal",
        )
    ax.set_ylim(n_drivers + 0.5, 0.5)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=n_drivers))
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel("Lap (0 = grid)")
    ax.set_ylabel("Position")
    if highlight:
        _legend(
            ax,
            [Line2D([], [], color=colour[c], linewidth=2, label=c) for c in highlight],
        )
    _title(fig, title, subtitle)
    return _png(fig)


@tool_handler
async def plot_position_chart(
    year: int,
    event: str | int,
    session: str = "R",
    highlight: list[str] | None = None,
    save_path: bool | str = False,
) -> ToolResult:
    """
    Plot the lap-by-lap race position of every driver (a "race history"
    chart). Returns a PNG image.

    Use when the user wants to see how the race unfolded, overtakes,
    pit-cycle shuffles or a driver's recovery drive.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: "R" (default) or "S" (sprint)
        highlight: Up to 8 driver codes drawn in colour (others are grey,
                   all are labelled). Default: the podium finishers.
        save_path: If True, also write the PNG to the export directory
                   (default `./fastf1-exports/`); a string is a directory or
                   a full `.png` path.
    """
    logger.info(f"plot_position_chart: {year=}, {event=}, {session=}, {highlight=}")
    session_obj = await require_session(year, event, session)
    lookup = build_driver_lookup(session_obj)
    laps = session_obj.laps
    if laps is None or "Position" not in getattr(laps, "columns", []):
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE, "Lap position data is unavailable."
        )

    order = _finishing_order(session_obj)
    codes = _resolve_codes(lookup, highlight) if highlight else order[:3]
    if len(codes) > MAX_LINE_SERIES:
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER,
            f"At most {MAX_LINE_SERIES} drivers can be highlighted "
            f"(got {len(codes)}); the rest are always drawn in grey.",
        )

    grid = {}
    results = getattr(session_obj, "results", None)
    if results is not None and "GridPosition" in getattr(results, "columns", []):
        for _, r in results.iterrows():
            g = r.get("GridPosition")
            if r.get("Abbreviation") and pd.notna(g) and g > 0:
                grid[r["Abbreviation"]] = int(g)

    series = {}
    for drv, grp in laps.dropna(subset=["Position", "LapNumber"]).groupby("Driver"):
        code = lookup.get(drv, {}).get("driverCode") or drv
        grp = grp.sort_values("LapNumber")
        lap_nos = grp["LapNumber"].astype(int).tolist()
        pos = grp["Position"].astype(int).tolist()
        if code in grid:
            lap_nos, pos = [0] + lap_nos, [grid[code]] + pos
        if lap_nos:
            series[code] = (lap_nos, pos)
    if not series:
        raise FastF1MCPError(ErrorCode.DATA_UNAVAILABLE, "No lap positions found.")

    missing = [c for c in codes if c not in series]
    if missing:
        raise FastF1MCPError(
            ErrorCode.DRIVER_NOT_FOUND,
            f"No lap data for {missing} in {year} {event} {session}.",
        )

    n = max(len(series), max(max(p) for _, p in series.values()))
    png = await _render(
        _draw_positions,
        series,
        codes,
        n,
        f"{year} {_event_name(session_obj, event)} · {session}",
        "Position by lap" + (f" — highlighted: {', '.join(codes)}" if codes else ""),
    )
    winner = order[0] if order else None
    return _result(
        png,
        f"Race position chart ({len(series)} drivers); highlighted {', '.join(codes)}"
        + (f"; winner {winner}" if winner else ""),
        {"drivers": len(series), "highlighted": codes},
        save_path,
        "plot_position_chart",
        [year, event, session],
    )


# ---------------------------------------------------------------------------
# Tyre strategy
# ---------------------------------------------------------------------------


def _stints_by_driver(laps, lookup) -> dict[str, list[tuple[int, int, str]]]:
    out: dict[str, list[tuple[int, int, str]]] = {}
    df = laps.dropna(subset=["Stint", "LapNumber"])
    for (drv, _stint), grp in df.groupby(["Driver", "Stint"]):
        start, end = int(grp["LapNumber"].min()), int(grp["LapNumber"].max())
        # Same phantom lap-1 stint filter as get_stint_analysis.
        if start == 1 and end == 1 and grp["LapTime"].isna().all():
            continue
        comp = grp["Compound"].dropna()
        compound = str(comp.iloc[0]).upper() if len(comp) else "UNKNOWN"
        code = lookup.get(drv, {}).get("driverCode") or drv
        out.setdefault(code, []).append((start, end, compound))
    for v in out.values():
        v.sort()
    return out


def _draw_strategy(rows, total_laps, title, subtitle) -> bytes:
    fig, (ax,) = _figure(height=0.34 * len(rows) + _HEADER_IN + _FOOTER_IN + 0.3)
    used = []
    for y, (code, stints) in enumerate(rows):
        for start, end, compound in stints:
            colour, letter = COMPOUNDS.get(compound, UNKNOWN_COMPOUND)
            if compound not in used:
                used.append(compound)
            width = end - start + 1
            ax.barh(
                y,
                width,
                left=start - 1,
                height=0.72,
                color=colour,
                edgecolor=SURFACE,
                linewidth=2,
            )
            if width >= 3:
                ax.text(
                    start - 1 + width / 2,
                    y,
                    letter,
                    ha="center",
                    va="center",
                    fontsize=7.5,
                    color=INK if compound in {"MEDIUM", "HARD"} else SURFACE,
                    weight="bold",
                )
    ax.set_yticks(range(len(rows)), [c for c, _ in rows])
    ax.set_ylim(len(rows) - 0.5, -0.5)
    ax.set_xlim(0, total_laps + 0.5)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Lap")
    _legend(
        ax,
        [
            Patch(
                color=COMPOUNDS.get(c, UNKNOWN_COMPOUND)[0],
                label=f"{COMPOUNDS.get(c, UNKNOWN_COMPOUND)[1]} · {c.title()}",
            )
            for c in sorted(
                used, key=lambda c: list(COMPOUNDS).index(c) if c in COMPOUNDS else 99
            )
        ],
    )
    _title(fig, title, subtitle)
    return _png(fig)


@tool_handler
async def plot_tyre_strategy(
    year: int,
    event: str | int,
    session: str = "R",
    save_path: bool | str = False,
) -> ToolResult:
    """
    Plot every driver's tyre strategy as horizontal stint bars coloured by
    compound, in finishing order. Returns a PNG image.

    Use when the user asks to "show the strategies", "visualise the stints"
    or compare 1-stop vs 2-stop plans at a glance.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: "R" (default) or "S" (sprint)
        save_path: If True, also write the PNG to the export directory
                   (default `./fastf1-exports/`); a string is a directory or
                   a full `.png` path.
    """
    logger.info(f"plot_tyre_strategy: {year=}, {event=}, {session=}")
    session_obj = await require_session(year, event, session)
    lookup = build_driver_lookup(session_obj)
    stints = _stints_by_driver(session_obj.laps, lookup)
    if not stints:
        raise FastF1MCPError(ErrorCode.DATA_UNAVAILABLE, "No stint data found.")

    order = [c for c in _finishing_order(session_obj) if c in stints]
    order += sorted(c for c in stints if c not in order)
    rows = [(c, stints[c]) for c in order]
    total = max(end for s in stints.values() for _, end, _ in s)

    stops = {c: len(s) - 1 for c, s in stints.items()}
    common = max(set(stops.values()), key=list(stops.values()).count)
    png = await _render(
        _draw_strategy,
        rows,
        total,
        f"{year} {_event_name(session_obj, event)} · {session}",
        "Tyre strategy by stint (finishing order, top = winner)",
    )
    return _result(
        png,
        f"Tyre strategy chart for {len(rows)} drivers; most common: {common}-stop",
        {
            "drivers": len(rows),
            "stops": stops,
            "strategies": {c: [comp for _, _, comp in s] for c, s in stints.items()},
        },
        save_path,
        "plot_tyre_strategy",
        [year, event, session],
    )


# ---------------------------------------------------------------------------
# Lap times
# ---------------------------------------------------------------------------


def _draw_lap_times(series, title, subtitle) -> bytes:
    fig, (ax,) = _figure(height=5.5)
    used_compounds = []
    for i, (code, df) in enumerate(series):
        # Break the connecting line wherever laps were omitted (pit/SC), so
        # it never implies data across the gap.
        y = df["sec"].to_numpy(dtype=float)
        x = df["lap"].to_numpy(dtype=float)
        breaks = np.flatnonzero(np.diff(x) > 1) + 1
        ax.plot(
            np.insert(x, breaks, np.nan),
            np.insert(y, breaks, np.nan),
            color=SERIES[i],
            linewidth=1,
            alpha=0.6,
        )
        for compound, grp in df.groupby("compound"):
            if compound not in used_compounds:
                used_compounds.append(compound)
            ax.scatter(
                grp["lap"],
                grp["sec"],
                s=34,
                marker=COMPOUND_MARKERS.get(compound, "o"),
                color=SERIES[i],
                edgecolors=SURFACE,
                linewidths=1.5,
                zorder=3,
            )
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_laptime))
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel("Lap")
    ax.set_ylabel("Lap time")
    handles = [
        Line2D([], [], color=SERIES[i], linewidth=2, label=code)
        for i, (code, _) in enumerate(series)
    ] + [
        Line2D(
            [],
            [],
            linestyle="",
            marker=COMPOUND_MARKERS.get(c, "o"),
            color=INK_2,
            markersize=6,
            label=c.title(),
        )
        for c in sorted(
            used_compounds,
            key=lambda c: list(COMPOUNDS).index(c) if c in COMPOUNDS else 99,
        )
    ]
    _legend(ax, handles)
    _title(fig, title, subtitle)
    return _png(fig)


@tool_handler
async def plot_lap_times(
    year: int,
    event: str | int,
    session: str = "R",
    drivers: list[str] | None = None,
    save_path: bool | str = False,
) -> ToolResult:
    """
    Plot lap time against lap number for up to three drivers, with marker
    shape showing the tyre compound. Returns a PNG image.

    Use for "show me the race pace of X vs Y", "plot their lap times",
    or to visualise degradation and safety-car phases.

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (default "R")
        drivers: Up to 3 driver codes. Default: the top three finishers.
        save_path: If True, also write the PNG to the export directory
                   (default `./fastf1-exports/`); a string is a directory or
                   a full `.png` path.

    Note:
        Lap 1, in/out laps and laps slower than 107% of the driver's median
        (safety car, incidents) are omitted so the y-axis stays readable;
        the caption reports how many slow laps were dropped and the line
        breaks across every omitted lap.
    """
    logger.info(f"plot_lap_times: {year=}, {event=}, {session=}, {drivers=}")
    session_obj = await require_session(year, event, session)
    lookup = build_driver_lookup(session_obj)
    codes = (
        _resolve_codes(lookup, drivers)
        if drivers
        else (_finishing_order(session_obj)[:MAX_SCATTER_SERIES])
    )
    if not codes:
        raise FastF1MCPError(ErrorCode.DATA_UNAVAILABLE, "No drivers to plot.")
    if len(codes) > MAX_SCATTER_SERIES:
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER,
            f"plot_lap_times supports up to {MAX_SCATTER_SERIES} drivers so "
            f"their colours stay distinguishable (got {len(codes)}).",
            suggestions=["Split into several charts, or use get_race_pace"],
        )

    series, dropped = [], 0
    for code in codes:
        laps = session_obj.laps.pick_drivers(code)
        if len(laps) == 0:
            raise FastF1MCPError(
                ErrorCode.DRIVER_NOT_FOUND, f"No laps found for driver '{code}'"
            )
        laps = laps.pick_wo_box().dropna(subset=["LapTime", "LapNumber"])
        laps = laps[laps["LapNumber"] > 1]  # standing start skews the axis
        df = pd.DataFrame(
            {
                "lap": laps["LapNumber"].astype(int).to_numpy(),
                "sec": laps["LapTime"].map(to_seconds).astype(float).to_numpy(),
                "compound": (
                    laps["Compound"].fillna("UNKNOWN").astype(str).str.upper()
                    if "Compound" in laps.columns
                    else "UNKNOWN"
                ),
            }
        )
        keep = df["sec"] <= df["sec"].median() * 1.07
        dropped += int((~keep).sum())
        series.append((code, df[keep]))

    png = await _render(
        _draw_lap_times,
        series,
        f"{year} {_event_name(session_obj, event)} · {session}",
        "Lap times (marker = compound; lap 1, in/out and slow laps omitted)",
    )
    best = {
        code: _fmt_laptime(float(df["sec"].min())) for code, df in series if len(df)
    }
    return _result(
        png,
        "Lap-time chart for "
        + ", ".join(f"{c} (best {t})" for c, t in best.items())
        + f"; {dropped} slow laps omitted",
        {"drivers": codes, "bestLaps": best, "omittedLaps": dropped},
        save_path,
        "plot_lap_times",
        [year, event, session, *codes],
    )
