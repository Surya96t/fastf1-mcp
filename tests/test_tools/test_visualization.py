"""Tests for tools/visualization.py — Vega-Lite chart tools."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from fastf1_mcp.tools.visualization import (
    get_lap_time_chart,
    get_pace_delta_chart,
    get_tyre_strategy_chart,
)
from fastf1_mcp.utils.errors import ErrorCode
from fastf1_mcp.utils.vega_lite import RENDERING_INSTRUCTIONS
from tests._helpers import FakeLaps

try:
    import vl_convert as _vlc
except ImportError:  # pragma: no cover
    _vlc = None


def _assert_compiles_to_vega(spec: dict) -> None:
    """Compile the spec through vl-convert — a real Vega-Lite v5 validation.

    Structural key checks miss malformed encodings, bad mark types, and
    invalid channel definitions; the vega-lite compiler catches them. Skips
    when vl-convert is unavailable so the suite still runs without it.
    """
    if _vlc is None:  # pragma: no cover
        pytest.skip("vl-convert not installed")
    # Raises ValueError on an invalid Vega-Lite v5 spec.
    _vlc.vegalite_to_vega(vl_spec=spec)


# PNG rendering is slow (~1 s) and requires vl-convert; mock it in unit tests.
_FAKE_PNG = "data:image/png;base64,iVBORw0KGgo="
_patch_png = patch(
    "fastf1_mcp.tools.visualization.spec_to_png_b64",
    return_value=_FAKE_PNG,
)


@pytest.fixture(autouse=True)
def _no_side_effects():
    """Prevent actual vl-convert rendering and browser opens in every test."""
    with (
        _patch_png,
        patch(
            "fastf1_mcp.tools.visualization._open_chart_in_browser",
            return_value="/tmp/fastf1_chart_test.html",
        ),
    ):
        yield


def _patch_session(mock_session):
    return patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=mock_session,
    )


def _td(s: float) -> pd.Timedelta:
    return pd.Timedelta(seconds=s)


def _make_stint_session(
    laps_df: FakeLaps,
    drivers: list[str],
    results_df: pd.DataFrame | None = None,
) -> MagicMock:
    session = MagicMock()
    session.drivers = drivers
    session.laps = laps_df
    session.results = results_df if results_df is not None else pd.DataFrame()
    return session


# ---------------------------------------------------------------------------
# get_pace_delta_chart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pace_delta_chart_response_shape(mock_session):
    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            return_value={"domain": ["Red Bull", "Ferrari"], "range": ["#1", "#2"]},
        ),
    ):
        result = await get_pace_delta_chart(2024, "Monaco")

    assert set(result.keys()) == {
        "vegaLiteSpec",
        "html",
        "png",
        "htmlPath",
        "title",
        "rowCount",
        "renderingInstructions",
    }
    assert result["rowCount"] == len(result["vegaLiteSpec"]["data"]["values"])


@pytest.mark.asyncio
async def test_pace_delta_chart_spec_is_valid_v5(mock_session):
    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            return_value={"domain": [], "range": []},
        ),
    ):
        result = await get_pace_delta_chart(2024, "Monaco")

    spec = result["vegaLiteSpec"]
    assert spec["$schema"].endswith("vega-lite/v5.json")
    _assert_compiles_to_vega(spec)
    # Required structural keys.
    for key in ("title", "data", "mark", "encoding", "width", "height"):
        assert key in spec, f"missing required spec key: {key}"
    # Required encoding channels for a horizontal bar.
    for channel in ("x", "y", "color", "tooltip"):
        assert channel in spec["encoding"], f"missing encoding channel: {channel}"


@pytest.mark.asyncio
async def test_pace_delta_chart_auto_title(mock_session):
    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            return_value={"domain": [], "range": []},
        ),
    ):
        result = await get_pace_delta_chart(2024, "Monaco")

    expected = "2024 Monaco — Race pace delta (fastest = 0s)"
    assert result["title"] == expected
    assert result["vegaLiteSpec"]["title"] == expected


@pytest.mark.asyncio
async def test_pace_delta_chart_custom_title_overrides_auto(mock_session):
    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            return_value={"domain": [], "range": []},
        ),
    ):
        result = await get_pace_delta_chart(2024, "Monaco", title="Custom")

    assert result["title"] == "Custom"
    assert result["vegaLiteSpec"]["title"] == "Custom"


@pytest.mark.asyncio
async def test_pace_delta_chart_y_sorted_by_delta(mock_session):
    """The y-axis must be sorted by deltaToFastestSec ascending so the
    fastest driver sits at the top of the bar chart."""
    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            return_value={"domain": [], "range": []},
        ),
    ):
        result = await get_pace_delta_chart(2024, "Monaco")

    y_enc = result["vegaLiteSpec"]["encoding"]["y"]
    assert y_enc["field"] == "driver"
    assert y_enc["sort"] == {"field": "deltaToFastestSec", "order": "ascending"}


@pytest.mark.asyncio
async def test_pace_delta_chart_color_scale_built_from_teams_in_data(mock_session):
    """team_color_scale should be called with the unique team names that
    actually appear in the pace data (in appearance order, fastest-first)."""
    captured: dict = {}

    def _capture(teams, session_obj):
        captured["teams"] = list(teams)
        return {"domain": list(teams), "range": ["#abc"] * len(teams)}

    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            side_effect=_capture,
        ),
    ):
        await get_pace_delta_chart(2024, "Monaco")

    # mock_session has three drivers across "Red Bull" + "Ferrari" (twice).
    # Order should reflect appearance in pace_data (sorted by pace).
    assert "Red Bull" in captured["teams"]
    assert "Ferrari" in captured["teams"]
    # Each team listed once — no duplicates.
    assert len(captured["teams"]) == len(set(captured["teams"]))


@pytest.mark.asyncio
async def test_pace_delta_chart_data_rows_match_pace_data(mock_session):
    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            return_value={"domain": [], "range": []},
        ),
    ):
        result = await get_pace_delta_chart(2024, "Monaco")

    rows = result["vegaLiteSpec"]["data"]["values"]
    assert len(rows) > 0
    # First row is fastest -> delta 0.
    assert rows[0]["deltaToFastestSec"] == 0.0
    # Required fields present on every row.
    for row in rows:
        for k in ("driver", "teamName", "deltaToFastestSec", "avgLapTime", "lapCount"):
            assert k in row


@pytest.mark.asyncio
async def test_pace_delta_chart_response_is_json_serializable(mock_session):
    """Whole response (including the spec) must round-trip through json so
    no numpy/Timedelta values leak into the wire format."""
    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            return_value={"domain": [], "range": []},
        ),
    ):
        result = await get_pace_delta_chart(2024, "Monaco")

    encoded = json.dumps(result)
    assert "vegaLiteSpec" in encoded


@pytest.mark.asyncio
async def test_pace_delta_chart_pre_2018_returns_error_dict():
    """Bad year goes through the tool_handler decorator and returns the
    standard error dict — never raises."""
    result = await get_pace_delta_chart(2017, "Monaco")

    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result["code"] == ErrorCode.YEAR_OUT_OF_RANGE.value


# ---------------------------------------------------------------------------
# get_tyre_strategy_chart
# ---------------------------------------------------------------------------


def _two_driver_stint_session() -> MagicMock:
    """VER (P1, 2-stop MED→HARD→SOFT) vs LEC (P2, 1-stop MED→HARD)."""
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 6 + ["LEC"] * 6,
            "LapNumber": [1, 2, 3, 4, 5, 6] * 2,
            "Stint": [1.0, 1.0, 2.0, 2.0, 3.0, 3.0] + [1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
            "LapTime": [_td(90)] * 12,
            "Compound": ["MEDIUM", "MEDIUM", "HARD", "HARD", "SOFT", "SOFT"]
            + ["MEDIUM"] * 3
            + ["HARD"] * 3,
            "IsAccurate": [True] * 12,
        }
    )
    results = pd.DataFrame(
        {
            "Position": [1.0, 2.0],
            "Abbreviation": ["VER", "LEC"],
            "DriverNumber": ["1", "16"],
            "FullName": ["Max Verstappen", "Charles Leclerc"],
            "TeamName": ["Red Bull", "Ferrari"],
            "GridPosition": [1.0, 2.0],
            "Status": ["Finished", "Finished"],
            "Points": [25.0, 18.0],
            "Time": [pd.Timedelta("1:30:00"), pd.Timedelta(seconds=5)],
        }
    )
    return _make_stint_session(laps, ["VER", "LEC"], results)


@pytest.mark.asyncio
async def test_tyre_strategy_chart_spec_is_valid_v5():
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco")

    spec = result["vegaLiteSpec"]
    assert spec["$schema"].endswith("vega-lite/v5.json")
    _assert_compiles_to_vega(spec)
    for key in ("title", "data", "mark", "encoding", "width", "height"):
        assert key in spec
    # Gantt requires x + x2.
    for ch in ("x", "x2", "y", "color", "tooltip"):
        assert ch in spec["encoding"]
    assert spec["encoding"]["x2"]["field"] == "endLap"


@pytest.mark.asyncio
async def test_tyre_strategy_chart_auto_title_and_response_shape():
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco")

    assert set(result.keys()) == {
        "vegaLiteSpec",
        "html",
        "png",
        "htmlPath",
        "title",
        "rowCount",
        "renderingInstructions",
    }
    assert result["title"] == "2024 Monaco — Tyre strategies"
    assert result["rowCount"] == len(result["vegaLiteSpec"]["data"]["values"])


@pytest.mark.asyncio
async def test_tyre_strategy_chart_custom_title_overrides_auto():
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco", title="Strategy view")

    assert result["title"] == "Strategy view"
    assert result["vegaLiteSpec"]["title"] == "Strategy view"


@pytest.mark.asyncio
async def test_tyre_strategy_chart_y_sorted_by_finishing_position():
    """P1 sits at the top of the chart — y-sort is an explicit array matching
    session.results order."""
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco")

    y_sort = result["vegaLiteSpec"]["encoding"]["y"]["sort"]
    assert y_sort == ["VER", "LEC"]


@pytest.mark.asyncio
async def test_tyre_strategy_chart_color_scale_scoped_to_compounds_in_data():
    """The compound colour-scale domain should only list compounds that
    actually appear in the data — keeps the legend tight."""
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco")

    scale = result["vegaLiteSpec"]["encoding"]["color"]["scale"]
    assert set(scale["domain"]) == {"MEDIUM", "HARD", "SOFT"}
    assert "INTERMEDIATE" not in scale["domain"]
    assert "WET" not in scale["domain"]


@pytest.mark.asyncio
async def test_tyre_strategy_chart_data_rows_carry_required_fields():
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco")

    rows = result["vegaLiteSpec"]["data"]["values"]
    assert len(rows) == 5  # 3 stints VER + 2 stints LEC
    for row in rows:
        for k in ("driver", "stintNumber", "compound", "startLap", "endLap"):
            assert k in row
    # VER's first stint covers laps 1-2 with MEDIUM.
    ver_stint1 = next(r for r in rows if r["driver"] == "VER" and r["stintNumber"] == 1)
    assert ver_stint1["startLap"] == 1
    assert ver_stint1["endLap"] == 2
    assert ver_stint1["compound"] == "MEDIUM"


@pytest.mark.asyncio
async def test_tyre_strategy_chart_single_driver_filter():
    """Passing a driver narrows the chart to that driver's stints only."""
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco", driver="VER")

    rows = result["vegaLiteSpec"]["data"]["values"]
    assert {r["driver"] for r in rows} == {"VER"}
    assert result["vegaLiteSpec"]["encoding"]["y"]["sort"] == ["VER"]


