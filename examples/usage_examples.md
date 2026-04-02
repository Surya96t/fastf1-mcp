# fastf1-mcp — Usage Examples

A reference of sample MCP tool calls and their expected responses. All examples use real 2024 season data; responses are abbreviated for readability.

---

## Running the MCP Inspector

```bash
# Recommended — official npx inspector
npx @modelcontextprotocol/inspector uv --directory . run fastf1-mcp

# Alternative — fastmcp wrapper
uv run fastmcp dev inspector -m fastf1_mcp.server --with-editable .
```

Opens at **http://localhost:6274**. To skip the auth token locally:

```bash
DANGEROUSLY_OMIT_AUTH=true npx @modelcontextprotocol/inspector uv --directory . run fastf1-mcp
```

---

## Quick Lookup Tools (Ergast — all seasons)

### `get_driver_standings`

```json
{ "year": 2024 }
```

```json
[
  { "position": 1, "code": "VER", "name": "Max Verstappen", "team": "Red Bull", "points": 437.0, "wins": 19 },
  { "position": 2, "code": "LEC", "name": "Charles Leclerc", "team": "Ferrari", "points": 307.0, "wins": 3 },
  ...
]
```

### `get_driver_standings` — after a specific round

```json
{ "year": 2024, "after_round": 10 }
```

Returns standings as of round 10 (Canadian GP).

### `get_constructor_standings`

```json
{ "year": 2024 }
```

```json
[
  { "position": 1, "name": "McLaren", "nationality": "British", "points": 666.0, "wins": 6 },
  { "position": 2, "name": "Ferrari", "nationality": "Italian", "points": 652.0, "wins": 5 },
  ...
]
```

### `get_schedule`

```json
{ "year": 2025 }
```

```json
[
  { "round": 1, "raceName": "Bahrain Grand Prix", "circuitName": "Bahrain International Circuit", "country": "Bahrain", "date": "2025-03-02" },
  ...
]
```

### `get_circuit_info`

```json
{ "circuit_id": "monaco" }
```

```json
[
  { "circuitId": "monaco", "circuitName": "Circuit de Monaco", "country": "Monaco", "locality": "Monte-Carlo", "lat": 43.7347, "long": 7.42056 }
]
```

### `get_race_results_historical`

```json
{ "year": 1988, "round_num": 1 }
```

Returns Ergast race results — useful for pre-2018 data.

---

## Session Data Tools (FastF1 — 2018-present)

> First call loads from disk/network (10–60 s). Subsequent calls return from memory cache instantly.

### `get_session_results`

```json
{ "year": 2024, "event": "Monaco", "session": "R" }
```

```json
[
  { "position": 1, "driverCode": "LEC", "fullName": "Charles Leclerc", "teamName": "Ferrari", "gridPosition": 1, "status": "Finished", "points": 25.0, "time": "0 days 01:45:21.622000" },
  { "position": 2, "driverCode": "PIA", "fullName": "Oscar Piastri", "teamName": "McLaren", "gridPosition": 4, ... },
  ...
]
```

### `get_lap_times`

```json
{ "year": 2024, "event": "Monaco", "session": "R", "driver": "LEC" }
```

```json
[
  { "lapNumber": 1, "lapTime": "0:02:05.123", "sector1": "0:00:42.1", "compound": "MEDIUM", "tyreLife": 1, "isPersonalBest": false, "deleted": false },
  ...
]
```

### `get_fastest_laps`

```json
{ "year": 2024, "event": "Silverstone", "session": "Q", "top_n": 5 }
```

```json
[
  { "driver": "NOR", "lapTime": "0:01:26.720", "lapNumber": 18, "compound": "SOFT" },
  { "driver": "VER", "lapTime": "0:01:26.818", ... },
  ...
]
```

### `get_race_pace`

```json
{ "year": 2024, "event": "Bahrain" }
```

