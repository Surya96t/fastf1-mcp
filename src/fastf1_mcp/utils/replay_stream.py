"""
Client for the f1-race-replay telemetry stream.

f1-race-replay (https://github.com/IAmTomShaw/f1-race-replay) broadcasts its
playback state as newline-delimited JSON over TCP (default localhost:9999)
while a replay is playing. Each message can be ~1 MB because it carries
every driver's lap history.

Important: the replay's server writes with a blocking `sendall` on its
render thread, so a client that stops reading can freeze the replay. This
client therefore drains the socket continuously and disconnects as soon as
it has what it needs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from ..config import settings
from .errors import ErrorCode, FastF1MCPError

logger = logging.getLogger(__name__)

# Messages carry the full lap-time history; allow generous line lengths.
_LINE_LIMIT = 64 * 1024 * 1024


def _endpoint() -> tuple[str, int]:
    return settings.replay_stream_host, settings.replay_stream_port


async def _connect(timeout: float):
    host, port = _endpoint()
    try:
        return await asyncio.wait_for(
            asyncio.open_connection(host, port, limit=_LINE_LIMIT), timeout
        )
    except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"No f1-race-replay telemetry stream at {host}:{port} ({e!r}).",
            suggestions=[
                "Start a race replay in f1-race-replay "
                "(python main.py --viewer --year <y> --round <r>)",
                "The stream address is set by FASTF1_MCP_REPLAY_STREAM_HOST / "
                "FASTF1_MCP_REPLAY_STREAM_PORT (default 127.0.0.1:9999)",
            ],
        ) from e


def _no_data_error(seconds: float) -> FastF1MCPError:
    return FastF1MCPError(
        ErrorCode.DATA_UNAVAILABLE,
        f"Connected to the replay stream but received no data in {seconds:.0f}s.",
        suggestions=[
            "The replay only broadcasts while playing — un-pause it (SPACE)",
        ],
    )


async def _close(writer) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


async def read_snapshot(timeout: float = 5.0) -> dict:
    """Connect, return the next complete message, disconnect."""
    reader, writer = await _connect(timeout)
    try:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout)
        except asyncio.TimeoutError as e:
            raise _no_data_error(timeout) from e
        if not line:
            raise FastF1MCPError(
                ErrorCode.DATA_UNAVAILABLE, "The replay closed the stream."
            )
        return _decode(line)
    finally:
        await _close(writer)


async def read_window(
    duration: float,
    sample_every: float = 0.25,
    timeout: float = 5.0,
    on_sample: Callable[[dict], None] | None = None,
) -> list[dict]:
    """
    Read the stream for `duration` seconds, decoding one message every
    `sample_every` seconds (every message is still read, so the replay
    is never blocked). Always includes the first and last message seen.
    """
    reader, writer = await _connect(timeout)
    samples: list[dict] = []
    last_line: bytes | None = None
    last_decoded_at = -1e9
    start = time.monotonic()
    try:
        while True:
            remaining = duration - (time.monotonic() - start)
            if remaining <= 0:
                break
            wait = remaining if samples else max(remaining, timeout)
            try:
                line = await asyncio.wait_for(reader.readline(), wait)
            except asyncio.TimeoutError:
                break
            if not line:
                break
            now = time.monotonic()
            if now - last_decoded_at >= sample_every:
                msg = _decode(line)
                samples.append(msg)
                if on_sample:
                    on_sample(msg)
                last_decoded_at = now
                last_line = None
            else:
                last_line = line
    finally:
        await _close(writer)

    if last_line is not None:
        samples.append(_decode(last_line))
    if not samples:
        raise _no_data_error(max(duration, timeout))
    return samples


def _decode(line: bytes) -> dict[str, Any]:
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        raise FastF1MCPError(
            ErrorCode.DATA_UNAVAILABLE,
            f"Malformed message from the replay stream: {e}",
        ) from e
