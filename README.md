# fastf1-mcp

[![CI](https://github.com/Surya96t/fastf1-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Surya96t/fastf1-mcp/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server that exposes Formula 1 data to AI assistants via the [FastF1](https://docs.fastf1.dev) library. Ask Claude (or any MCP-compatible client) questions about race results, lap times, telemetry, standings, and more.

---

## Features

- **26 tools** covering standings, race results, lap times, telemetry, pit stops, qualifying, and **Vega-Lite chart generation**
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
git clone https://github.com/Surya96t/fastf1-mcp
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
| `FASTF1_MCP_EXPORT_DIR` | `./fastf1-exports` | Directory for CSV exports (relative to server cwd) |
| `FASTF1_MCP_AUTO_EXPORT_ROWS` | `50` | Auto-export to CSV when the bulk array exceeds this many rows. Set to `0` to disable. |
| `FASTF1_MCP_LOG_LEVEL` | `INFO` | Python logging level |

### Exporting full datasets for analysis

`get_lap_times`, `get_stint_analysis`, `get_lap_telemetry`, and
`compare_telemetry` route the bulk data array through CSV when the user
needs the file rather than the inline JSON.

**Two ways the export gets triggered:**

1. **Auto-export** (default for large responses) — when the response's
   bulk array would exceed `FASTF1_MCP_AUTO_EXPORT_ROWS` rows (default 50),
   the server writes it to CSV in `FASTF1_MCP_EXPORT_DIR` and the response
   carries `exportPath` + a `note` instead of the array. This catches
   full-race lap-time queries, full-grid stint analyses, and 200-point
   telemetry traces — exactly the cases where MCP clients would otherwise
   silently spill the response to an opaque temp file.
2. **Explicit `export_path` parameter** — pass it on the tool call:
   - `export_path=True` → write to `<FASTF1_MCP_EXPORT_DIR>/<auto-named>.csv`
   - `export_path="data/laps"` → write the auto-named file into the given directory
   - `export_path="data/ver-monaco.csv"` → write to exactly that file

The `summary` field is always included so a chat-only user can still
answer "what was the fastest lap / what's the strategy" without opening
the file. Relative paths resolve against the MCP server's working
directory — under Claude Desktop, that's the `cwd` set in your MCP
config, so files land in the user's project directory by default.

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

### Visualization (Vega-Lite v5 chart specs — 2018-present)

Each tool returns a ready-to-render **Vega-Lite v5** spec plus a self-contained
HTML page. See [Rendering charts](#rendering-charts) below for how the charts
surface in different clients.

| Tool | Description |
|---|---|
| `get_lap_time_chart` | Line+point chart of a driver's lap times across a session, coloured by tyre compound. |
| `get_pace_delta_chart` | Horizontal-bar chart of each driver's race-pace delta to the fastest driver. |
| `get_tyre_strategy_chart` | Horizontal-Gantt chart of tyre stints across the field (or one driver). |
| `get_position_chart` | Multi-line chart of race positions by lap (P1 on top). |
| `get_speed_trace_chart` | Two-driver speed trace along the lap distance. |

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

## Rendering charts

The five `*_chart` tools return a Vega-Lite v5 spec that renders the same way
across clients. Each response carries:

| Field | Purpose |
|---|---|
| `vegaLiteSpec` | The full Vega-Lite v5 spec (self-contained — data is embedded, no external fetch at render time). |
| `html` | A complete, self-contained HTML page with the spec already embedded — paste verbatim into a Claude Desktop artifact. |
| `png` | A `data:image/png;base64,…` render of the chart (via `vl-convert`), for clients that display images inline. |
| `htmlPath` | Path to a temp HTML file the server **auto-opens in your default browser** — so the chart appears even in clients that ignore the other fields. |
| `renderingInstructions` | Guidance the model follows to surface the chart without inventing custom tags. |

**Per client:**

- **Claude Desktop** — the model builds an artifact from the `html` field and
  the chart renders in the conversation.
- **VS Code / other MCP clients** — the server opens `htmlPath` in your default
  browser automatically, regardless of how the client handles tool output.

Charts use F1 broadcast conventions: team colours (via `fastf1.plotting`),
tyre-compound colours (soft/medium/hard/inter/wet), reversed position axes
(P1 on top), and lap times formatted as `M:SS.mmm`.

Example:

```
Chart Verstappen's lap times at the 2024 Monaco GP
→ get_lap_time_chart(2024, "Monaco", "R", "VER")

Show the tyre strategy for the 2024 Hungarian GP
→ get_tyre_strategy_chart(2024, "Hungary")
```

Rendering PNGs requires [`vl-convert`](https://github.com/vega/vl-convert)
(installed automatically via `uv sync`); no Node.js is needed.

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
