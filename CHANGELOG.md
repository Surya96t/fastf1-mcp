<!-- markdownlint-configure-file { "MD024": { "siblings_only": true } } -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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

[Unreleased]: https://github.com/Surya96t/fastf1-mcp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/Surya96t/fastf1-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Surya96t/fastf1-mcp/releases/tag/v0.1.0