@pytest.mark.asyncio
async def test_tyre_strategy_chart_response_is_json_serializable():
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco")

    encoded = json.dumps(result)
    assert "vegaLiteSpec" in encoded


@pytest.mark.asyncio
async def test_tyre_strategy_chart_pre_2018_returns_error_dict():
    result = await get_tyre_strategy_chart(2017, "Monaco")
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result["code"] == ErrorCode.YEAR_OUT_OF_RANGE.value


@pytest.mark.asyncio
async def test_tyre_strategy_chart_unknown_driver_error():
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco", driver="ZZZ")
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result["code"] == ErrorCode.DRIVER_NOT_FOUND.value


# ---------------------------------------------------------------------------
# get_lap_time_chart
# ---------------------------------------------------------------------------


def _lap_time_chart_session(
    *,
    with_track_status: bool = True,
    with_pit_columns: bool = True,
) -> MagicMock:
    """Build a session with 6 laps for VER:
    - lap 1: pit out (PitOutTime set)        → outlier
    - lap 2: clean MEDIUM
    - lap 3: clean MEDIUM
    - lap 4: SC lap (TrackStatus != "1")     → outlier
    - lap 5: clean HARD
    - lap 6: pit in (PitInTime set)          → outlier
    """
    data: dict = {
        "Driver": ["VER"] * 6,
        "LapNumber": [1, 2, 3, 4, 5, 6],
        "LapTime": [
            _td(95.0),
            _td(90.5),
            _td(90.3),
            _td(105.0),
            _td(91.0),
            _td(94.0),
        ],
        "Compound": ["MEDIUM"] * 3 + ["MEDIUM", "HARD", "HARD"],
        "TyreLife": [1, 2, 3, 4, 1, 2],
        "Deleted": [False] * 6,
    }
    if with_track_status:
        data["TrackStatus"] = ["1", "1", "1", "4", "1", "1"]
    if with_pit_columns:
        data["PitInTime"] = [pd.NaT, pd.NaT, pd.NaT, pd.NaT, pd.NaT, _td(0)]
        data["PitOutTime"] = [_td(0), pd.NaT, pd.NaT, pd.NaT, pd.NaT, pd.NaT]
    laps = FakeLaps(data)
    results = pd.DataFrame(
        {
            "Position": [1.0],
            "Abbreviation": ["VER"],
            "DriverNumber": ["1"],
            "FullName": ["Max Verstappen"],
            "TeamName": ["Red Bull"],
        }
    )
    return _make_stint_session(laps, ["VER"], results)


