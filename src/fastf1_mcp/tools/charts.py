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
from ..utils.circuits import load_circuits, resolve_circuit, smooth_loop
from ..utils.errors import ErrorCode, FastF1MCPError
from ..utils.exports import _display_path, resolve_export_path
from ..utils.session_loader import require_session, tool_handler
from ..utils.timing import to_seconds
from .corners import corner_label, load_circuit_info, load_corners
from .telemetry import _cumulative_time_at_distances, _get_driver_lap
from .track import _open_runs, session_drs_zones

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
DRS_BAND = "#ecebe6"

# Sequential blue ramp (light -> dark = slow -> fast). Starts at step 250 so
# the slowest colour still clears 2:1 against the chart surface.
SPEED_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]
# Ordinal ramp for gears 1-8, same hue.
GEAR_RAMP = [
    "#86b6ef",
    "#6da7ec",
    "#5598e7",
    "#3987e5",
    "#2a78d6",
    "#1c5cab",
    "#184f95",
    "#0d366b",
]

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
    nrows: int = 1, height: float = 5.5, height_ratios=None, width: float = 11
) -> tuple[Figure, list]:
    fig = Figure(figsize=(width, height), dpi=120, facecolor=SURFACE)
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


def _draw_speed_trace(traces, delta, corners, drs_runs, title, subtitle) -> bytes:
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
    for start, end in drs_runs:
        for a in axes:
            a.axvspan(start, end, color=DRS_BAND, zorder=0, linewidth=0)
    if drs_runs:
        handles.append(Patch(color=DRS_BAND, label="DRS open"))
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
    show_drs: bool = True,
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
        show_drs: Shade where `driver`'s DRS flap was open on this lap
                  (default True)
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

    drs_runs = _open_runs(tels[0]) if show_drs else []

    title = f"{year} {_event_name(session_obj, event)} · {session}"
    png = await _render(
        _draw_speed_trace,
        traces,
        delta,
        corners,
        drs_runs,
        title,
        " vs ".join(m["driver"] for m in meta_laps) + " — speed by lap distance",
    )
    return _result(
        png,
        caption,
        {
            "laps": meta_laps,
            "corners": len(corners),
            "drsOpen": [{"startM": round(a), "endM": round(b)} for a, b in drs_runs],
        },
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
    highlight: list[str] | str | None = None,
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
    drivers: list[str] | str | None = None,
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


# ---------------------------------------------------------------------------
# Track map
# ---------------------------------------------------------------------------


def _rotate(x: np.ndarray, y: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rotate track coordinates so the map matches the official orientation."""
    a = np.deg2rad(angle_deg)
    rot = np.array([[np.cos(a), np.sin(a)], [-np.sin(a), np.cos(a)]])
    return np.column_stack([x, y]) @ rot


def _lap_position_data(lap_row) -> pd.DataFrame:
    """X/Y position samples (with SessionTime) for one lap."""
    return lap_row.get_pos_data()


def _corner_marks(corners: pd.DataFrame | None, rotation: float) -> list[tuple]:
    """(label, label_xy, track_xy) for each corner, offset off the racing line."""
    if corners is None or corners.empty or not {"X", "Y"}.issubset(corners.columns):
        return []
    marks = []
    offset = 500.0  # FastF1 map units (1/10 m)
    for _, c in corners.iterrows():
        a = np.deg2rad(float(c.get("Angle", 0.0) or 0.0))
        lx = float(c["X"]) + offset * np.cos(a)
        ly = float(c["Y"]) + offset * np.sin(a)
        (tx, ty), (px, py) = _rotate(
            np.array([lx, float(c["X"])]), np.array([ly, float(c["Y"])]), rotation
        )
        marks.append((corner_label(c), (tx, px), (ty, py)))
    return marks


def _draw_corner_marks(ax, marks) -> None:
    for label, (lx, px), (ly, py) in marks:
        ax.plot([px, lx], [py, ly], color=AXIS, linewidth=0.8, zorder=4)
        ax.scatter([lx], [ly], s=170, color=SURFACE, edgecolors=AXIS, zorder=5)
        ax.text(
            lx,
            ly,
            label[1:],
            fontsize=6.5,
            color=INK_2,
            ha="center",
            va="center",
            zorder=6,
        )


def _map_axes(fig: Figure, ax) -> None:
    ax.set_aspect("equal")
    ax.axis("off")


def _draw_track_trace(xy, values, mode, drs_mask, marks, title, subtitle) -> bytes:
    from matplotlib.collections import LineCollection
    from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap

    fig, (ax,) = _figure(height=7.5, width=9)
    _map_axes(fig, ax)
    pts = xy.reshape(-1, 1, 2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    handles = []

    if drs_mask is not None and drs_mask.any():
        band = LineCollection(
            segs[drs_mask[:-1]], colors=SERIES[1], linewidths=11, alpha=0.4, zorder=1
        )
        ax.add_collection(band)
        handles.append(
            Line2D([], [], color=SERIES[1], alpha=0.4, linewidth=8, label="DRS zone")
        )

    if mode == "none" or values is None:
        ax.plot(xy[:, 0], xy[:, 1], color=INK_2, linewidth=3, zorder=2)
    else:
        if mode == "gear":
            cmap = ListedColormap(GEAR_RAMP)
            norm = BoundaryNorm(np.arange(0.5, 9.5, 1.0), cmap.N)
        else:
            cmap = LinearSegmentedColormap.from_list("speed", SPEED_RAMP)
            norm = None
        lc = LineCollection(
            segs, cmap=cmap, norm=norm, linewidths=4, zorder=2, capstyle="round"
        )
        lc.set_array(values[:-1])
        if norm is None:
            lc.set_clim(float(np.nanmin(values)), float(np.nanmax(values)))
        ax.add_collection(lc)
        cbar = fig.colorbar(
            lc,
            ax=ax,
            fraction=0.03,
            pad=0.02,
            ticks=range(1, 9) if mode == "gear" else None,
        )
        cbar.set_label("Gear" if mode == "gear" else "Speed (km/h)", color=INK_2)
        cbar.outline.set_visible(False)
        cbar.ax.tick_params(colors=INK_2, labelsize=8, length=0)

    # Start/finish and direction of travel.
    k = min(25, len(xy) - 1)
    ax.annotate(
        "",
        xy=xy[k],
        xytext=xy[0],
        arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 1.5},
        zorder=6,
    )
    ax.scatter([xy[0, 0]], [xy[0, 1]], s=40, color=INK, zorder=6, marker="s")
    handles.append(
        Line2D(
            [],
            [],
            color=INK,
            marker="s",
            linestyle="",
            label="Start/finish → direction",
        )
    )

    _draw_corner_marks(ax, marks)
    ax.autoscale_view()
    ax.margins(0.08)
    _legend(ax, handles)
    _title(fig, title, subtitle)
    return _png(fig)


def _label_offsets(cars: list[dict], centre: np.ndarray, extent: float) -> dict:
    """
    Place highlighted-car labels radially outside the track, stacking them
    further out when two cars are close, so labels never overlap.
    """
    placed: list[np.ndarray] = []
    offsets = {}
    for car in cars:
        p = np.array([car["x"], car["y"]])
        v = p - centre
        n = np.linalg.norm(v)
        unit = v / n if n > 0 else np.array([1.0, 0.0])
        step = 0
        while (
            any(
                np.linalg.norm((p + unit * extent * 0.07 * (1 + step)) - q)
                < extent * 0.06
                for q in placed
            )
            and step < 6
        ):
            step += 1
        placed.append(p + unit * extent * 0.07 * (1 + step))
        offsets[car["driver"]] = unit * (16 + 14 * step)
    return offsets


def _draw_track_positions(outline, cars, highlight, marks, title, subtitle) -> bytes:
    fig, (ax,) = _figure(height=7.5, width=9)
    _map_axes(fig, ax)
    ax.plot(
        outline[:, 0],
        outline[:, 1],
        color=BACKGROUND_LINE,
        linewidth=9,
        solid_capstyle="round",
        zorder=1,
    )
    _draw_corner_marks(ax, marks)
    colour = {code: SERIES[i] for i, code in enumerate(highlight)}

    for car in sorted(cars, key=lambda c: c["driver"] in colour):
        hl = car["driver"] in colour
        ax.scatter(
            [car["x"]],
            [car["y"]],
            s=110 if hl else 55,
            color=colour.get(car["driver"], MUTED),
            edgecolors=SURFACE,
            linewidths=2,
            zorder=4 if hl else 3,
        )

    centre = outline.mean(axis=0)
    extent = float(np.ptp(outline, axis=0).max()) or 1.0
    hl_cars = sorted(
        (c for c in cars if c["driver"] in colour), key=lambda c: c["position"]
    )
    for car in hl_cars:
        dx, dy = _label_offsets(hl_cars, centre, extent)[car["driver"]]
        ax.annotate(
            f"P{car['position']} {car['driver']}",
            (car["x"], car["y"]),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=8.5,
            color=INK,
            weight="bold",
            ha="left" if dx >= 0 else "right",
            va="bottom" if dy >= 0 else "top",
            arrowprops={"arrowstyle": "-", "color": AXIS, "lw": 0.8},
            zorder=5,
        )

    # Running order in a right-hand panel — readable even when cars bunch.
    h = fig.get_figheight()
    top = 1 - 1.05 / h
    fig.text(0.86, top, "Order", fontsize=9, color=INK, weight="bold", va="top")
    order = "\n".join(
        f"{c['position']:>2} {c['driver']}" + (" pit" if c.get("inPit") else "")
        for c in sorted(cars, key=lambda c: c["position"])
    )
    fig.text(
        0.86,
        top - 0.3 / h,
        order,
        fontsize=8,
        family="monospace",
        color=INK_2,
        va="top",
        linespacing=1.5,
    )
    if highlight:
        _legend(
            ax,
            [
                Line2D(
                    [],
                    [],
                    color=colour[c],
                    marker="o",
                    linestyle="",
                    markersize=8,
                    label=c,
                )
                for c in highlight
            ],
        )
    _title(fig, title, subtitle)
    fig.subplots_adjust(left=0.03, right=0.84, top=top, bottom=0.3 / h)
    ax.margins(0.12)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    return buf.getvalue()


async def _stored_outline_fallback(
    error: FastF1MCPError, year: int, event, at_lap, save_path
) -> ToolResult | None:
    """
    When FastF1 can't provide a lap (offline, data not published), draw the
    stored circuit outline instead — trace mode only, 2018+ layouts only.
    """
    if at_lap is not None or error.code not in (
        ErrorCode.SESSION_NOT_FOUND,
        ErrorCode.DATA_UNAVAILABLE,
    ):
        return None
    try:
        c = resolve_circuit(event, year)
    except FastF1MCPError:
        return None
    reason = error.message.split(". ")[0][:160].rstrip(".")
    note = (
        "FastF1 telemetry unavailable — showing the stored circuit outline "
        "(no speed/gear colouring, corners or DRS zones)."
    )
    png = await _render_stored_circuit(c, note)
    meta = {"mode": "stored-outline", "fallbackReason": reason, **_circuit_meta(c)}
    caption = (
        f"{c.name} — {_circuit_subtitle(c)}. Stored outline: FastF1 telemetry "
        f"was unavailable ({reason})."
    )
    return _result(png, caption, meta, save_path, "plot_track_map", [year, c.id])


async def _reference_lap(session_obj, driver: str | None, lap):
    """Telemetry of the requested lap (default: the session's fastest lap)."""
    if driver is None:
        fastest = session_obj.laps.pick_fastest()
        if fastest is None:
            raise FastF1MCPError(ErrorCode.DATA_UNAVAILABLE, "No timed laps found.")
        driver = fastest["Driver"]
    lap_row = _get_driver_lap(session_obj, driver, lap)
    if lap_row is None:
        raise FastF1MCPError(
            ErrorCode.DRIVER_NOT_FOUND, f"No lap {lap!r} found for driver '{driver}'"
        )
    try:
        tel = lap_row.get_telemetry()
        if "Distance" not in tel.columns:
            tel = tel.add_distance()
    except Exception as e:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE, f"Telemetry unavailable for {driver}: {e}"
        ) from e
    if not {"X", "Y"}.issubset(tel.columns):
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE, "Lap telemetry has no X/Y position data."
        )
    tel = tel.dropna(subset=["X", "Y", "Distance"]).sort_values("Distance")
    return driver, lap_row, tel


def _cars_at(session_obj, t: float, lookup: dict) -> tuple[list[dict], list[str]]:
    """Every car's X/Y and running order at session time `t` (seconds)."""
    laps = session_obj.laps
    cars, not_running = [], []
    for drv, dl in laps.dropna(subset=["LapStartTime", "Time"]).groupby("Driver"):
        code = lookup.get(drv, {}).get("driverCode") or drv
        start = dl["LapStartTime"].map(to_seconds)
        end = dl["Time"].map(to_seconds)
        current = dl[(start <= t) & (end > t)]
        if current.empty:
            not_running.append(code)
            continue
        row = current.iloc[0]
        try:
            pos = _lap_position_data(row)
        except Exception as e:
            logger.debug(f"No position data for {code}: {e}")
            not_running.append(code)
            continue
        pos = pos.dropna(subset=["X", "Y", "SessionTime"])
        st = pos["SessionTime"].map(to_seconds).to_numpy(dtype=float)
        if len(st) == 0:
            not_running.append(code)
            continue
        order = np.argsort(st)
        st = st[order]
        x = float(np.interp(t, st, pos["X"].to_numpy(dtype=float)[order]))
        y = float(np.interp(t, st, pos["Y"].to_numpy(dtype=float)[order]))
        lap_start, lap_end = to_seconds(row["LapStartTime"]), to_seconds(row["Time"])
        frac = (t - lap_start) / (lap_end - lap_start) if lap_end > lap_start else 0.0
        in_pit = bool(
            (
                "PitOutTime" in row
                and pd.notna(row["PitOutTime"])
                and to_seconds(row["PitOutTime"]) >= t
            )
            or (
                "PitInTime" in row
                and pd.notna(row["PitInTime"])
                and to_seconds(row["PitInTime"]) <= t
            )
        )
        cars.append(
            {
                "driver": code,
                "lap": int(row["LapNumber"]),
                "lapFraction": round(float(frac), 3),
                "x": x,
                "y": y,
                "inPit": in_pit,
            }
        )
    cars.sort(key=lambda c: -(c["lap"] + c["lapFraction"]))
    for i, c in enumerate(cars, start=1):
        c["position"] = i
    return cars, sorted(not_running)


@tool_handler
async def plot_track_map(
    year: int,
    event: str | int,
    session: str = "Q",
    driver: str | None = None,
    lap: int | str = "fastest",
    color_by: str = "speed",
    at_lap: int | None = None,
    highlight: list[str] | str | None = None,
    show_corners: bool = True,
    show_drs: bool = True,
    save_path: bool | str = False,
) -> ToolResult:
    """
    Draw the circuit map. Two modes:

    1. Lap trace (default): the racing line of one lap coloured by speed or
       gear, with numbered corners, DRS zones and the direction of travel.
    2. Car positions (`at_lap=N`): where every car was on track at the
       moment the leader started lap N, with the running order listed.

    Returns a PNG image.

    Data source: FastF1 Live Timing position data + MultiViewer corners
    Coverage: 2018-present

    Use for "show me the track", "where is the car fastest", "which gear
    through each corner", or "where was everyone when the safety car came
    out on lap 34".

    Args:
        year: Season year (2018+)
        event: Race name or round number
        session: Session type (default "Q"; use "R"/"S" for at_lap)
        driver: Driver whose lap is traced (default: the session's fastest lap)
        lap: Lap number or "fastest" (default)
        color_by: "speed" (default), "gear" or "none"
        at_lap: Draw car positions at the start of this lap instead of a trace
        highlight: Up to 8 driver codes coloured in positions mode
                   (default: the top three at that moment)
        show_corners: Number the corners (default True)
        show_drs: Highlight DRS zones on the trace (default True)
        save_path: If True, also write the PNG to the export directory
                   (default `./fastf1-exports/`); a string is a directory or
                   a full `.png` path.

    Returns:
        An image plus a caption. Structured content has the lap used,
        DRS zones and, in positions mode, the running order.
    """
    color_by = (color_by or "speed").lower()
    if color_by not in {"speed", "gear", "none"}:
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER,
            f"color_by must be 'speed', 'gear' or 'none' (got {color_by!r}).",
        )
    logger.info(
        f"plot_track_map: {year=}, {event=}, {session=}, {driver=}, {lap=}, "
        f"{color_by=}, {at_lap=}"
    )
    try:
        session_obj = await require_session(year, event, session, with_telemetry=True)
        lookup = build_driver_lookup(session_obj)
        ref_driver, lap_row, tel = await _reference_lap(session_obj, driver, lap)
    except FastF1MCPError as e:
        fallback = await _stored_outline_fallback(e, year, event, at_lap, save_path)
        if fallback is None:
            raise
        return fallback

    rotation, corners = 0.0, None
    try:
        info = await load_circuit_info(session_obj)
        rotation = float(getattr(info, "rotation", 0.0) or 0.0)
        corners = getattr(info, "corners", None)
    except FastF1MCPError as e:
        logger.info(f"Track map without circuit info: {e.message}")
    marks = _corner_marks(corners, rotation) if show_corners else []
    xy = _rotate(
        tel["X"].to_numpy(dtype=float), tel["Y"].to_numpy(dtype=float), rotation
    )
    title = f"{year} {_event_name(session_obj, event)} · {session}"
    ref_code = lookup.get(ref_driver, {}).get("driverCode") or ref_driver

    if at_lap is None:
        values = None
        if color_by == "speed" and "Speed" in tel.columns:
            values = tel["Speed"].to_numpy(dtype=float)
        elif color_by == "gear" and "nGear" in tel.columns:
            values = tel["nGear"].to_numpy(dtype=float)

        zones = []
        drs_mask = None
        if show_drs:
            zones, _, _ = await session_drs_zones(session_obj)
            d = tel["Distance"].to_numpy(dtype=float)
            drs_mask = np.zeros(len(d), dtype=bool)
            lap_len = float(d.max()) if len(d) else 0.0
            for z in zones:
                if z.get("crossesLine"):
                    drs_mask |= (d >= z["startM"]) | (d <= z["endM"])
                else:
                    drs_mask |= (d >= z["startM"]) & (d <= z["endM"])
            if lap_len == 0:
                drs_mask = None

        lap_no = lap_row.get("LapNumber")
        lap_time = lap_row.get("LapTime")
        lt = _fmt_laptime(to_seconds(lap_time)) if pd.notna(lap_time) else "n/a"
        subtitle = (
            f"{ref_code} lap {int(lap_no) if pd.notna(lap_no) else '?'} ({lt}) — "
            + {
                "speed": "coloured by speed",
                "gear": "coloured by gear",
                "none": "racing line",
            }[color_by]
        )
        png = await _render(
            _draw_track_trace, xy, values, color_by, drs_mask, marks, title, subtitle
        )
        meta = {
            "mode": "trace",
            "driver": ref_code,
            "lapNumber": int(lap_no) if pd.notna(lap_no) else None,
            "colorBy": color_by,
            "corners": len(marks),
            "drsZones": zones,
        }
        caption = f"Track map — {subtitle}; {len(zones)} DRS zones marked"
        if values is not None and color_by == "speed":
            caption += f"; speed {np.nanmin(values):.0f}–{np.nanmax(values):.0f} km/h"
        return _result(
            png,
            caption,
            meta,
            save_path,
            "plot_track_map",
            [year, event, session, ref_code, color_by],
        )

    # ---------------- positions mode ----------------
    from ..utils.timing import LapClock

    clock = LapClock.from_laps(session_obj.laps)
    if clock is None or at_lap not in set(clock.lap_numbers.tolist()):
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER,
            f"Lap {at_lap} not found in {year} {event} {session}.",
        )
    idx = int(np.flatnonzero(clock.lap_numbers == at_lap)[0])
    t = float(clock.start_secs[idx]) + 0.5  # just after the leader crosses the line
    cars, not_running = _cars_at(session_obj, t, lookup)
    if not cars:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE, f"No car positions available at lap {at_lap}."
        )
    for c in cars:
        ((c["x"], c["y"]),) = _rotate(np.array([c["x"]]), np.array([c["y"]]), rotation)
    codes = (
        _resolve_codes(lookup, highlight)
        if highlight
        else [c["driver"] for c in cars[:3]]
    )
    if len(codes) > MAX_LINE_SERIES:
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER,
            f"At most {MAX_LINE_SERIES} drivers can be highlighted.",
        )
    missing = [c for c in codes if c not in {car["driver"] for car in cars}]
    if missing:
        raise FastF1MCPError(
            ErrorCode.DRIVER_NOT_FOUND,
            f"{missing} not on track at lap {at_lap}.",
        )
    png = await _render(
        _draw_track_positions,
        xy,
        cars,
        codes,
        marks,
        title,
        f"Car positions as the leader starts lap {at_lap}",
    )
    order = [
        {k: c[k] for k in ("position", "driver", "lap", "lapFraction", "inPit")}
        for c in cars
    ]
    caption = (
        f"Track positions at the start of lap {at_lap}: "
        + ", ".join(f"P{c['position']} {c['driver']}" for c in cars[:5])
        + (f"; not running: {', '.join(not_running)}" if not_running else "")
    )
    return _result(
        png,
        caption,
        {
            "mode": "positions",
            "atLap": at_lap,
            "order": order,
            "notRunning": not_running,
        },
        save_path,
        "plot_track_map",
        [year, event, session, f"lap-{at_lap}"],
    )


