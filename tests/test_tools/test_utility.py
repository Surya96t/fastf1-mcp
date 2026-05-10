"""Tests for utility tools (cache management, list_events, prompts)."""

from unittest.mock import patch

import pytest

from fastf1_mcp.prompts import (
    qualifying_analysis,
    race_recap,
    strategy_analysis,
)
from fastf1_mcp.tools.utility import clear_cache, get_cache_status
from fastf1_mcp.utils.errors import ErrorCode


# ---------------------------------------------------------------------------
# clear_cache validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_cache_rejects_event_without_year():
    # M5 regression: passing event without year used to silently clear all.
    result = await clear_cache(year=None, event="Monaco")
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result.get("code") == ErrorCode.INVALID_PARAMETER.value


@pytest.mark.asyncio
async def test_clear_cache_no_args_returns_dict():
    with (
        patch("fastf1_mcp.tools.utility.session_manager.clear_cache", return_value=0),
        patch("fastf1_mcp.tools.utility.session_manager._cache", {}),
    ):
        result = await clear_cache()
    assert isinstance(result, dict)
    assert "cleared" in result
    assert "remaining" in result


# ---------------------------------------------------------------------------
# get_cache_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cache_status_includes_disk_size_when_path_exists(tmp_path):
    # Drop a tiny file in the cache path so we can verify the size calc.
    f = tmp_path / "stub.bin"
    f.write_bytes(b"x" * 1024)

    fake_status = {
        "sessions_cached": 0,
        "max_sessions": 10,
        "cached_sessions": [],
    }

    with patch(
        "fastf1_mcp.tools.utility.session_manager.get_cache_status",
        return_value=fake_status,
    ):
        from fastf1_mcp import config as cfg

        with patch.object(cfg.settings, "fastf1_cache_path", tmp_path):
            result = await get_cache_status()

    assert result["fastf1_cache_path"] == str(tmp_path)
    assert result["fastf1_cache_size_mb"] >= 0.0


@pytest.mark.asyncio
async def test_get_cache_status_missing_path_reports_zero(tmp_path):
    missing = tmp_path / "does-not-exist"
    fake_status = {
        "sessions_cached": 0,
        "max_sessions": 10,
        "cached_sessions": [],
    }

    with patch(
        "fastf1_mcp.tools.utility.session_manager.get_cache_status",
        return_value=fake_status,
    ):
        from fastf1_mcp import config as cfg

        with patch.object(cfg.settings, "fastf1_cache_path", missing):
            result = await get_cache_status()

    assert result["fastf1_cache_size_mb"] == 0.0


# ---------------------------------------------------------------------------
# Prompt escaping (L5)
# ---------------------------------------------------------------------------


def _prompt_text(messages) -> str:
    """Extract the underlying text from a FastMCP prompt Message."""
    return messages[0].content.text


def test_race_recap_escapes_event_with_quote():
    # L5 regression: if event contained a quote, the old code produced malformed
    # snippets like get_session_results(2024, "evil"event", "R"). After json.dumps
    # the call lines must contain backslash-escaped quotes.
    text = _prompt_text(race_recap(2024, 'evil"event'))
    assert '"evil\\"event"' in text, "json.dumps escape did not survive interpolation"


@pytest.mark.parametrize(
    "fn",
    [race_recap, qualifying_analysis, strategy_analysis],
)
def test_event_prompts_return_at_least_one_message(fn):
    msgs = fn(2024, "Monaco")
    assert isinstance(msgs, list) and len(msgs) >= 1
