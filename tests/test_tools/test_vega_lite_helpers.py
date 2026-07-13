"""Unit tests for utils/vega_lite.py and the _compute_race_pace refactor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastf1_mcp.tools.session import (
    _compute_lap_times,
    _compute_race_pace,
    _compute_stints,
    get_lap_times,
    get_race_pace,
    get_stint_analysis,
)
from fastf1_mcp.tools.visualization import _filter_clean_laps
from fastf1_mcp.utils.vega_lite import (
    COMPOUND_COLOR_SCALE,
    VEGA_LITE_SCHEMA,
    base_spec,
    compound_color_scale,
    driver_color_scale,
    spec_to_html,
    spec_to_png_b64,
    team_color_scale,
)


# ---------------------------------------------------------------------------
# base_spec
# ---------------------------------------------------------------------------


def test_spec_to_png_b64_returns_data_uri():
    """spec_to_png_b64 returns a data URI when vl-convert is available."""
    fake_bytes = b"\x89PNG fake"
    import base64

    expected_b64 = base64.b64encode(fake_bytes).decode()
    with (
        patch("fastf1_mcp.utils.vega_lite._vlc") as mock_vlc,
        patch("fastf1_mcp.utils.vega_lite._VL_CONVERT_AVAILABLE", True),
    ):
        mock_vlc.vegalite_to_png.return_value = fake_bytes
        result = spec_to_png_b64({"$schema": VEGA_LITE_SCHEMA}, scale=2.0)

    assert result is not None
    assert result.startswith("data:image/png;base64,")
    assert expected_b64 in result
    mock_vlc.vegalite_to_png.assert_called_once()


def test_spec_to_png_b64_returns_none_when_vl_convert_unavailable():
    with patch("fastf1_mcp.utils.vega_lite._VL_CONVERT_AVAILABLE", False):
        result = spec_to_png_b64({"$schema": VEGA_LITE_SCHEMA})
    assert result is None


def test_spec_to_png_b64_returns_none_on_render_failure():
    with (
        patch("fastf1_mcp.utils.vega_lite._vlc") as mock_vlc,
        patch("fastf1_mcp.utils.vega_lite._VL_CONVERT_AVAILABLE", True),
    ):
        mock_vlc.vegalite_to_png.side_effect = RuntimeError("render error")
        result = spec_to_png_b64({"$schema": VEGA_LITE_SCHEMA})
    assert result is None


def test_spec_to_html_produces_valid_embed_page():
    spec = {"$schema": VEGA_LITE_SCHEMA, "mark": "bar", "data": {"values": []}}
    html = spec_to_html(spec, "Test Chart")
    assert "<!doctype html>" in html
    assert "Test Chart" in html
    assert "cdn.jsdelivr.net/npm/vega@5" in html
    assert "cdn.jsdelivr.net/npm/vega-lite@5" in html
    assert "cdn.jsdelivr.net/npm/vega-embed@6" in html
    assert "vegaEmbed(" in html
    assert VEGA_LITE_SCHEMA in html  # spec inlined


def test_spec_to_html_escapes_script_close_tag():
    """A spec whose string values contain '</script>' must not break the HTML."""
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "title": "ok",
        "note": "</script><script>alert(1)</script>",
    }
    html = spec_to_html(spec, "Safe")
    # The raw closing tag must not appear unescaped inside the script block.
    assert "</script><script>" not in html
    # The escape sequence should be present instead.
    assert r"<\/script>" in html


def test_spec_to_html_escapes_title_html():
    spec = {"$schema": VEGA_LITE_SCHEMA}
    html = spec_to_html(spec, "<b>Title & stuff</b>")
    assert "<b>" not in html
    assert "&lt;b&gt;" in html
    assert "&amp;" in html


def test_base_spec_has_required_keys():
    spec = base_spec("My title", mark="line")
    assert spec["$schema"] == VEGA_LITE_SCHEMA
    assert spec["$schema"].endswith("vega-lite/v5.json")
    assert spec["title"] == "My title"
    assert spec["mark"] == "line"
    assert spec["width"] == 700
    assert spec["height"] == 400


def test_base_spec_accepts_dict_mark_and_custom_dims():
    spec = base_spec(
        "x",
        mark={"type": "line", "point": True},
        width=900,
        height=300,
    )
    assert spec["mark"] == {"type": "line", "point": True}
    assert spec["width"] == 900
    assert spec["height"] == 300


# ---------------------------------------------------------------------------
# COMPOUND_COLOR_SCALE
# ---------------------------------------------------------------------------


def test_compound_color_scale_has_all_six_compounds():
    expected = {"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", "UNKNOWN"}
    assert set(COMPOUND_COLOR_SCALE.keys()) == expected
    # All values are hex colour strings.
    for v in COMPOUND_COLOR_SCALE.values():
        assert v.startswith("#") and len(v) == 7


def test_compound_color_scale_hard_not_pure_white():
    # The whole reason this dict exists vs deferring to fastf1: HARD must be
    # visible on a white background. Documented in the module docstring.
    assert COMPOUND_COLOR_SCALE["HARD"].upper() != "#FFFFFF"


def test_compound_color_scale_helper_full_domain():
    scale = compound_color_scale()
    assert list(scale["domain"]) == list(COMPOUND_COLOR_SCALE.keys())
    assert list(scale["range"]) == list(COMPOUND_COLOR_SCALE.values())


def test_compound_color_scale_helper_scoped_domain():
    scale = compound_color_scale(["SOFT", "HARD"])
    assert scale["domain"] == ["SOFT", "HARD"]
    assert scale["range"] == [
        COMPOUND_COLOR_SCALE["SOFT"],
        COMPOUND_COLOR_SCALE["HARD"],
    ]


# ---------------------------------------------------------------------------
# driver_color_scale / team_color_scale
# ---------------------------------------------------------------------------


def test_driver_color_scale_domain_matches_input_order():
    fake_session = MagicMock()
    with patch(
        "fastf1_mcp.utils.vega_lite.fastf1.plotting.get_driver_color",
        side_effect=lambda d, s: {"VER": "#0000ff", "LEC": "#ff0000"}[d],
    ):
        scale = driver_color_scale(["VER", "LEC"], fake_session)

    assert scale["domain"] == ["VER", "LEC"]
    assert scale["range"] == ["#0000ff", "#ff0000"]


def test_driver_color_scale_falls_back_on_unknown_driver():
    fake_session = MagicMock()
    with patch(
        "fastf1_mcp.utils.vega_lite.fastf1.plotting.get_driver_color",
        side_effect=ValueError("unknown driver"),
    ):
        scale = driver_color_scale(["XYZ"], fake_session)

    assert scale["domain"] == ["XYZ"]
    # Fallback is a neutral grey so the chart still renders.
    assert scale["range"][0] == "#888888"


def test_team_color_scale_domain_matches_input_order():
    fake_session = MagicMock()
    with patch(
        "fastf1_mcp.utils.vega_lite.fastf1.plotting.get_team_color",
        side_effect=lambda t, s: {"Red Bull": "#1e5bc6", "Ferrari": "#ed1c24"}[t],
    ):
        scale = team_color_scale(["Red Bull", "Ferrari"], fake_session)

    assert scale["domain"] == ["Red Bull", "Ferrari"]
    assert scale["range"] == ["#1e5bc6", "#ed1c24"]


def test_team_color_scale_falls_back_on_unknown_team():
    fake_session = MagicMock()
    with patch(
        "fastf1_mcp.utils.vega_lite.fastf1.plotting.get_team_color",
        side_effect=RuntimeError("nope"),
    ):
        scale = team_color_scale(["Unknown Team"], fake_session)

    assert scale["range"][0] == "#888888"


# ---------------------------------------------------------------------------
# _compute_race_pace refactor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_race_pace_returns_session_data_filters_tuple(mock_session):
    """The helper returns (session_obj, pace_data, filters) so chart tools
    can reuse it without loading the session twice."""
    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=mock_session,
    ):
        result = await _compute_race_pace(
            2024,
            "Monaco",
            exclude_sc_laps=True,
        )

    assert isinstance(result, tuple) and len(result) == 3
    session_obj, pace_data, filters = result
    assert session_obj is mock_session
    assert isinstance(pace_data, list) and len(pace_data) > 0
    assert pace_data[0]["deltaToFastestSec"] == 0.0
    # Each driver row carries enrichment + delta fields.
    for row in pace_data:
        assert "driver" in row
        assert "fullName" in row
        assert "teamName" in row
        assert "deltaToFastestSec" in row
        # The raw avg Timedelta is internal — must be stripped from output.
        assert "_avgRaw" not in row
    assert filters == {
        "excludeFirstLaps": 2,
        "excludeSafetyCarLaps": True,
        "excludePitLaps": True,
        "minLaps": 10,
    }


@pytest.mark.asyncio
async def test_compute_stints_returns_session_data_tuple():
    """The _compute_stints helper returns (session_obj, stints) so chart
    tools can reuse it without loading the session twice."""
    import pandas as pd
    from unittest.mock import MagicMock

    from tests._helpers import FakeLaps

    laps = FakeLaps(
        {
            "Driver": ["VER"] * 4,
            "LapNumber": [1, 2, 3, 4],
            "Stint": [1.0, 1.0, 2.0, 2.0],
            "LapTime": [
                pd.Timedelta(seconds=91),
                pd.Timedelta(seconds=90),
                pd.Timedelta(seconds=89),
                pd.Timedelta(seconds=88.5),
            ],
            "Compound": ["MEDIUM", "MEDIUM", "HARD", "HARD"],
            "IsAccurate": [True] * 4,
        }
    )
    session = MagicMock()
    session.drivers = ["VER"]
    session.laps = laps
    session.results = pd.DataFrame()

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        result = await _compute_stints(2024, "Monaco")

    assert isinstance(result, tuple) and len(result) == 2
    session_obj, stints = result
    assert session_obj is session
    assert len(stints) == 2
    assert stints[0]["compound"] == "MEDIUM"
    assert stints[0]["startLap"] == 1
    assert stints[0]["endLap"] == 2
    assert stints[1]["compound"] == "HARD"


@pytest.mark.asyncio
async def test_get_stint_analysis_public_shape_unchanged():
    """Existing get_stint_analysis contract — summary + stints — preserved
    through the refactor."""
    import pandas as pd
    from unittest.mock import MagicMock

    from tests._helpers import FakeLaps

    laps = FakeLaps(
        {
            "Driver": ["VER"] * 4,
            "LapNumber": [1, 2, 3, 4],
            "Stint": [1.0, 1.0, 2.0, 2.0],
            "LapTime": [
                pd.Timedelta(seconds=91),
                pd.Timedelta(seconds=90),
                pd.Timedelta(seconds=89),
                pd.Timedelta(seconds=88),
            ],
            "Compound": ["MEDIUM", "MEDIUM", "HARD", "HARD"],
            "IsAccurate": [True] * 4,
        }
    )
    session = MagicMock()
    session.drivers = ["VER"]
    session.laps = laps
    session.results = pd.DataFrame()

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        result = await get_stint_analysis(2024, "Monaco", "VER")

    assert set(result.keys()) >= {"summary", "stints"}
    assert result["summary"]["totalStints"] == 2
    assert len(result["stints"]) == 2


@pytest.mark.asyncio
async def test_get_race_pace_public_shape_unchanged(mock_session):
    """The public tool's return shape is unaffected by the refactor —
    same {"filters": {...}, "drivers": [...]} contract."""
    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=mock_session,
    ):
        result = await get_race_pace(2024, "Monaco")

    assert isinstance(result, dict)
    assert set(result.keys()) == {"filters", "drivers"}
    assert result["filters"]["excludeFirstLaps"] == 2
    assert len(result["drivers"]) > 0
    assert result["drivers"][0]["deltaToFastestSec"] == 0.0


# ---------------------------------------------------------------------------
# _compute_lap_times refactor + _filter_clean_laps
# ---------------------------------------------------------------------------


def _lap_times_session(deleted: list[bool] | None = None):
    """Build a (session, FakeLaps) pair for a single driver with 4 laps."""
    import pandas as pd

    from tests._helpers import FakeLaps

    deleted = deleted if deleted is not None else [False] * 4
    laps = FakeLaps(
        {
            "Driver": ["VER"] * 4,
            "LapNumber": [1, 2, 3, 4],
            "LapTime": [pd.Timedelta(seconds=t) for t in (90.0, 89.5, 89.7, 89.9)],
            "Compound": ["MEDIUM"] * 4,
            "TyreLife": [1, 2, 3, 4],
            "IsPersonalBest": [False, True, False, False],
            "Deleted": deleted,
        }
    )
    session = MagicMock()
    session.drivers = ["VER"]
    session.laps = laps
    session.results = pd.DataFrame(
        {
            "Position": [1.0],
            "Abbreviation": ["VER"],
            "DriverNumber": ["1"],
            "FullName": ["Max Verstappen"],
            "TeamName": ["Red Bull"],
        }
    )
    return session


@pytest.mark.asyncio
async def test_compute_lap_times_returns_session_df_info_tuple():
    """_compute_lap_times returns (session_obj, laps_df, driver_info) so the
    chart tool can apply chart-specific filtering on the DataFrame."""
    session = _lap_times_session(deleted=[False, False, True, False])

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        result = await _compute_lap_times(2024, "Monaco", "R", "VER")

    assert isinstance(result, tuple) and len(result) == 3
    session_obj, laps_df, info = result
    assert session_obj is session
    # Deleted lap (lapNumber=3) filtered out by default.
    assert len(laps_df) == 3
    assert 3 not in laps_df["LapNumber"].tolist()
    assert info["driverCode"] == "VER"
    assert info["teamName"] == "Red Bull"


@pytest.mark.asyncio
async def test_compute_lap_times_include_deleted_keeps_all():
    session = _lap_times_session(deleted=[False, False, True, False])

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        _, laps_df, _ = await _compute_lap_times(
            2024, "Monaco", "R", "VER", include_deleted=True
        )

    assert len(laps_df) == 4


@pytest.mark.asyncio
async def test_get_lap_times_public_shape_unchanged():
    """The public tool still returns the {driver, fullName, teamName,
    summary, laps} contract after the refactor."""
    session = _lap_times_session()
    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        result = await get_lap_times(2024, "Monaco", "R", "VER")

    assert set(result.keys()) >= {"driver", "fullName", "teamName", "summary", "laps"}
    assert result["driver"] == "VER"
    assert result["summary"]["totalLaps"] == 4


def test_filter_clean_laps_drops_pit_and_sc_laps():
    """The chart filter chain calls pick_wo_box + pick_track_status to mirror
    get_race_pace's clean-lap definition."""
    laps_df = MagicMock()
    wo_box = MagicMock()
    clean = MagicMock()
    laps_df.pick_wo_box = MagicMock(return_value=wo_box)
    wo_box.pick_track_status = MagicMock(return_value=clean)

    out = _filter_clean_laps(laps_df, include_outliers=False)

    laps_df.pick_wo_box.assert_called_once_with()
    wo_box.pick_track_status.assert_called_once_with("1", how="equals")
    assert out is clean


