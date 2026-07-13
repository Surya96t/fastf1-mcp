"""
Vega-Lite v5 spec-building helpers shared by the chart tools.

The chart tools (v0.3.0) return ready-to-render Vega-Lite specs that Claude
Artifacts can embed directly via react-vega. These helpers keep the per-tool
spec code small and ensure team/driver/compound colours stay consistent
across charts.
"""

import base64
import json
import logging
from typing import Any

import fastf1.plotting

try:
    import vl_convert as _vlc

    _VL_CONVERT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _vlc = None  # type: ignore[assignment]
    _VL_CONVERT_AVAILABLE = False

logger = logging.getLogger(__name__)


VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


# Returned alongside every chart tool's response. Tells the calling model how
# to surface the spec so the user actually sees a chart — not a JSON dump.
# MCP clients (Claude Desktop, Copilot, Cursor, …) don't auto-render
# Vega-Lite from tool output; the model has to author an HTML artifact that
# loads vega-embed and inlines the spec. Without this instruction, models
# tend to invent `<anartifact>`-style wrappers that no client recognises.
RENDERING_INSTRUCTIONS: str = (
    "The chart has been automatically opened in the user's default browser "
    "(see 'htmlPath' in this response for the file location).\n\n"
    "Additionally:\n"
    "• Claude Desktop / claude.ai (Artifacts): create an HTML artifact "
    "whose entire content is the exact value of the 'html' field. Copy it "
    "verbatim — do not modify it.\n"
    "• VS Code / other clients: tell the user the chart is open in their "
    "browser, or share the 'htmlPath' so they can open it manually.\n\n"
    "Do NOT invent custom tags like <anartifact>. "
    "Do NOT paste the vegaLiteSpec JSON as visible chat text."
)

# F1 broadcast convention. HARD is technically white — we use light grey so
# the line stays visible against Vega's default white background.
COMPOUND_COLOR_SCALE: dict[str, str] = {
    "SOFT": "#FF3333",
    "MEDIUM": "#FFD23F",
    "HARD": "#CCCCCC",
    "INTERMEDIATE": "#43B02A",
    "WET": "#0070C0",
    "UNKNOWN": "#888888",
}

# Neutral fallback when fastf1.plotting can't resolve a driver/team — keeps
# the chart renderable even if FastF1's colour map drifts.
_FALLBACK_COLOR = "#888888"


def base_spec(
    title: str,
    *,
    mark: str | dict,
    width: int = 700,
    height: int = 400,
) -> dict:
    """Return a starter Vega-Lite v5 spec with title, dimensions, and mark.

    Callers attach `data` and `encoding` on top.
    """
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": title,
        "width": width,
        "height": height,
        "mark": mark,
    }


def driver_color_scale(drivers: list[str], session_obj: Any) -> dict:
    """Build a Vega-Lite ``{"domain": [...], "range": [...]}`` colour scale
    from ``fastf1.plotting.get_driver_color()`` for the given driver codes.

    Order in ``domain`` matches the input list so callers can rely on it for
    legend ordering. Drivers that fastf1 can't resolve fall back to a neutral
    grey so the chart still renders.
    """
    range_ = []
    for drv in drivers:
        try:
            range_.append(fastf1.plotting.get_driver_color(drv, session_obj))
        except Exception as exc:
            logger.debug(f"get_driver_color failed for {drv!r}: {exc}")
            range_.append(_FALLBACK_COLOR)
    return {"domain": list(drivers), "range": range_}


def team_color_scale(teams: list[str], session_obj: Any) -> dict:
    """Build a Vega-Lite ``{"domain": [...], "range": [...]}`` colour scale
    from ``fastf1.plotting.get_team_color()`` for the given team names."""
    range_ = []
    for team in teams:
        try:
            range_.append(fastf1.plotting.get_team_color(team, session_obj))
        except Exception as exc:
            logger.debug(f"get_team_color failed for {team!r}: {exc}")
            range_.append(_FALLBACK_COLOR)
    return {"domain": list(teams), "range": range_}


def spec_to_png_b64(spec: dict, scale: float = 2.0) -> str | None:
    """Render a Vega-Lite spec to a PNG and return a ``data:image/png;base64,``
    URI suitable for Markdown image embedding.

    Uses ``vl-convert-python`` (Rust-backed, no Node.js required).  Returns
    ``None`` if the package is not installed or rendering fails — callers
    should treat ``None`` as "PNG unavailable" and fall back to ``html``.
    ``scale=2`` gives a 2× resolution image (good for Retina displays).
    """
    if not _VL_CONVERT_AVAILABLE or _vlc is None:
        return None
    try:
        png_bytes: bytes = _vlc.vegalite_to_png(vl_spec=spec, scale=scale)
        return "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    except Exception as exc:
        logger.debug(f"spec_to_png_b64 failed: {exc}")
        return None


def spec_to_html(spec: dict, title: str = "Chart") -> str:
    """Return a complete, self-contained HTML page that renders ``spec``.

    The spec dict is serialised with ``json.dumps`` and inlined directly into
    the ``<script>`` block as a JS object literal — no model manipulation
    required.  ``</script>`` inside string values is escaped to prevent the
    HTML parser from closing the block prematurely.
    """
    spec_json = json.dumps(spec, ensure_ascii=False).replace("</script>", r"<\/script>")
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<!doctype html>\n"
        "<html>\n"
        "<head>\n"
        f'  <meta charset="utf-8" />\n'
        f"  <title>{safe_title}</title>\n"
        '  <script src="https://cdn.jsdelivr.net/npm/vega@5"></script>\n'
        '  <script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>\n'
        '  <script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>\n'
        "  <style>body{margin:0;padding:1rem;font-family:system-ui,sans-serif}</style>\n"
        "</head>\n"
        "<body>\n"
        '  <div id="chart"></div>\n'
        "  <script>\n"
        f"    vegaEmbed('#chart', {spec_json}, {{actions: false}});\n"
        "  </script>\n"
        "</body>\n"
        "</html>"
    )


def compound_color_scale(compounds: list[str] | None = None) -> dict:
    """Build a Vega-Lite colour scale from ``COMPOUND_COLOR_SCALE``.

    Pass a list of compounds present in the data to scope the domain to only
    those values (keeps the legend tight). With ``None``, all six compounds
    are included.
    """
    if compounds is None:
        keys = list(COMPOUND_COLOR_SCALE.keys())
    else:
        keys = list(compounds)
    return {
        "domain": keys,
        "range": [COMPOUND_COLOR_SCALE.get(c, _FALLBACK_COLOR) for c in keys],
    }
