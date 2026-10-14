<!-- markdownlint-configure-file { "MD024": { "siblings_only": true } } -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- **Real circuit maps, offline** — `plot_circuit` draws any F1 circuit from bundled outline data ([bacinger/f1-circuits](https://github.com/bacinger/f1-circuits), MIT; 40 circuits incl. every venue used 2018–2026 and the 2026 Madring), by name, Grand Prix, nickname or year + round, or a whole season's calendar as a sheet. The data was validated before bundling: outline lengths match the official figures to within 1.5% for every modern circuit, the direction of racing matches circuit guides (Marina Bay and Paul Ricard are stored reversed in the source and are flipped), and each outline starts on the start/finish line with Turn 1 bending the right way.
- `plot_track_map` falls back to the stored outline (with a note) when FastF1 telemetry can't be downloaded.
- **Track map** — `plot_track_map` draws the circuit from FastF1 position data: a lap's racing line coloured by speed or gear with numbered corners, DRS zones and direction of travel, or (`at_lap=N`) every car's position on track as the leader starts lap N with the running order. Uses the official map rotation from the circuit data.
- **Live championship projection** — `get_championship_projection` gives drivers' and constructors' standings as if the race ended at any lap: baseline from the previous round's official standings (plus same-weekend sprint points), race points from the running order (sprint table for sprints; +1 fastest-lap point for 2019–2024 Grands Prix), official points on the final lap, and optionally the projected championship leader after every lap.
- **DRS zones** — `get_drs_zones` infers zones from where the DRS flap actually opened on the fastest laps of up to 8 drivers (clustered, with zones straddling the start/finish line merged) and names the surrounding corners. `plot_speed_trace` shades DRS-open sections and the track map highlights the zones.
- **f1-race-replay bridge** — `get_replay_state` and `watch_replay` read the telemetry stream of a race playing in [f1-race-replay](https://github.com/IAmTomShaw/f1-race-replay): live leaderboard, track status, safety car, weather, race-control messages, and overtakes / pit stops / status changes over a watch window. The client drains the stream continuously (the replay's server would otherwise block its render thread) and handles multi-megabyte messages. New `replay_commentary` prompt; `FASTF1_MCP_REPLAY_STREAM_HOST` / `_PORT` settings.

- **Race control & weather** — `get_race_control_messages` returns the race-control feed plus a session-wide summary: SC / VSC / red-flag periods with start/end laps (from the track-status feed), penalties, investigations (every car involved), and track-limits lap deletions per driver; filter by category, driver or lap range. `get_weather` returns air/track temperature, humidity and wind stats, rain periods mapped to laps, and an interval-sampled series.
- **Race progression** — `get_position_changes`: lap-by-lap positions and race-history gaps to the leader, grid→finish movement, lap-1 gains, laps led, lead changes, and biggest gainers/losers. Long-format CSV export.
- **Tyre degradation** — `get_tyre_degradation`: fuel-corrected degradation rate (s/lap) per stint from clean green-flag laps (outliers >107% dropped), per-compound medians, fresh-tyre pace and compound crossover estimates. Works for races and practice long runs.
- **Corner analysis** — `get_corner_analysis`: apex speed, gear, approach/exit speed and brake point at every numbered corner (MultiViewer markers via FastF1), and per-corner time deltas between two drivers.
- **Charts** — `plot_speed_trace`, `plot_position_chart`, `plot_tyre_strategy`, `plot_lap_times` return PNG images (plus a caption and structured metadata) that MCP clients render inline; `save_path` also writes the file. Colours use a fixed, colour-vision-checked palette; tyre compounds are always lettered as well as coloured.
- Prompts `race_recap`, `qualifying_analysis` and `strategy_analysis` now use the new tools.
- `matplotlib` is now a direct dependency (it was already installed via FastF1).

### Fixed

- **Failed FastF1 downloads crashed tools and poisoned the cache** — FastF1's `Session.load()` only logs download failures, so an unreachable live-timing API (or a session whose data isn't published yet) surfaced later as a raw `DataNotLoadedError` from every session tool, and the half-loaded session stayed cached. The session manager now verifies that results/laps/telemetry actually loaded (a failed results download leaves an empty table, which made `get_session_results` silently return `[]`), never caches a failed load, and tools return a `data_unavailable` error explaining the likely cause.
- FastF1 warnings were logged twice (FastF1's own handler plus ours).
- Review fixes (CodeRabbit on #8): stored calendars now list one entry per championship round (2020 repeat venues, 2021 Styrian GP, cancelled 2023 Imola; 2026 Bahrain/Saudi cancellations and the Bahrain GP at Sepang), so round-number lookups resolve the right circuit; `watch_replay` reports every position change seen (plus `netPositionChanges`), keeps only the first/last messages in memory, ends early with `stalled` when the stream goes quiet, and rejects non-object messages; driver-list parameters accept comma-separated strings through MCP; `get_championship_projection` handles sessions without laps; the race-control driver filter ignores case and whitespace.

- **`compare_telemetry` docs had the `timeDelta` sign backwards** — it said positive = driver1 ahead, but the value is driver1 − driver2 elapsed time, so negative = driver1 ahead (consistent with `lapTimeDeltaSec` and sector `deltaSec`). Values are unchanged; the docstring now matches them.
- `get_speed_trap_data` return annotation corrected to `dict` (it has returned `{source, drivers}` since 0.2.0).

- **Error dicts rejected by MCP clients** — tools annotated `-> list[dict]` (`get_session_results`, `get_fastest_laps`, `get_pit_stops`, the Ergast lookups, …) advertised an `outputSchema` that the structured error dict didn't match, so clients reported "Invalid structured content" instead of the error. `@tool_handler` now widens each tool's advertised return type to also allow the error shape.
- **Ergast lookup tools raised raw exceptions** — `get_schedule`, `get_driver_standings`, `get_constructor_standings`, `get_driver_info`, `get_race_results_historical` and `get_circuit_info` now return a `data_unavailable` error dict when the Ergast/Jolpica API fails, like every other tool.
- **Non-JSON values in Ergast responses** — `NaN` (e.g. missing driver codes / permanent numbers before 2005), `NaT` and numpy integers either broke FastMCP serialisation or produced invalid JSON. Ergast-backed tools and resources now normalise them (`None`, ISO strings, Python ints).
- **`get_race_results_historical` `time` was `"NaT"` for non-finishers** — now `null`.
- **`list_drivers` returned `"nan"` as the number** for drivers without a permanent number — now `""`.

### Changed

- Internal: new `utils/ergast.py` (`run_ergast`, `first_frame`) and `converters.to_jsonable` / `records_to_json` shared by tools and resources. `list_events`, `list_drivers`, `get_cache_status` and `clear_cache` now use `@tool_handler`; `list_drivers(event=…)` loads through `require_session` (gains the `session_not_held` error).
- Docs: `CONTRIBUTING.md` now states Python 3.12+, documents how tools are actually registered (`mcp.tool(fn)` in `server.py`) and lists all `utils/` modules; `examples/usage_examples.md` uses the `fastf1-mcp-server` command.

### Tests

- 253 tests passing (up from 68). New synthetic race fixture with known ground truth (degradation rates, SC window, pit laps, apex speeds) so the analysis tools are checked for *recovering* the right answers; chart tools are exercised end-to-end through an MCP client (PNG content, captions, `save_path`, input validation); error dicts are validated against each tool's advertised output schema. The replay bridge is tested against a local TCP server speaking the f1-race-replay protocol (including ~3 MB messages) and was also checked against f1-race-replay's own `TelemetryStreamServer`.

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

[Unreleased]: https://github.com/Surya96t/fastf1-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Surya96t/fastf1-mcp/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Surya96t/fastf1-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Surya96t/fastf1-mcp/releases/tag/v0.1.0
