# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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

[Unreleased]: https://github.com/Surya96t/fastf1-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Surya96t/fastf1-mcp/releases/tag/v0.1.0