```json
[
  { "driver": "VER", "avgLapTime": "0:01:33.456", "lapCount": 51, "deltaToFastestSec": 0.0, "fastestLap": "0:01:32.900", "slowestLap": "0:01:34.100" },
  { "driver": "LEC", "avgLapTime": "0:01:33.910", "lapCount": 50, "deltaToFastestSec": 0.454, ... },
  ...
]
```

Safety car laps, pit laps, and the first 2 laps are excluded by default.

### `get_stint_analysis`

```json
{ "year": 2024, "event": "Monaco" }
```

```json
[
  { "driver": "LEC", "stint": 1, "compound": "MEDIUM", "lapCount": 28, "minLapTime": "0:01:14.800", "avgLapTime": "0:01:15.200", "maxLapTime": "0:01:16.100" },
  { "driver": "LEC", "stint": 2, "compound": "HARD", "lapCount": 50, ... },
  ...
]
```

### `get_pit_stops`

```json
{ "year": 2024, "event": "Monaco" }
```

```json
[
  { "driver": "LEC", "lap": 28, "compound": "MEDIUM", "newCompound": "HARD", "duration": "0:00:22.456" },
  ...
]
```

### `get_qualifying_breakdown`

```json
{ "year": 2024, "event": "Silverstone" }
```

```json
{
  "Q1": {
    "results": [{ "position": 1, "driver": "NOR", "lapTime": "0:01:27.450", ... }, ...],
    "eliminated": ["RIC", "SAR", "GUA", "BOT", "ZHO"]
  },
  "Q2": { ... },
  "Q3": { ... }
}
```

---

## Telemetry Tools

### `get_lap_telemetry`

```json
{ "year": 2024, "event": "Monaco", "session": "Q", "driver": "LEC", "lap": "fastest", "sample_size": 200 }
```

```json
{
  "driver": "LEC",
  "lapNumber": 20,
  "lapTime": "0:01:10.270",
  "data": [
    { "distance": 0.0, "speed": 60.0, "throttle": 25.0, "brake": false, "gear": 2, "drs": 0 },
    { "distance": 16.7, "speed": 78.3, "throttle": 48.0, "brake": false, "gear": 3, "drs": 0 },
    ...
  ]
}
```

Returns exactly `sample_size` distance-evenly-spaced points (max 500).

### `compare_telemetry`

```json
{ "year": 2024, "event": "Monaco", "session": "Q", "driver1": "VER", "driver2": "LEC" }
```

```json
{
  "driver1": { "code": "VER", "lapNumber": 18, "lapTime": "0:01:10.720" },
  "driver2": { "code": "LEC", "lapNumber": 20, "lapTime": "0:01:10.270" },
  "comparison": [
    { "distance": 0.0, "speed1": 62.0, "speed2": 60.0, "speedDelta": 2.0, "timeDelta": 0.0 },
    ...
  ],
  "summary": {
    "lapTimeDeltaSec": 0.450,
    "maxSpeedDelta": 8.2,
    "sectors": {
      "S1": { "driver1": "0:00:24.100", "driver2": "0:00:23.800", "deltaSec": 0.300 },
      "S2": { "driver1": "0:00:22.050", "driver2": "0:00:22.100", "deltaSec": -0.050 },
      "S3": { "driver1": "0:00:24.570", "driver2": "0:00:24.370", "deltaSec": 0.200 }
    }
  }
}
```

`timeDelta` is cumulative — positive means driver1 is further behind at that point.

### `get_sector_times`

```json
{ "year": 2024, "event": "Silverstone", "session": "Q" }
```

```json
[
  {
    "driver": "NOR",
    "bestS1": "0:00:27.100", "bestS2": "0:00:35.800", "bestS3": "0:00:23.820",
    "theoreticalBest": "0:01:26.720", "actualBest": "0:01:26.720", "gapSec": 0.0
  },
  ...
]
```

### `get_speed_trap_data`

```json
{ "year": 2024, "event": "Monza", "session": "Q" }
```

```json
[
  { "driver": "VER", "speedTrap": 362.3, "speedFL": 328.0, "speedI1": 298.5, "speedI2": 311.2 },
  ...
]
```

---

## Utility Tools

### `list_events`

