"""Integration tests for SessionManager caching behaviour."""

import json
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fastf1_mcp.session_manager import SessionManager
from fastf1_mcp.utils.converters import telemetry_to_json


def _make_mock_session():
    """Return a minimal FastF1 session mock."""
    session = MagicMock()
    session.load = MagicMock(return_value=None)
    return session


@pytest.mark.asyncio
async def test_cache_hit_returns_same_object():
    """Second call for the same session must return the cached object without reloading."""
    mock_session = _make_mock_session()

    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", return_value=mock_session
    ) as mock_get:
        manager = SessionManager(max_sessions=5)

        s1 = await manager.get_session(2024, "Monaco", "R")
        s2 = await manager.get_session(2024, "Monaco", "R")

        # Same object
        assert s1 is s2
        # fastf1.get_session called only once
        assert mock_get.call_count == 1
        # session.load called only once
        assert mock_session.load.call_count == 1


@pytest.mark.asyncio
async def test_cache_hit_is_fast():
    """Second call should return near-instantly (no executor overhead)."""
    mock_session = _make_mock_session()

    def slow_get_session(*args, **kwargs):
        time.sleep(0.05)  # simulate 50ms load
        return mock_session

    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", side_effect=slow_get_session
    ):
        manager = SessionManager(max_sessions=5)

        # First call — loads (slow)
        t0 = time.monotonic()
        await manager.get_session(2024, "Monaco", "R")
        first_call_ms = (time.monotonic() - t0) * 1000

        # Second call — cache hit (fast)
        t1 = time.monotonic()
        await manager.get_session(2024, "Monaco", "R")
        second_call_ms = (time.monotonic() - t1) * 1000

        # Cache hit should be at least 10x faster than the initial load
        assert second_call_ms < first_call_ms / 10, (
            f"Cache hit took {second_call_ms:.1f}ms, expected < {first_call_ms / 10:.1f}ms"
        )


@pytest.mark.asyncio
async def test_lru_eviction_drops_oldest():
    """Loading more sessions than max_sessions must evict the least-recently-used one."""
    mock_session = _make_mock_session()

    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", return_value=mock_session
    ):
        manager = SessionManager(max_sessions=3)

        await manager.get_session(2024, "Bahrain", "R")  # key: 2024:Bahrain:R  (oldest)
        await manager.get_session(2024, "Jeddah", "R")  # key: 2024:Jeddah:R
        await manager.get_session(
            2024, "Australia", "R"
        )  # key: 2024:Australia:R — cache full

        assert len(manager._cache) == 3
        assert "2024:Bahrain:R" in manager._cache

        # Loading a 4th session must evict Bahrain (LRU)
        await manager.get_session(2024, "Monaco", "R")

        assert len(manager._cache) == 3
        assert "2024:Bahrain:R" not in manager._cache, "Oldest session was not evicted"
        assert "2024:Monaco:R" in manager._cache


@pytest.mark.asyncio
async def test_lru_eviction_respects_access_order():
    """Accessing an old session refreshes its recency so it isn't evicted first."""
    mock_session = _make_mock_session()

    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", return_value=mock_session
    ):
        manager = SessionManager(max_sessions=2)

        await manager.get_session(2024, "Bahrain", "R")  # loaded first
        await manager.get_session(2024, "Jeddah", "R")  # loaded second — cache full

        # Re-access Bahrain — it should become the MRU
        await manager.get_session(2024, "Bahrain", "R")

        # Loading Australia must evict Jeddah (now the LRU), not Bahrain
        await manager.get_session(2024, "Australia", "R")

        assert "2024:Jeddah:R" not in manager._cache, "Jeddah should have been evicted"
        assert "2024:Bahrain:R" in manager._cache
        assert "2024:Australia:R" in manager._cache


@pytest.mark.asyncio
async def test_clear_cache_all():
    """clear_cache() with no args clears everything."""
    mock_session = _make_mock_session()

    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", return_value=mock_session
    ):
        manager = SessionManager(max_sessions=5)

        await manager.get_session(2024, "Bahrain", "R")
        await manager.get_session(2024, "Monaco", "R")

        cleared = manager.clear_cache()
        assert cleared == 2
        assert len(manager._cache) == 0


@pytest.mark.asyncio
async def test_cache_upgrades_when_more_data_requested():
    """
    First call requests load_laps=False (e.g. get_session_results), then a
    second call requests load_laps=True (e.g. get_pit_stops). The cached
    session must be upgraded — calling session.load(laps=True) — instead of
    returning a session with un-loaded laps.

    Regression for the "data has not been loaded yet" error users hit when
    asking follow-up questions in the same chat.
    """
    mock_session = _make_mock_session()
    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", return_value=mock_session
    ):
        manager = SessionManager(max_sessions=5)

        # Step 1: get_session_results-style call (laps not needed)
        await manager.get_session(2026, "Miami", "R", load_laps=False)
        first_load_call = mock_session.load.call_args
        assert first_load_call.kwargs["laps"] is False

        # Step 2: get_pit_stops-style call (laps required)
        await manager.get_session(2026, "Miami", "R", load_laps=True)

        # session.load should have been invoked a second time with laps=True
        assert mock_session.load.call_count == 2
        upgrade_call = mock_session.load.call_args
        assert upgrade_call.kwargs["laps"] is True

        # The cached session should now record laps as loaded
        cached = manager._cache["2026:Miami:R"]
        assert cached.loaded["laps"] is True