@pytest.mark.asyncio
async def test_lap_time_chart_spec_is_valid_v5():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER")

    spec = result["vegaLiteSpec"]
    assert spec["$schema"].endswith("vega-lite/v5.json")
    _assert_compiles_to_vega(spec)
    for key in ("title", "data", "mark", "encoding", "width", "height"):
        assert key in spec
    for channel in ("x", "y", "color", "tooltip"):
        assert channel in spec["encoding"]
    # Line + point overlay with tooltip enabled.
    assert spec["mark"]["type"] == "line"
    assert spec["mark"]["point"] is True
    # y-axis must NOT include zero (keeps the range tight around real pace).
    assert spec["encoding"]["y"]["scale"] == {"zero": False}


@pytest.mark.asyncio
async def test_lap_time_chart_auto_title_uses_driver_code():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER")

    assert result["title"] == "2024 Miami R — VER lap times"
    assert result["vegaLiteSpec"]["title"] == "2024 Miami R — VER lap times"


@pytest.mark.asyncio
async def test_lap_time_chart_custom_title_overrides_auto():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER", title="Pace trace")

    assert result["title"] == "Pace trace"


@pytest.mark.asyncio
async def test_lap_time_chart_default_drops_pit_and_sc_laps():
    """include_outliers=False (default): pit-in lap, pit-out lap, and the
    SC lap (TrackStatus != "1") should all be absent from data.values."""
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER")

    laps_in_chart = [r["lapNumber"] for r in result["vegaLiteSpec"]["data"]["values"]]
    # Pit-out (1), SC (4), pit-in (6) are dropped → only 2, 3, 5 remain.
    assert laps_in_chart == [2, 3, 5]
    assert result["rowCount"] == 3


