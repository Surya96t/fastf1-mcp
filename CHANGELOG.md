<!-- markdownlint-configure-file { "MD024": { "siblings_only": true } } -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.3.0] - 2026-07-13

### Added

- **Vega-Lite visualization tools (5)** — new `tools/visualization.py` module exposing chart tools that return ready-to-render **Vega-Lite v5** specs. Each response carries `vegaLiteSpec`, a self-contained `html` page (spec pre-embedded), a `png` data URI (rendered via `vl-convert`), and `htmlPath` — a temp file the server auto-opens in the default browser so the chart surfaces even in clients that ignore tool output. A `renderingInstructions` field keeps the model from inventing custom render tags.
  - `get_lap_time_chart` — line+point trace of one driver's lap times, coloured by tyre compound; drops deleted/pit/SC laps by default (`include_outliers=True` to keep them); y-axis formatted `M:SS.mmm` via a Vega `labelExpr`.
  - `get_pace_delta_chart` — horizontal-bar race-pace delta to the fastest driver, coloured by team; exposes the same `exclude_*` filter params as `get_race_pace`.
  - `get_tyre_strategy_chart` — horizontal-Gantt stint chart across the field (or a single driver), sorted by finishing order, coloured by compound.
  - `get_position_chart` — multi-line race positions by lap with a reversed y-axis (P1 on top); filters `NaN` positions (laps under SC).
  - `get_speed_trace_chart` — two-driver speed trace along lap distance, in long format, coloured by fastf1 driver palette.
- **`utils/vega_lite.py`** — shared chart infrastructure: `base_spec`, `driver_color_scale` / `team_color_scale` (via `fastf1.plotting`, `#888888` fallback), `compound_color_scale` (F1 compound palette, `HARD = #CCCCCC` so it stays visible on white), `spec_to_html` (self-contained vega-embed page), `spec_to_png_b64` (`vl-convert`), and `RENDERING_INSTRUCTIONS`.
- **`examples/preview-charts.html`** — paste-and-render dev harness (CDN vega-embed, no build step) for eyeballing specs during development.

### Changed

- Refactored `tools/session.py` to extract `_compute_race_pace`, `_compute_stints`, and `_compute_lap_times` so the chart tools and the data tools share one computation path. Public tool return shapes are unchanged.

### Dependencies

- Added **`vl-convert-python >= 1.9.0`** — Rust-backed Vega-Lite → PNG rendering, no Node.js required.

### Tests

- 81 new tests across `test_visualization.py` (all 5 chart tools: spec shape, encoding, filtering, titles, error paths, HTML/PNG fields, y-axis `labelExpr`) and `test_vega_lite_helpers.py` (color scales, `spec_to_html`/`spec_to_png_b64`, and the `_compute_*` refactor contracts). The `*_spec_is_valid_v5` tests compile each spec through the vega-lite compiler (`vl-convert`) — real schema validation, not just key presence.

---

## [0.2.0] - 2026-05-11

### Fixed

- **Lap-1 pit-stop & stint artifacts** — `get_pit_stops` now filters phantom entries with `duration > 120s` (FastF1 occasionally records a multi-thousand-second lap-1 `PitInTime` tied to the session start). `get_stint_analysis` drops the matching phantom single-lap stint at lap 1 when it has no recorded `LapTime`.
- **Speed-trap nulls** — `get_speed_trap_data` falls back to a per-driver max across `session.laps` for `SpeedST/FL/I1/I2` when the `session.results` columns are entirely empty. A new `source` field on the response (`"results"` or `"laps"`) indicates which path produced the values, and a `data_unavailable` error is raised only when both sources are empty.
- **Better `SESSION_NOT_HELD` error** — asking for a session that wasn't held at an event (e.g. Sprint at a non-sprint weekend) now returns a dedicated `session_not_held` error code with usable suggestions instead of a generic load failure.

### Added

