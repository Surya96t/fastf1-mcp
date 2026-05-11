"""Unit tests for tools/telemetry.py (FastF1 telemetry tools)."""

import pytest
from unittest.mock import AsyncMock, patch

from fastf1_mcp.config import settings
from fastf1_mcp.tools.telemetry import get_lap_telemetry, compare_telemetry


@pytest.mark.asyncio
async def test_telemetry_sample_size_capped(mock_session):
    """
    Requesting sample_size > max_telemetry_samples returns exactly
    max_telemetry_samples data points (default 500).
    """
    # Disable auto-export — this test verifies the sample-size cap, not the
    # auto-export behaviour. With auto-export on, the data would land in a
    # CSV instead of inline.
    with (
        patch.object(settings, "auto_export_rows", 0),
        patch(
            "fastf1_mcp.utils.session_loader.session_manager.get_session_with_telemetry",
            new_callable=AsyncMock,
            return_value=mock_session,
        ),
    ):
        result = await get_lap_telemetry(
            2024, "Monaco", "Q", "VER", lap="fastest", sample_size=1000
        )

    assert isinstance(result, dict)
    assert "data" in result
    assert len(result["data"]) == settings.max_telemetry_samples


@pytest.mark.asyncio
async def test_compare_telemetry_summary(mock_session):
    """
    compare_telemetry returns a summary dict with lapTimeDeltaSec and
    per-sector splits (S1, S2, S3).
    """
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

    assert isinstance(result, dict)
    assert "summary" in result
    assert "sectors" in result["summary"]
    assert "S1" in result["summary"]["sectors"]
    assert "S2" in result["summary"]["sectors"]
    assert "S3" in result["summary"]["sectors"]
    assert "lapTimeDeltaSec" in result["summary"]
    assert "comparison" in result
    assert len(result["comparison"]) == 200  # default sample_size


@pytest.mark.asyncio
async def test_get_lap_telemetry_auto_exports_above_threshold(mock_session, tmp_path):
    """
    When the sampled telemetry exceeds auto_export_rows, the data array is
    written to CSV and replaced by exportPath + a note explaining why.
    """
    with (
        patch.object(settings, "auto_export_rows", 50),
        patch.object(settings, "export_dir", tmp_path),
        patch(
            "fastf1_mcp.utils.session_loader.session_manager.get_session_with_telemetry",
            new_callable=AsyncMock,
            return_value=mock_session,
        ),
    ):
        result = await get_lap_telemetry(
            2024, "Monaco", "Q", "VER", lap="fastest", sample_size=200
        )

    assert "data" not in result, "Bulk array should be omitted after auto-export"
    assert "exportPath" in result
    assert "rowCount" in result and result["rowCount"] == 200
    assert "note" in result
    assert "summary" in result, "Summary stays even when data is exported"