@pytest.mark.asyncio
async def test_lap_time_chart_include_outliers_keeps_all_laps():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(
            2024, "Miami", "R", "VER", include_outliers=True
        )

    laps_in_chart = [r["lapNumber"] for r in result["vegaLiteSpec"]["data"]["values"]]
    assert laps_in_chart == [1, 2, 3, 4, 5, 6]
    assert result["rowCount"] == 6


@pytest.mark.asyncio
async def test_lap_time_chart_rows_carry_required_fields():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER")

    rows = result["vegaLiteSpec"]["data"]["values"]
    for row in rows:
        for k in ("lapNumber", "lapTimeSec", "lapTimeStr", "compound", "tyreLife"):
            assert k in row
        # lapTimeSec is numeric, lapTimeStr is the formatted source string.
        assert isinstance(row["lapTimeSec"], (int, float))
        assert isinstance(row["lapTimeStr"], str)
    # Lap 2 was 90.5s.
    lap2 = next(r for r in rows if r["lapNumber"] == 2)
    assert lap2["lapTimeSec"] == 90.5


@pytest.mark.asyncio
async def test_lap_time_chart_y_axis_uses_time_label_expr():
    """Y-axis tick labels must be formatted as M:SS.mmm, not raw seconds."""
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER")

    y_enc = result["vegaLiteSpec"]["encoding"]["y"]
    assert "axis" in y_enc
    label_expr = y_enc["axis"].get("labelExpr", "")
    # Must include minute extraction and zero-pad logic.
    assert "floor(datum.value / 60)" in label_expr
    assert "datum.value % 60" in label_expr
    assert ".3f" in label_expr