# ---------------------------------------------------------------------------
# Stored circuit outlines (offline)
# ---------------------------------------------------------------------------

ASPHALT = "#3d3c39"


def _nice_scale(extent_m: float) -> int:
    for step in (100, 200, 250, 500, 1000, 2000):
        if step >= extent_m / 8:
            return step
    return 2000


def _draw_outline(ax, xy: np.ndarray, *, width: float = 8, arrow: bool = True) -> None:
    """Track ribbon with a start/finish bar and a direction arrow."""
    loop = np.vstack([xy, xy[:1]])
    ax.plot(
        loop[:, 0],
        loop[:, 1],
        color=ASPHALT,
        linewidth=width,
        solid_capstyle="round",
        solid_joinstyle="round",
        zorder=2,
    )
    # Start/finish: a fixed-size bar across the track at the first point.
    # A 2-point asterisk marker is a line; rotation 0 is vertical, so rotating
    # by the track heading makes it perpendicular to the track.
    t = xy[min(3, len(xy) - 1)] - xy[0]
    heading = float(np.degrees(np.arctan2(t[1], t[0])))
    size = width * 2.6
    for colour, mew in ((INK, width * 0.65), (SURFACE, width * 0.2)):
        ax.plot(
            [xy[0, 0]],
            [xy[0, 1]],
            linestyle="",
            marker=(2, 2, heading),
            markersize=size,
            markeredgecolor=colour,
            markeredgewidth=mew,
            zorder=5,
        )
    if arrow and len(xy) > 80:
        a, b = xy[len(xy) // 40], xy[len(xy) // 40 + len(xy) // 60]
        ax.annotate(
            "",
            xy=b,
            xytext=a,
            zorder=6,
            arrowprops={
                "arrowstyle": "-|>",
                "color": SURFACE,
                "lw": 1.6,
                "mutation_scale": 12,
            },
        )


def _draw_circuit(circuit, xy, title, subtitle, note: str | None) -> bytes:
    fig, (ax,) = _figure(height=7.5, width=9)
    _map_axes(fig, ax)
    _draw_outline(ax, xy)
    extent = float(np.ptp(xy, axis=0).max())
    ax.margins(0.1)
    ax.autoscale_view()

    # Scale bar (bottom-left) and north arrow (top-right).
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    bar = _nice_scale(extent)
    bx, by = x0 + (x1 - x0) * 0.03, y0 + (y1 - y0) * 0.045
    ax.plot([bx, bx + bar], [by, by], color=INK_2, linewidth=2, zorder=3)
    ax.text(
        bx + bar / 2,
        by - (y1 - y0) * 0.012,
        f"{bar} m" if bar < 1000 else f"{bar // 1000} km",
        ha="center",
        va="top",
        fontsize=8,
        color=INK_2,
    )
    nx, ny = x1 - (x1 - x0) * 0.04, y1 - (y1 - y0) * 0.1
    ax.annotate(
        "N",
        xy=(nx, ny + (y1 - y0) * 0.06),
        xytext=(nx, ny),
        ha="center",
        va="top",
        fontsize=9,
        color=INK_2,
        arrowprops={"arrowstyle": "-|>", "color": INK_2, "lw": 1.2},
    )

    _legend(
        ax,
        [
            Line2D([], [], color=ASPHALT, linewidth=6, label="Track"),
            Line2D([], [], color=INK, linewidth=3, label="Start / finish"),
        ],
    )
    if note:
        fig.text(0.01, 0.015, note, fontsize=8, color=MUTED, va="bottom")
    _title(fig, title, subtitle)
    return _png(fig)


def _circuit_meta(c) -> dict:
    return {
        "circuitId": c.id,
        "name": c.name,
        "location": c.location,
        "lengthM": c.length_m,
        "outlineLengthM": round(c.measured_length_m),
        "direction": c.direction,
        "firstGrandPrix": c.first_gp,
        "opened": c.opened,
        "altitudeM": c.altitude_m,
        "source": "bacinger/f1-circuits (MIT), stored outline",
    }


def _circuit_subtitle(c) -> str:
    parts = [c.location, f"{c.length_m / 1000:.3f} km", c.direction]
    if c.first_gp:
        parts.append(f"first GP {c.first_gp}")
    return " · ".join(p for p in parts if p)


async def _render_stored_circuit(c, note: str | None) -> bytes:
    xy = smooth_loop(c.xy)
    return await _render(_draw_circuit, c, xy, c.name, _circuit_subtitle(c), note)


def _draw_season(cells, title, subtitle) -> bytes:
    cols = 6
    rows = int(np.ceil(len(cells) / cols))
    fig = Figure(figsize=(12, 2.15 * rows + 1.1), dpi=120, facecolor=SURFACE)
    axes = fig.subplots(rows, cols, squeeze=False)
    for ax in axes.flat:
        ax.set_facecolor(SURFACE)
        ax.axis("off")
    for ax, (label, sub, xy) in zip(axes.flat, cells):
        ax.set_aspect("equal")
        _draw_outline(ax, xy, width=2.6, arrow=False)
        ax.margins(0.12)
        ax.set_title(label, fontsize=9, color=INK, loc="left", pad=2, weight="bold")
        ax.text(
            0, -0.04, sub, transform=ax.transAxes, fontsize=7.5, color=INK_2, va="top"
        )
    h = fig.get_figheight()
    fig.text(0.01, 1 - 0.15 / h, title, fontsize=14, color=INK, weight="bold", va="top")
    fig.text(0.01, 1 - 0.45 / h, subtitle, fontsize=10, color=INK_2, va="top")
    fig.subplots_adjust(
        left=0.02,
        right=0.98,
        top=1 - 1.0 / h,
        bottom=0.25 / h,
        hspace=0.45,
        wspace=0.12,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    return buf.getvalue()


@tool_handler
async def plot_circuit(
    circuit: str | None = None,
    year: int | None = None,
    save_path: bool | str = False,
) -> ToolResult:
    """
    Draw a real F1 circuit from stored outline data — works offline, no
    session download needed. With only `year`, draws every circuit on that
    season's calendar as a sheet of small maps.

    Data source: bundled circuit outlines (bacinger/f1-circuits, MIT),
    covering every F1 circuit used since 2018 plus classic venues.

    Use for "show me the Monaco track", "what does Suzuka look like",
    "draw the 2026 calendar circuits", or when FastF1 telemetry is not
    available. For speed/gear-coloured maps, corners and DRS zones from a
    real lap, use plot_track_map.

    Args:
        circuit: Grand Prix name, circuit name, location or nickname
                 ("Monaco", "British", "COTA", "Spa", "Yas Marina"), or a
                 round number together with `year`.
        year: Season. Disambiguates names whose venue changed (the Spanish
              GP moves to Madrid in 2026) and is needed for round numbers.
              On its own (no `circuit`), draws that season's calendar
              (2020-2026 stored).
        save_path: If True, also write the PNG to the export directory
                   (default `./fastf1-exports/`); a string is a directory or
                   a full `.png` path.

    Returns:
        An image plus a caption. Structured content has the circuit name,
        location, official length, direction of racing, first Grand Prix
        and altitude (or the season's circuit list).

    Note:
        Maps are drawn north-up from geographic coordinates (official F1
        maps are often rotated). The outline starts on the start/finish
        line and the arrow shows the direction of racing.
    """
    logger.info(f"plot_circuit: {circuit=}, {year=}")
    if circuit is None and year is None:
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER,
            "Give a circuit (e.g. 'Monaco') or a year for the season sheet.",
        )

    if circuit is None:
        from ..utils.circuits import _calendar, calendar_years

        ids = _calendar(year)
        if not ids:
            raise FastF1MCPError(
                ErrorCode.INVALID_PARAMETER,
                f"No stored calendar for {year}.",
                suggestions=[
                    f"Stored seasons: {', '.join(map(str, calendar_years()))}"
                ],
            )
        circuits = load_circuits()
        cells = [
            (
                f"R{i} · {circuits[cid].location}",
                f"{circuits[cid].length_m / 1000:.3f} km · {circuits[cid].direction}",
                smooth_loop(circuits[cid].xy, n=500),
            )
            for i, cid in enumerate(ids, start=1)
        ]
        png = await _render(
            _draw_season,
            cells,
            f"{year} Formula 1 circuits",
            "In calendar order · north up · bar marks the start/finish line",
        )
        meta = {
            "year": year,
            "rounds": [
                {"round": i, **_circuit_meta(circuits[cid])}
                for i, cid in enumerate(ids, start=1)
            ],
        }
        return _result(
            png,
            f"{year} calendar: {len(ids)} circuits",
            meta,
            save_path,
            "plot_circuit",
            [year, "calendar"],
        )

    c = resolve_circuit(circuit, year)
    png = await _render_stored_circuit(c, None)
    caption = f"{c.name} — {_circuit_subtitle(c)} (stored outline, north up)"
    return _result(png, caption, _circuit_meta(c), save_path, "plot_circuit", [c.id])