- **`get_sector_times` per-lap detail (Q18)** — new `include_laps: bool = False` parameter. When True, each driver's entry includes a `laps` array with `lapNumber`, `s1`, `s2`, `s3`, `lapTime` so callers can answer "show me Antonelli's sector times each lap" instead of only the best/theoretical-best summary. Also adds `fullName` + `teamName` enrichment to every driver entry.
- **Relative `exportPath` when the file lives under cwd** — the export response now reports paths like `fastf1-exports/get_lap_times_2024_british_r_ver_<ts>.csv` instead of the full absolute path, so chat replies stay readable. Falls back to absolute when the export was written outside the working directory (e.g. caller supplied an unrelated path).
- **`gap` field on `get_session_results` (Q36)** — backmarker rows used to have null `time` because FastF1 leaves `Time` as `NaT` for drivers more than a lap behind the leader. The new `gap` field synthesises a single human-readable string for every classified driver: `"leader"` for P1, `"+0:00:07.152"` for same-lap finishers, `"+1 Lap"`/`"+2 Laps"` from `Status` for lapped finishers, `"DNF"`/`"Retired"`/etc. for non-finishers. The raw `time` field is preserved.
- **`export_path` parameter + auto-export on heavy tools** — `get_lap_times`, `get_stint_analysis`, `get_lap_telemetry`, and `compare_telemetry` now route their bulk array through CSV in two ways:
  - **Auto-export** (default): when the array would exceed `FASTF1_MCP_AUTO_EXPORT_ROWS` rows (default 50), the server writes it to CSV and the response carries `exportPath` + a `note` instead of the inline array. Catches full-race lap-time queries, full-grid stint analyses, and 200-point telemetry traces — the cases where MCP clients would otherwise silently spill the response to an opaque temp file. Set the env var to `0` to disable.
  - **Explicit `export_path`** parameter: `True` writes to the configured `FASTF1_MCP_EXPORT_DIR` (default `./fastf1-exports/` relative to the server cwd, i.e. the user's project directory under Claude Desktop). A string is treated as a directory or a full `.csv` path.
  - The `summary` field is always included so callers can still answer aggregate questions without opening the file.
  - Docstring trigger phrases ("save as CSV", "load into pandas", "ML / notebook / analysis") help the chat model infer when to set `export_path=True` proactively for small-but-data-shaped queries.
- **`FASTF1_MCP_EXPORT_DIR` and `FASTF1_MCP_AUTO_EXPORT_ROWS` settings** with sensible defaults.
- **Driver enrichment on session-data tools** — `get_race_pace`, `get_pit_stops`, `get_stint_analysis`, `get_lap_times`, and `get_speed_trap_data` now include `fullName` and `teamName` for every row. Real FastF1 `Session.drivers` returns driver numbers (e.g. `"1"`), not codes; the new `build_driver_lookup()` helper resolves either form to `{driverCode, fullName, teamName}` via `session.results`.
- **`team` field on season-level `list_drivers`** — the no-event path now fetches `get_driver_standings` in parallel and merges the constructor name in. Tolerates a standings failure (still returns the driver list without team).
- **Response summaries** on tools that often produce large arrays (Q17/Q26/Q30–32/Q38 regressions):
  - `get_lap_times.summary` — total/valid lap counts, fastest+slowest, avg, compound distribution.
  - `get_stint_analysis.summary.strategies` — per-driver compound sequence + stint lengths + pit-stop count (compact 1-stop vs 2-stop view).
  - `get_lap_telemetry.summary` and `compare_telemetry.summary.{driver1,driver2}Telemetry` — max/min/avg speed, max gear, braking-zone count, full-throttle percentage.
- **Echoed filters on `get_race_pace`** — response includes a `filters` block (`excludeSafetyCarLaps`, `excludePitLaps`, etc.) so the caller can clearly state the conditions under which the pace was computed.
- New `ErrorCode.SESSION_NOT_HELD`.

### Changed

- **Breaking — return shape changes** (rationale: room for `summary` / `filters` / `source` metadata alongside the row arrays):
  - `get_race_pace` now returns `{filters, drivers}` (was `list[dict]`).
  - `get_speed_trap_data` now returns `{source, drivers}` (was `list[dict]`).
  - `get_lap_times` now returns `{driver, fullName, teamName, summary, laps}` (was `list[dict]`).
  - `get_stint_analysis` now returns `{summary, stints}` (was `list[dict]`).
- `get_speed_trap_data` now loads lap data (needed for the laps fallback). The cache upgrade path means this is a no-op when laps are already loaded.

### Tests

- 68 tests passing. Added regression tests for the speed-trap laps fallback (`test_get_speed_trap_data_falls_back_to_laps`), the explicit export path (`test_get_lap_times_export_writes_csv_and_omits_inline_array`, `test_get_stint_analysis_export_writes_csv`), the auto-export threshold (`test_get_lap_telemetry_auto_exports_above_threshold`), per-lap sector data (`test_get_sector_times_includes_per_lap_array_when_requested`), and the gap synthesis for lapped/DNF finishers (`test_get_session_results_gap_for_lapped_finisher`). Updated assertions for the new return shapes.

---

## [0.1.1] - 2026-05-10

### Fixed

- **Telemetry math correctness** — `compare_telemetry`'s `timeDelta` is now reliable at standing starts, in pit lane, and when telemetry has non-monotonic `Distance` samples. Prefers FastF1's recorded `Time` channel when available; otherwise integrates speed with a 0.5 m/s floor and integrates after sorting.
- **`get_pit_stops`** — `stopNumber` is now deterministic per driver; rows with missing lap numbers are skipped; results sorted by `(lap, driver)`.
- **`get_sector_times` / `get_qualifying_breakdown`** — sort by raw `Timedelta` (the previous string sort could misorder times at edge cases).
- **`get_lap_times`** — no longer raises `KeyError` on session types that omit the `Deleted` column.
- **`clear_cache`** — rejects `event` without `year` (used to silently clear all caches).
- **Lock-dict leak** — `SessionManager._locks` entries are now removed on LRU eviction and `clear_cache`.
- **Telemetry JSON output** — `driver` field guarded against `pd.NA`; sampling drops NaN distances before computing the search axis.
- **Prompts** — event names with quotes no longer break the embedded code snippets (`json.dumps` escape).

### Added

- `driver` field on `laps_to_json` output (matches `get_fastest_laps` docstring).
- Year/sample-size bounds validation on telemetry tools.
- Friendly error message when `FASTF1_MCP_*` env vars are invalid.
- `Programming Language :: Python :: 3.13` classifier.

### Changed

- Internal: extracted `require_session()` + `@tool_handler` helper, removing ~14 copies of error-handling boilerplate across tool modules.
- `telemetry_to_json` now uses `np.searchsorted` instead of per-sample `idxmin` (large speedup on long sessions).
- `get_cache_status` runs the disk scan in an executor (no longer blocks the event loop).
- Ergast `limit` raised from 30/100 to 1000 (older seasons with >30 drivers no longer truncated).
- Upper-bound version pins on `fastf1`, `fastmcp`, `pydantic-settings`.
- Package version now derived from git tag via `hatch-vcs` — no more manual `pyproject.toml` bump.
- Release workflow gated by the `pypi` GitHub environment (manual approval before publish).

### Tests

- 60 tests passing (up from 20). Added regression tests for telemetry math, pit stops, qualifying breakdown, sector times, stint analysis, lap times, cache lock dedup, and the year guard across all 11 session-dependent tools.

---

## [0.1.0] - 2026-04-01

### Added

- **21 MCP tools** across four categories:
  - Ergast lookup tools (6): `get_schedule`, `get_driver_standings`, `get_constructor_standings`, `get_driver_info`, `get_race_results_historical`, `get_circuit_info`
  - Session data tools (7): `get_session_results`, `get_lap_times`, `get_fastest_laps`, `get_race_pace`, `get_stint_analysis`, `get_pit_stops`, `get_qualifying_breakdown`
  - Telemetry tools (4): `get_lap_telemetry`, `compare_telemetry`, `get_speed_trap_data`, `get_sector_times`
  - Utility tools (4): `list_events`, `list_drivers`, `get_cache_status`, `clear_cache`
- **4 MCP resources**: `f1://schedule/{year}`, `f1://drivers/{year}`, `f1://constructors/{year}`, `f1://circuits`
- **5 MCP prompts**: `race_recap`, `qualifying_analysis`, `driver_comparison`, `strategy_analysis`, `weekend_preview`
- Async LRU session cache (`SessionManager`) with configurable size and per-session locks
- Distance-based telemetry sampling — raw 5000+ point laps compressed to ≤ 500 points
- Structured error responses (`FastF1MCPError`) — server never crashes on bad input
- Claude Desktop and VS Code Copilot configuration support
- 20 unit and integration tests, 0 warnings

[Unreleased]: https://github.com/Surya96t/fastf1-mcp/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Surya96t/fastf1-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Surya96t/fastf1-mcp/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Surya96t/fastf1-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Surya96t/fastf1-mcp/releases/tag/v0.1.0