@pytest.mark.asyncio
async def test_lap_time_chart_compound_palette_scoped_to_data():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER")

    scale = result["vegaLiteSpec"]["encoding"]["color"]["scale"]
    assert set(scale["domain"]) == {"MEDIUM", "HARD"}
    assert "INTERMEDIATE" not in scale["domain"]
    assert "SOFT" not in scale["domain"]


@pytest.mark.asyncio
async def test_lap_time_chart_response_is_json_serializable():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER")

    encoded = json.dumps(result)
    assert "vegaLiteSpec" in encoded


@pytest.mark.asyncio
async def test_lap_time_chart_pre_2018_returns_error_dict():
    result = await get_lap_time_chart(2017, "Miami", "R", "VER")
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result["code"] == ErrorCode.YEAR_OUT_OF_RANGE.value


@pytest.mark.asyncio
async def test_lap_time_chart_unknown_driver_returns_error_dict():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "ZZZ")
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result["code"] == ErrorCode.DRIVER_NOT_FOUND.value


# ---------------------------------------------------------------------------
# renderingInstructions — present on every chart tool's response, points the
# model at the HTML/vega-embed template so the spec actually renders.
# ---------------------------------------------------------------------------


def test_rendering_instructions_mentions_browser_and_warns():
    """The constant must tell the model the chart was opened in the browser
    and warn against inventing custom artifact tags like <anartifact>."""
    assert "browser" in RENDERING_INSTRUCTIONS
    assert "html" in RENDERING_INSTRUCTIONS
    assert "<anartifact>" in RENDERING_INSTRUCTIONS


@pytest.mark.asyncio
async def test_pace_delta_chart_includes_rendering_instructions(mock_session):
    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            return_value={"domain": [], "range": []},
        ),
    ):
        result = await get_pace_delta_chart(2024, "Monaco")
    assert result["renderingInstructions"] == RENDERING_INSTRUCTIONS


@pytest.mark.asyncio
async def test_tyre_strategy_chart_includes_rendering_instructions():
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco")
    assert result["renderingInstructions"] == RENDERING_INSTRUCTIONS


@pytest.mark.asyncio
async def test_lap_time_chart_includes_rendering_instructions():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER")
    assert result["renderingInstructions"] == RENDERING_INSTRUCTIONS


# ---------------------------------------------------------------------------
# html field — pre-built self-contained HTML page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pace_delta_chart_html_is_valid_embed_page(mock_session):
    with (
        _patch_session(mock_session),
        patch(
            "fastf1_mcp.tools.visualization.team_color_scale",
            return_value={"domain": [], "range": []},
        ),
    ):
        result = await get_pace_delta_chart(2024, "Monaco")

    html = result["html"]
    assert "<!doctype html>" in html
    assert "cdn.jsdelivr.net/npm/vega@5" in html
    assert "vegaEmbed(" in html
    assert "$schema" in html  # spec is inlined
    assert result["title"] in html  # title tag uses the chart title


@pytest.mark.asyncio
async def test_tyre_strategy_chart_html_is_valid_embed_page():
    session = _two_driver_stint_session()
    with _patch_session(session):
        result = await get_tyre_strategy_chart(2024, "Monaco")

    html = result["html"]
    assert "<!doctype html>" in html
    assert "vegaEmbed(" in html
    assert "$schema" in html


@pytest.mark.asyncio
async def test_lap_time_chart_html_is_valid_embed_page():
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(2024, "Miami", "R", "VER")

    html = result["html"]
    assert "<!doctype html>" in html
    assert "vegaEmbed(" in html
    assert "$schema" in html


@pytest.mark.asyncio
async def test_lap_time_chart_lapTimeStr_is_formatted():
    """lapTimeStr should be M:SS.mmm (e.g. '1:30.500'), not the raw pandas
    Timedelta repr '0 days 00:01:30.500000'."""
    session = _lap_time_chart_session()
    with _patch_session(session):
        result = await get_lap_time_chart(
            2024, "Miami", "R", "VER", include_outliers=True
        )

    rows = result["vegaLiteSpec"]["data"]["values"]
    for row in rows:
        if row["lapTimeStr"] is not None:
            assert "days" not in row["lapTimeStr"], (
                f"lapTimeStr should not contain 'days': {row['lapTimeStr']!r}"
            )
            assert ":" in row["lapTimeStr"]


