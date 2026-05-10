import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any

import fastf1

from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class CachedSession:
    """Wrapper for a cached FastF1 session."""

    session: Any  # fastf1.Session
    loaded_at: datetime
    year: int
    event: str
    session_type: str
    # What FastF1 Session.load() flags have been satisfied for this session.
    loaded: dict[str, bool] = field(default_factory=dict)


class SessionManager:
    """
    Async-safe session manager with LRU caching.

    Features:
    - Wraps synchronous FastF1 calls with run_in_executor
    - LRU cache with configurable max size
    - Lock-based deduplication of concurrent loads
    """

    def __init__(self, max_sessions: int = 10):
        self.max_sessions = max_sessions
        self._cache: OrderedDict[str, CachedSession] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _cache_key(self, year: int, event: str | int, session_type: str) -> str:
        """Generate a unique cache key."""
        return f"{year}:{event}:{session_type}"

    async def _get_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for a specific session."""
        async with self._global_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def get_session(
        self,
        year: int,
        event: str | int,
        session_type: str = "R",
        load_laps: bool = True,
        load_telemetry: bool = False,
        load_weather: bool = True,
        load_messages: bool = True,
    ) -> Any:
        """
        Get a FastF1 session, loading if necessary.

        Args:
            year: Season year
            event: Race name or round number
            session_type: R, Q, S, FP1, FP2, FP3, etc.
            load_laps: Load lap timing data
            load_telemetry: Load telemetry (slower)
            load_weather: Load weather data
            load_messages: Load race control messages

        Returns:
            Loaded FastF1 Session object
        """
        key = self._cache_key(year, event, session_type)
        requested = {
            "laps": load_laps,
            "telemetry": load_telemetry,
            "weather": load_weather,
            "messages": load_messages,
        }

        # Cache hit — return immediately if the cached session already has
        # everything we need; otherwise fall through to a lock-protected
        # upgrade load.
        if key in self._cache and not self._needs_upgrade(self._cache[key], requested):
            logger.debug(f"Cache hit: {key}")
            self._cache.move_to_end(key)
            return self._cache[key].session

        lock = await self._get_lock(key)
        async with lock:
            if key in self._cache:
                cached = self._cache[key]
                if not self._needs_upgrade(cached, requested):
                    self._cache.move_to_end(key)
                    return cached.session

                # Upgrade in place: load only the flags that aren't satisfied.
                # FastF1's Session.load() is idempotent for already-loaded
                # data, but we still pass only the missing flags to avoid
                # ambiguity. Any flags already loaded stay loaded.
                missing = {
                    k: True
                    for k, v in requested.items()
                    if v and not cached.loaded.get(k, False)
                }
                logger.info(
                    f"Upgrading cached session {key}: loading {sorted(missing.keys())}"
                )
                loop = asyncio.get_running_loop()
                load_func = partial(
                    cached.session.load,
                    laps=missing.get("laps", False),
                    telemetry=missing.get("telemetry", False),
                    weather=missing.get("weather", False),
                    messages=missing.get("messages", False),
                )
                await loop.run_in_executor(None, load_func)

                for k in missing:
                    cached.loaded[k] = True
                self._cache.move_to_end(key)
                return cached.session

            logger.info(f"Loading session: {year} {event} {session_type}")
            loop = asyncio.get_running_loop()

            session = await loop.run_in_executor(
                None,
                partial(fastf1.get_session, year, event, session_type),
            )

            load_func = partial(
                session.load,
                laps=load_laps,
                telemetry=load_telemetry,
                weather=load_weather,
                messages=load_messages,
            )
            await loop.run_in_executor(None, load_func)

            logger.info(f"Session loaded: {year} {event} {session_type}")

            # Evict if at capacity
            while len(self._cache) >= self.max_sessions:
                evicted_key, _ = self._cache.popitem(last=False)
                self._locks.pop(evicted_key, None)
                logger.info(f"Evicted from cache: {evicted_key}")

            self._cache[key] = CachedSession(
                session=session,
                loaded_at=datetime.now(),
                year=year,
                event=str(event),
                session_type=session_type,
                loaded={k: v for k, v in requested.items() if v},
            )

            return session

    @staticmethod
    def _needs_upgrade(cached: CachedSession, requested: dict[str, bool]) -> bool:
        """True if the cached session is missing any flag the caller needs."""
        return any(v and not cached.loaded.get(k, False) for k, v in requested.items())

    async def get_session_with_telemetry(
        self,
        year: int,
        event: str | int,
        session_type: str = "R",
    ) -> Any:
        """Convenience method for getting session with telemetry loaded."""
        return await self.get_session(year, event, session_type, load_telemetry=True)

    def get_cache_status(self) -> dict:
        """Return current cache status."""
        return {
            "sessions_cached": len(self._cache),
            "max_sessions": self.max_sessions,
            "cached_sessions": [
                {
                    "year": cs.year,
                    "event": cs.event,
                    "session": cs.session_type,
                    "loaded_at": cs.loaded_at.isoformat(),
                }
                for cs in self._cache.values()
            ],
        }

    def clear_cache(
        self,
        year: int | None = None,
        event: str | None = None,
    ) -> int:
        """
        Clear sessions from cache.

        Returns number of sessions cleared.
        """
        if year is None:
            count = len(self._cache)
            self._cache.clear()
            self._locks.clear()
            return count

        keys_to_remove = [
            key
            for key, cs in self._cache.items()
            if cs.year == year and (event is None or cs.event == event)
        ]

        for key in keys_to_remove:
            del self._cache[key]
            self._locks.pop(key, None)

        return len(keys_to_remove)


# Global instance
session_manager = SessionManager(max_sessions=settings.max_cached_sessions)
