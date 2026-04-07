# fastf1-mcp

[![CI](https://github.com/Surya96t/fastf1-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Surya96t/fastf1-mcp/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server that exposes Formula 1 data to AI assistants via the [FastF1](https://docs.fastf1.dev) library. Ask Claude (or any MCP-compatible client) questions about race results, lap times, telemetry, standings, and more.

---

## Features

- **21 tools** covering standings, race results, lap times, telemetry, pit stops, and qualifying
- **4 MCP resources** for schedule, driver, constructor, and circuit reference data
- **5 guided prompts** for race recaps, qualifying analysis, strategy deep-dives, and weekend previews
- Async-safe **LRU session cache** — repeat queries are instant after the first load
- Distance-based telemetry sampling — large raw datasets compressed to ≤ 500 points
- All errors returned as structured dicts — the server never crashes on bad input

---

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

---

## Installation

### With uv (recommended)

```bash
git clone https://github.com/yourname/fastf1-mcp
cd fastf1-mcp
uv sync
```

### With pip

```bash
pip install fastf1-mcp-server
```

---

## Running the server

```bash
# via uv (development)
uv run fastf1-mcp-server

# or directly
python -m fastf1_mcp
```

### MCP Inspector (development / debugging)

```bash
# Option A — official npx inspector
npx @modelcontextprotocol/inspector uv --directory . run fastf1-mcp-server

# Option B — fastmcp wrapper
uv run fastmcp dev inspector -m fastf1_mcp.server --with-editable .
```

Both open the inspector at **http://localhost:6274**.

---

## Claude Desktop configuration

Add the following to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "fastf1": {
      "command": "uv",
      "args": ["run", "fastf1-mcp-server"],
      "cwd": "/absolute/path/to/fastf1-mcp",
      "env": {
        "FASTF1_MCP_LOG_LEVEL": "INFO",
        "FASTF1_MCP_MAX_CACHED_SESSIONS": "10"
      }
    }
  }
}
```

Restart Claude Desktop after saving. The server name `fastf1` will appear in the tools panel.

---

## Configuration

All settings are read from environment variables with the `FASTF1_MCP_` prefix.

| Variable | Default | Description |
|---|---|---|
| `FASTF1_MCP_FASTF1_CACHE_PATH` | `~/.fastf1_cache` | Disk cache for FastF1 session files |
| `FASTF1_MCP_MAX_CACHED_SESSIONS` | `10` | Max sessions held in memory (LRU) |
| `FASTF1_MCP_DEFAULT_TELEMETRY_SAMPLES` | `200` | Default telemetry sample points |
| `FASTF1_MCP_MAX_TELEMETRY_SAMPLES` | `500` | Hard cap on telemetry sample points |
| `FASTF1_MCP_LOG_LEVEL` | `INFO` | Python logging level |

---

## Tools

### Quick Lookup (Ergast API — 1950-present)

| Tool | Description |
|---|---|
| `get_schedule` | Get the F1 race calendar for a season. |
| `get_driver_standings` | Get driver championship standings. |
| `get_constructor_standings` | Get constructor championship standings. |
| `get_driver_info` | Get driver information. |
| `get_race_results_historical` | Get historical race results (pre-2018 or when session data unavailable). |
| `get_circuit_info` | Get circuit information. |

### Session Data (FastF1 Live Timing — 2018-present)

| Tool | Description |
|---|---|
| `get_session_results` | Get session classification/results. |
| `get_lap_times` | Get all lap times for a driver in a session. |
| `get_fastest_laps` | Get fastest laps in a session, one per driver. |
| `get_race_pace` | Calculate average race pace for all drivers. |
| `get_stint_analysis` | Analyze tire stints for a race. |
| `get_pit_stops` | Get all pit stops from a race. |
| `get_qualifying_breakdown` | Get qualifying results split by Q1/Q2/Q3. |

### Telemetry (FastF1 Live Timing — 2018-present)

| Tool | Description |
|---|---|
| `get_lap_telemetry` | Get telemetry data for a specific lap. |
| `compare_telemetry` | Compare telemetry between two drivers on the same session. |
| `get_speed_trap_data` | Get speed trap and top-speed data for all drivers in a session. |
| `get_sector_times` | Get best sector times and theoretical best lap for each driver. |

### Utility

| Tool | Description |
|---|---|
| `list_events` | List all events in a season. |
| `list_drivers` | List all drivers in a season, optionally filtered to a specific event. |
| `get_cache_status` | Check server in-memory session cache status. |
| `clear_cache` | Clear cached sessions from in-memory storage. |

---

## Resources

| URI | Description |
|---|---|
| `f1://schedule/{year}` | Full race calendar for a season |
| `f1://drivers/{year}` | All drivers who competed in a season |
| `f1://constructors/{year}` | All constructors in a season |
| `f1://circuits` | All F1 circuits (all-time) |

---

## Prompts

| Prompt | Args | What it does |
|---|---|---|
| `race_recap` | `year, event` | Calls results + fastest laps + pit stops + stints, then narrates the race |
| `qualifying_analysis` | `year, event` | Q breakdown + sector times + top laps analysis |
| `driver_comparison` | `year, driver1, driver2` | Season-level head-to-head: standings, races, qualifying |
| `strategy_analysis` | `year, event` | Stints + pit timing + race pace — explains who won the strategy battle |
| `weekend_preview` | `year, event` | Circuit details + recent history + championship context |

---

## Example queries (Claude Desktop)

```
Who won the 2024 Monaco Grand Prix and what was the strategy?
→ use race_recap prompt or call get_session_results + get_stint_analysis

Compare Verstappen and Leclerc's telemetry in 2024 Monaco qualifying
→ compare_telemetry(2024, "Monaco", "Q", "VER", "LEC")

Who had the fastest theoretical lap in 2024 Silverstone qualifying?
→ get_sector_times(2024, "Silverstone", "Q")

Show me the 2024 constructor standings after round 10
→ get_constructor_standings(2024, after_round=10)
```

---

## Development

```bash
# Install dev dependencies
uv sync --dev

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=fastf1_mcp

# Lint
uv run ruff check src/
```

---

## Data sources & coverage

| Source | Coverage | Used for |
|---|---|---|
| [Ergast API](http://ergast.com/mrd/) (via FastF1) | 1950 – present | Standings, schedules, historical results, circuit info |
| [FastF1 Live Timing](https://docs.fastf1.dev) | 2018 – present | Lap times, telemetry, qualifying, pit stops, tire data |

> **Note:** FastF1 session data is only available from 2018 onwards. Use `get_race_results_historical` for earlier seasons.

---

## License

MIT
