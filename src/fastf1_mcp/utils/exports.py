"""CSV export helpers for the heavy data tools.

When a caller wants the *full* dataset for downstream analysis (notebook,
ML pipeline) rather than just the inline JSON, the tools write CSV to a
known directory and return the path on the response instead of the bulk
array. This keeps the MCP response small while still giving the user a
concrete file they can open.
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime
from pathlib import Path

from ..config import settings

logger = logging.getLogger(__name__)


_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(value: object) -> str:
    """Lowercase + collapse non-alphanumerics for use in a filename."""
    return _SAFE_CHARS.sub("-", str(value)).strip("-").lower() or "unknown"


def resolve_export_path(
    export: bool | str,
    *,
    tool: str,
    parts: list[object],
    extension: str = "csv",
) -> Path:
    """
    Resolve the caller's `export` flag/string to a concrete file path.

    Args:
        export: `True` → auto-name under `settings.export_dir`.
                `str` → either a full file path (ends in `.<extension>`),
                or a directory to put the auto-named file in.
        tool:   Tool name slug used in the auto-generated filename.
        parts:  Identifying parts (year, event, session, driver, …) used in
                the auto-generated filename. Falsy parts are dropped.
        extension: File extension, default "csv".

    Returns:
        Absolute Path. Parent directory is created.
    """
    if export is True:
        base_dir = Path(settings.export_dir).expanduser().resolve()
        filename = _auto_filename(tool, parts, extension)
        target = base_dir / filename
    else:
        # `export` is a string at this point.
        candidate = Path(str(export)).expanduser()
        if candidate.suffix == f".{extension}":
            target = candidate.resolve()
        else:
            # Treat as a directory.
            target = (candidate / _auto_filename(tool, parts, extension)).resolve()

    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _auto_filename(tool: str, parts: list[object], extension: str) -> str:
    """Build `<tool>_<part1>_<part2>_<timestamp>.<ext>`."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug_parts = [_slug(p) for p in parts if p not in (None, "")]
    stem = "_".join([_slug(tool), *slug_parts, ts])
    return f"{stem}.{extension}"


def _display_path(path: Path) -> str:
    """
    Render an absolute Path for the caller — relative to cwd when the file
    lives under the project root, absolute otherwise.

    Under Claude Desktop the server's cwd is the user's project, so the
    relative form (`fastf1-exports/<…>.csv`) is what the user actually
    types to `open` the file. We fall back to absolute when the export
    lives outside cwd (e.g. caller passed a custom path elsewhere) — a
    relative path with `..` segments would be more confusing than useful.
    """
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def should_auto_export(rows: list[dict]) -> bool:
    """True if `rows` exceeds the auto-export size threshold."""
    threshold = settings.auto_export_rows
    return threshold > 0 and len(rows) >= threshold


def apply_export(
    response: dict,
    rows: list[dict],
    *,
    bulk_key: str,
    export_path: bool | str,
    tool: str,
    parts: list[object],
) -> None:
    """
    Mutate `response` to either inline the bulk array OR write it to CSV.

    Decision tree:
      - `export_path` truthy            → write CSV, omit bulk_key, set
                                          `exportPath` + `rowCount`.
      - exceeds auto-export threshold   → same, plus a `note` explaining
                                          the auto-export.
      - otherwise                       → response[bulk_key] = rows.

    `bulk_key` is the name of the array field that would otherwise hold
    `rows` (e.g. "laps", "stints", "data", "comparison"). `tool` and
    `parts` flow into the auto-generated filename.
    """
    auto = False
    if not export_path and should_auto_export(rows):
        export_path = True
        auto = True

    if not export_path:
        response[bulk_key] = rows
        return

    path = resolve_export_path(export_path, tool=tool, parts=parts)
    row_count = write_rows_csv(rows, path)
    response["exportPath"] = _display_path(path)
    response["rowCount"] = row_count
    if auto:
        response["note"] = (
            f"Auto-exported {row_count} rows to CSV "
            f"(threshold: {settings.auto_export_rows} rows). "
            f"The `summary` field above contains the key aggregates; "
            f"open the file at `exportPath` for the full dataset "
            f"(pandas / notebook / ML use)."
        )


def write_rows_csv(rows: list[dict], path: Path) -> int:
    """
    Write `rows` to `path` as CSV. Returns the row count.

    The header is the union of all row keys (stable: order preserved by
    first-seen). Missing fields are written as empty strings.
    """
    if not rows:
        # Still create an empty file with no header — a downstream reader
        # then sees an empty CSV rather than a missing file, which is the
        # more "expected" failure mode.
        path.write_text("")
        return 0

    seen: dict[str, None] = {}
    for r in rows:
        for k in r.keys():
            seen.setdefault(k, None)
    fieldnames = list(seen.keys())

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    logger.info(f"Exported {len(rows)} rows to {path}")
    return len(rows)