def test_filter_clean_laps_include_outliers_returns_input():
    """include_outliers=True bypasses both pick_wo_box and pick_track_status."""
    laps_df = MagicMock()

    out = _filter_clean_laps(laps_df, include_outliers=True)

    assert out is laps_df
    laps_df.pick_wo_box.assert_not_called()
    laps_df.pick_track_status.assert_not_called()


# ---------------------------------------------------------------------------
# _compute_positions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_positions_returns_session_rows_tuple():
    """_compute_positions returns (session_obj, rows) and drops NaN-position laps."""
    import pandas as pd
    from unittest.mock import MagicMock

    from fastf1_mcp.tools.session import _compute_positions
    from tests._helpers import FakeLaps

    laps = FakeLaps(
        {
            "Driver": ["VER"] * 3 + ["LEC"] * 3,
            "LapNumber": [1, 2, 3, 1, 2, 3],
            # VER lap 1 has NaN position (formation-lap artefact) → filtered.
            "Position": [float("nan"), 1.0, 1.0, 2.0, 2.0, 2.0],
            "LapTime": [pd.Timedelta(seconds=90)] * 6,
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
    session = MagicMock()
    session.drivers = ["VER", "LEC"]
    session.laps = laps
    session.results = results

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        result = await _compute_positions(2024, "Monaco")

    assert isinstance(result, tuple) and len(result) == 2
    session_obj, rows = result
    assert session_obj is session
    # 6 raw laps, but VER lap 1 NaN → 5 rows in output.
    assert len(rows) == 5
    for row in rows:
        assert set(row.keys()) >= {
            "driver",
            "fullName",
            "teamName",
            "lapNumber",
            "position",
        }
    ver_rows = [r for r in rows if r["driver"] == "VER"]
    assert all(r["lapNumber"] != 1 for r in ver_rows)
    # Enrichment came from session.results.
    assert ver_rows[0]["fullName"] == "Max Verstappen"
    assert ver_rows[0]["teamName"] == "Red Bull"


@pytest.mark.asyncio
async def test_compute_positions_filters_drivers():
    """Passing a drivers list restricts the output to those codes only."""
    import pandas as pd
    from unittest.mock import MagicMock

    from fastf1_mcp.tools.session import _compute_positions
    from tests._helpers import FakeLaps

    laps = FakeLaps(
        {
            "Driver": ["VER"] * 2 + ["LEC"] * 2,
            "LapNumber": [1, 2, 1, 2],
            "Position": [1.0, 1.0, 2.0, 2.0],
            "LapTime": [pd.Timedelta(seconds=90)] * 4,
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
    session = MagicMock()
    session.drivers = ["VER", "LEC"]
    session.laps = laps
    session.results = results

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session",
        new_callable=AsyncMock,
        return_value=session,
    ):
        _, rows = await _compute_positions(2024, "Monaco", drivers=["LEC"])

    assert {r["driver"] for r in rows} == {"LEC"}


# ---------------------------------------------------------------------------
# _compute_telemetry_comparison
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_telemetry_comparison_returns_session_codes_rows(mock_session):
    """Returns (session_obj, driver1, driver2, comparison_rows). Each row
    has the same keys compare_telemetry exposes."""
    from fastf1_mcp.tools.telemetry import _compute_telemetry_comparison

    with patch(
        "fastf1_mcp.utils.session_loader.session_manager.get_session_with_telemetry",
        new_callable=AsyncMock,
        return_value=mock_session,
    ):
        result = await _compute_telemetry_comparison(
            2024, "Monaco", "Q", "VER", "LEC", lap="fastest", sample_size=50
        )

    assert isinstance(result, tuple) and len(result) == 4
    session_obj, d1, d2, comparison = result
    assert session_obj is mock_session
    assert d1 == "VER"
    assert d2 == "LEC"
    assert len(comparison) == 50
    for row in comparison:
        assert set(row.keys()) >= {
            "distance",
            "speed1",
            "speed2",
            "speedDelta",
            "timeDelta",
        }


@pytest.mark.asyncio
async def test_compare_telemetry_public_shape_unchanged_after_refactor(mock_session):
    """compare_telemetry's public return shape is unchanged after the
    _compute_telemetry_comparison extraction."""
    from fastf1_mcp.config import settings
    from fastf1_mcp.tools.telemetry import compare_telemetry

    with (
        patch.object(settings, "auto_export_rows", 0),
        patch(
            "fastf1_mcp.utils.session_loader.session_manager.get_session_with_telemetry",
            new_callable=AsyncMock,
            return_value=mock_session,
        ),
    ):
        result = await compare_telemetry(
            2024, "Monaco", "Q", "VER", "LEC", lap="fastest"
        )

    assert set(result.keys()) >= {"driver1", "driver2", "summary", "comparison"}
    assert set(result["driver1"].keys()) >= {"code", "lapNumber", "lapTime"}
    assert set(result["summary"].keys()) >= {
        "lapTimeDeltaSec",
        "maxSpeedDelta",
        "sectors",
        "driver1Telemetry",
        "driver2Telemetry",
    }
    assert len(result["comparison"]) == 200