```json
{ "year": 2024 }
```

```json
[
  { "round": 1, "eventName": "Bahrain Grand Prix", "country": "Bahrain", "circuitName": "Bahrain International Circuit", "date": "2024-03-02" },
  ...
]
```

### `get_cache_status`

```json
{}
```

```json
{
  "cachedSessions": 3,
  "maxSessions": 10,
  "sessions": [
    { "key": "2024:Monaco:R", "loadedAt": "2025-04-01T12:34:56" },
    ...
  ],
  "diskCacheSizeMB": 842.3
}
```

---

## MCP Resources

Resources are fetched by URI, not tool call.

| URI | Returns |
|---|---|
| `f1://schedule/2024` | 24-race 2024 calendar |
| `f1://drivers/2024` | All drivers who raced in 2024 |
| `f1://constructors/2024` | All constructors in 2024 |
| `f1://circuits` | All-time F1 circuit list |

---

## Prompts

Prompts are multi-step workflows — the assistant calls several tools and synthesises the results.

### `race_recap`

```
year=2024, event=Monaco
```

The assistant will call `get_session_results`, `get_fastest_laps`, `get_pit_stops`, and `get_stint_analysis`, then write a narrative race review.

### `strategy_analysis`

```
year=2024, event=Bahrain
```

Calls `get_stint_analysis`, `get_pit_stops`, and `get_race_pace` to explain who won the tire strategy battle and why.

### `qualifying_analysis`

```
year=2024, event=Silverstone
```

Calls `get_qualifying_breakdown`, `get_sector_times`, and `get_fastest_laps` to break down the session sector-by-sector.

### `driver_comparison`

```
year=2024, driver1=VER, driver2=NOR
```

Compares championship positions, head-to-head race finishes, and qualifying results across the season.

### `weekend_preview`

```
year=2025, event=Monaco
```

Calls `get_circuit_info`, `get_race_results_historical`, and `get_driver_standings` to set the scene for an upcoming race.


---

## Auth token

By default the inspector generates a random token on each run and prints:

```
🔑 Session token: 68a7372e...
🚀 MCP Inspector is up and running at:
   http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=68a7372e...
```

**To use the token:** just copy-paste the full URL it prints — the token is already
in it. You don't set it yourself; it's freshly generated every time.

**To skip the token entirely** (simpler for local dev), prefix with:

```bash
DANGEROUSLY_OMIT_AUTH=true npx @modelcontextprotocol/inspector uv --directory . run fastf1-mcp
```

Then just open `http://localhost:6274`. Safe on a local machine — only matters if
the inspector were exposed on a network.

---

### What to expect in the browser

- **Tools tab** — lists all registered tools with input schemas; run them from here
- **Notifications pane** — shows server logs (INFO, WARNING, etc.)

Common issues:
- `"Failed to validate request: Received request before initialization"` — server still starting, retry in a second
- `"Not connected"` — auto-reload restarted the server after a file change, refresh the browser

---

## Phase 1 tool examples

### `get_driver_standings`

Input:
```json
{ "year": 2025 }
```

Expected: list of 20 drivers ordered by points, each with `position`, `code`, `name`, `team`, `points`, `wins`.

---

### `get_constructor_standings`

Input:
```json
{ "year": 2024 }
```

Expected: 10 constructors. P1 was McLaren (666 pts) in 2024.

---

### `get_schedule`

Input:
```json
{ "year": 2025 }
```

Expected: 24 races with `round`, `raceName`, `circuitName`, `country`, `date`.

---

### `get_driver_info`

Input (all drivers in a season):
```json
{ "year": 2024 }
```

Input (single driver):
```json
{ "driver_id": "max_verstappen" }
```

---

### `get_race_results_historical`

Input:
```json
{ "year": 2015, "round_num": 10 }
```

Good for pre-2018 data where FastF1 session data isn't available.

---

### `get_circuit_info`

Input (all circuits in a season):
```json
{ "year": 2024 }
```

Input (single circuit):
```json
{ "circuit_id": "monaco" }
```