@pytest.mark.asyncio
async def test_cache_no_upgrade_when_already_satisfied():
    """If a cached session already has everything requested, no reload."""
    mock_session = _make_mock_session()
    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", return_value=mock_session
    ):
        manager = SessionManager(max_sessions=5)

        await manager.get_session(2026, "Miami", "R", load_laps=True)
        await manager.get_session(2026, "Miami", "R", load_laps=False)
        await manager.get_session(2026, "Miami", "R", load_laps=True)

        # Only the first call should have triggered load()
        assert mock_session.load.call_count == 1


@pytest.mark.asyncio
async def test_concurrent_loads_dedupe_via_lock():
    """Two concurrent get_session() calls for the same key must call fastf1.get_session once."""
    import asyncio

    mock_session = _make_mock_session()
    load_started = asyncio.Event()
    release_load = asyncio.Event()

    def slow_get_session(*args, **kwargs):
        # Signal the test that we're inside the load, then block until released.
        load_started.set()
        # run_in_executor offloads to a thread, so we can't await here — we
        # have to spin until the asyncio Event is set from the main task.
        import time

        start = time.monotonic()
        while not release_load.is_set() and time.monotonic() - start < 2.0:
            time.sleep(0.005)
        return mock_session

    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", side_effect=slow_get_session
    ) as mock_get:
        manager = SessionManager(max_sessions=5)

        # First call begins loading and blocks; second call should wait on the lock.
        first = asyncio.create_task(manager.get_session(2024, "Monaco", "R"))
        await load_started.wait()
        second = asyncio.create_task(manager.get_session(2024, "Monaco", "R"))

        # Give the second task a moment to reach the lock — if dedup is broken
        # it would call fastf1.get_session a second time.
        await asyncio.sleep(0.05)

        release_load.set()
        s1, s2 = await asyncio.gather(first, second)

        assert s1 is s2
        assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_clear_cache_clears_locks_dict():
    """M6 regression: clear_cache() must drop entries from _locks too."""
    mock_session = _make_mock_session()
    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", return_value=mock_session
    ):
        manager = SessionManager(max_sessions=5)
        await manager.get_session(2024, "Monaco", "R")
        assert len(manager._locks) > 0

        manager.clear_cache()
        assert len(manager._locks) == 0


@pytest.mark.asyncio
async def test_lru_eviction_drops_lock_entry():
    """M6 regression: LRU eviction must also remove the per-session lock."""
    mock_session = _make_mock_session()
    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", return_value=mock_session
    ):
        manager = SessionManager(max_sessions=2)
        await manager.get_session(2024, "Bahrain", "R")
        await manager.get_session(2024, "Jeddah", "R")
        await manager.get_session(2024, "Monaco", "R")  # evicts Bahrain

        assert "2024:Bahrain:R" not in manager._cache
        assert "2024:Bahrain:R" not in manager._locks


@pytest.mark.asyncio
async def test_clear_cache_by_year():
    """clear_cache(year=2024) only removes sessions for that year."""
    mock_session = _make_mock_session()

    with patch(
        "fastf1_mcp.session_manager.fastf1.get_session", return_value=mock_session
    ):
        manager = SessionManager(max_sessions=5)

        await manager.get_session(2024, "Monaco", "R")
        await manager.get_session(2023, "Monaco", "R")

        cleared = manager.clear_cache(year=2024)
        assert cleared == 1
        assert "2024:Monaco:R" not in manager._cache
        assert "2023:Monaco:R" in manager._cache


# ---------------------------------------------------------------------------
# Phase 3.3 verification: telemetry sampling
# ---------------------------------------------------------------------------


def _make_mock_telemetry(n_points: int = 1000) -> pd.DataFrame:
    """Synthetic telemetry DataFrame with n_points rows."""
    distance = np.linspace(0, 3337.0, n_points)  # Monaco lap ~3337m
    return pd.DataFrame(
        {
            "Distance": distance,
            "Speed": np.random.uniform(60, 300, n_points),
            "Throttle": np.random.uniform(0, 100, n_points),
            "Brake": np.random.choice([True, False], n_points),
            "nGear": np.random.randint(1, 9, n_points),
            "DRS": np.zeros(n_points, dtype=int),
        }
    )


@pytest.mark.parametrize("sample_size", [50, 200, 500])
def test_telemetry_sample_size_matches(sample_size):
    """telemetry_to_json must return exactly sample_size points when source has more rows."""
    tel = _make_mock_telemetry(n_points=1000)  # always > max sample_size
    result = telemetry_to_json(tel, sample_size=sample_size)
    assert len(result) == sample_size, (
        f"Expected {sample_size} points, got {len(result)}"
    )


@pytest.mark.parametrize("sample_size", [50, 200, 500])
def test_telemetry_response_under_100kb(sample_size):
    """JSON-encoded telemetry response must stay under 100 KB for all valid sample sizes."""
    tel = _make_mock_telemetry(n_points=1000)
    result = telemetry_to_json(tel, sample_size=sample_size)
    payload = {
        "telemetry": result,
        "driver": "VER",
        "lap": "fastest",
        "sampleSize": sample_size,
    }
    size_bytes = len(json.dumps(payload).encode())
    assert size_bytes < 100_000, (
        f"Response for sample_size={sample_size} is {size_bytes} bytes (≥ 100KB)"
    )