# ---------------------------------------------------------------------------
# get_position_chart
# ---------------------------------------------------------------------------


def _position_chart_session() -> MagicMock:
    """Two drivers, 4 laps each, plus an extra NaN-position lap for VER."""
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 5 + ["LEC"] * 4,
            "LapNumber": [1, 2, 3, 4, 5] + [1, 2, 3, 4],
            # VER's first lap has no recorded position (formation-lap artefact)
            "Position": [float("nan"), 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0],
            "LapTime": [pd.Timedelta(seconds=90)] * 9,
        }
    )
    results = pd.DataFrame(
        {
            "Position": [1.0, 2.0],
            "Abbreviation": ["VER", "LEC"],
            "DriverNumber": ["1", "16"],
            "FullName": ["Max Verstappen", "Charles Leclerc"],
            "TeamName": ["Red Bull", "Ferrari"],
        }
    )
    return _make_stint_session(laps, ["VER", "LEC"], results)


@pytest.mark.asyncio
async def test_position_chart_spec_is_valid_v5():
    from fastf1_mcp.tools.visualization import get_position_chart

    session = _position_chart_session()
    with _patch_session(session):
        result = await get_position_chart(2024, "Monaco")

    spec = result["vegaLiteSpec"]
    assert spec["$schema"].endswith("vega-lite/v5.json")
    _assert_compiles_to_vega(spec)
    for key in ("title", "data", "mark", "encoding", "width", "height"):
        assert key in spec
    for channel in ("x", "y", "color", "tooltip"):
        assert channel in spec["encoding"]
    assert spec["mark"]["type"] == "line"
    assert spec["mark"]["point"] is False


@pytest.mark.asyncio
async def test_position_chart_auto_title_and_response_shape():
    from fastf1_mcp.tools.visualization import get_position_chart

    session = _position_chart_session()
    with _patch_session(session):
        result = await get_position_chart(2024, "Monaco")

    assert set(result.keys()) == {
        "vegaLiteSpec",
        "html",
        "png",
        "htmlPath",
        "title",
        "rowCount",
        "renderingInstructions",
    }
    assert result["title"] == "2024 Monaco — Race positions"
    assert result["rowCount"] == len(result["vegaLiteSpec"]["data"]["values"])


@pytest.mark.asyncio
async def test_position_chart_y_axis_reversed_for_p1_on_top():
    """Position 1 must render at the top of the chart — scale.reverse=True."""
    from fastf1_mcp.tools.visualization import get_position_chart

    session = _position_chart_session()
    with _patch_session(session):
        result = await get_position_chart(2024, "Monaco")

    y_enc = result["vegaLiteSpec"]["encoding"]["y"]
    assert y_enc["field"] == "position"
    assert y_enc["scale"] == {"reverse": True}
    # Integer axis ticks (no decimals).
    assert y_enc["axis"]["tickMinStep"] == 1
    assert y_enc["axis"]["format"] == "d"


@pytest.mark.asyncio
async def test_position_chart_drops_nan_position_rows():
    """VER's lap-1 NaN position must be dropped from the chart data."""
    from fastf1_mcp.tools.visualization import get_position_chart

    session = _position_chart_session()
    with _patch_session(session):
        result = await get_position_chart(2024, "Monaco")

    rows = result["vegaLiteSpec"]["data"]["values"]
    ver_rows = [r for r in rows if r["driver"] == "VER"]
    # 5 raw laps for VER, but lap 1 has NaN → 4 in the chart.
    assert len(ver_rows) == 4
    assert all(r["lapNumber"] != 1 for r in ver_rows)


@pytest.mark.asyncio
async def test_position_chart_drivers_filter():
    """Passing a drivers list restricts the chart to those codes."""
    from fastf1_mcp.tools.visualization import get_position_chart

    session = _position_chart_session()
    with _patch_session(session):
        result = await get_position_chart(2024, "Monaco", drivers=["LEC"])

    rows = result["vegaLiteSpec"]["data"]["values"]
    assert {r["driver"] for r in rows} == {"LEC"}


@pytest.mark.asyncio
async def test_position_chart_response_is_json_serializable():
    from fastf1_mcp.tools.visualization import get_position_chart

    session = _position_chart_session()
    with _patch_session(session):
        result = await get_position_chart(2024, "Monaco")

    encoded = json.dumps(result)
    assert "vegaLiteSpec" in encoded


@pytest.mark.asyncio
async def test_position_chart_pre_2018_returns_error_dict():
    from fastf1_mcp.tools.visualization import get_position_chart

    result = await get_position_chart(2017, "Monaco")
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result["code"] == ErrorCode.YEAR_OUT_OF_RANGE.value


@pytest.mark.asyncio
async def test_position_chart_includes_rendering_instructions():
    from fastf1_mcp.tools.visualization import get_position_chart

    session = _position_chart_session()
    with _patch_session(session):
        result = await get_position_chart(2024, "Monaco")
    assert result["renderingInstructions"] == RENDERING_INSTRUCTIONS


# ---------------------------------------------------------------------------
# get_speed_trace_chart
# ---------------------------------------------------------------------------


def _patch_telemetry_session(mock_session):
    return patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session_with_telemetry",
        new_callable=AsyncMock,
        return_value=mock_session,
    )


@pytest.mark.asyncio
async def test_speed_trace_chart_spec_is_valid_v5(mock_session):
    from fastf1_mcp.tools.visualization import get_speed_trace_chart

    with _patch_telemetry_session(mock_session):
        result = await get_speed_trace_chart(2024, "Monaco", "Q", "VER", "LEC")

    spec = result["vegaLiteSpec"]
    assert spec["$schema"].endswith("vega-lite/v5.json")
    _assert_compiles_to_vega(spec)
    for key in ("title", "data", "mark", "encoding", "width", "height"):
        assert key in spec
    for channel in ("x", "y", "color", "tooltip"):
        assert channel in spec["encoding"]
    assert spec["mark"]["type"] == "line"
    # Speed scale must not include zero (keeps the range tight).
    assert spec["encoding"]["y"]["scale"] == {"zero": False}


@pytest.mark.asyncio
async def test_speed_trace_chart_auto_title_and_response_shape(mock_session):
    from fastf1_mcp.tools.visualization import get_speed_trace_chart

    with _patch_telemetry_session(mock_session):
        result = await get_speed_trace_chart(
            2024, "Monaco", "Q", "VER", "LEC", lap="fastest"
        )

    assert set(result.keys()) == {
        "vegaLiteSpec",
        "html",
        "png",
        "htmlPath",
        "title",
        "rowCount",
        "renderingInstructions",
    }
    assert result["title"] == "2024 Monaco Q — VER vs LEC (lap fastest)"


@pytest.mark.asyncio
async def test_speed_trace_chart_long_format_rows(mock_session):
    """Each distance sample yields two rows — one per driver — and rowCount
    equals 2 x sample_size (default 200)."""
    from fastf1_mcp.tools.visualization import get_speed_trace_chart

    with _patch_telemetry_session(mock_session):
        result = await get_speed_trace_chart(2024, "Monaco", "Q", "VER", "LEC")

    rows = result["vegaLiteSpec"]["data"]["values"]
    assert len(rows) == 400
    assert result["rowCount"] == 400
    drivers_in_rows = {r["driver"] for r in rows}
    assert drivers_in_rows == {"VER", "LEC"}
    for row in rows:
        for k in ("distance", "driver", "speed"):
            assert k in row


@pytest.mark.asyncio
async def test_speed_trace_chart_response_is_json_serializable(mock_session):
    from fastf1_mcp.tools.visualization import get_speed_trace_chart

    with _patch_telemetry_session(mock_session):
        result = await get_speed_trace_chart(2024, "Monaco", "Q", "VER", "LEC")

    encoded = json.dumps(result)
    assert "vegaLiteSpec" in encoded


@pytest.mark.asyncio
async def test_speed_trace_chart_pre_2018_returns_error_dict():
    from fastf1_mcp.tools.visualization import get_speed_trace_chart

    result = await get_speed_trace_chart(2017, "Monaco", "Q", "VER", "LEC")
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result["code"] == ErrorCode.YEAR_OUT_OF_RANGE.value


@pytest.mark.asyncio
async def test_speed_trace_chart_includes_rendering_instructions(mock_session):
    from fastf1_mcp.tools.visualization import get_speed_trace_chart

    with _patch_telemetry_session(mock_session):
        result = await get_speed_trace_chart(2024, "Monaco", "Q", "VER", "LEC")
    assert result["renderingInstructions"] == RENDERING_INSTRUCTIONS
