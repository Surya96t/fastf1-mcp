import asyncio
from contextlib import asynccontextmanager

import fastf1
from fastmcp import FastMCP

from .config import settings
from .logging_config import setup_logging
from .tools.lookups import (
    get_schedule,
    get_driver_standings,
    get_constructor_standings,
    get_driver_info,
    get_race_results_historical,
    get_circuit_info,
)
from .tools.session import (
    get_session_results,
    get_lap_times,
    get_fastest_laps,
    get_race_pace,
    get_stint_analysis,
    get_pit_stops,
    get_qualifying_breakdown,
)
from .tools.utility import (
    list_events,
    list_drivers,
    get_cache_status,
    clear_cache,
)
from .tools.telemetry import (
    get_lap_telemetry,
    compare_telemetry,
    get_speed_trap_data,
    get_sector_times,
)
from .resources import (
    schedule_resource,
    drivers_resource,
    constructors_resource,
    circuits_resource,
)
from .prompts import (
    race_recap,
    qualifying_analysis,
    driver_comparison,
    strategy_analysis,
    weekend_preview,
)

# Setup logging
logger = setup_logging()


@asynccontextmanager
async def lifespan(app):
    """Initialize FastF1 cache after MCP handshake, before any tool calls."""
    logger.info("Starting FastF1 MCP Server")
    logger.info(f"FastF1 cache: {settings.fastf1_cache_path}")
    logger.info(f"Max cached sessions: {settings.max_cached_sessions}")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _enable_fastf1_cache)
    yield
    logger.info("FastF1 MCP Server stopping")


def _enable_fastf1_cache() -> None:
    settings.fastf1_cache_path.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(settings.fastf1_cache_path)
    logger.info(f"FastF1 cache enabled at {settings.fastf1_cache_path}")


# Create MCP server
mcp = FastMCP(
    name="fastf1-mcp",
    version="0.1.0",
    instructions="Formula 1 data via FastF1 - telemetry, timing, standings, and more",
    lifespan=lifespan,
)

# =============================================================================
# Quick Lookup Tools (Ergast API)
# =============================================================================

mcp.tool(get_schedule)
mcp.tool(get_driver_standings)
mcp.tool(get_constructor_standings)
mcp.tool(get_driver_info)
mcp.tool(get_race_results_historical)
mcp.tool(get_circuit_info)

# =============================================================================
# Session Data Tools (FastF1 Live Timing, 2018+)
# =============================================================================

mcp.tool(get_session_results)
mcp.tool(get_lap_times)
mcp.tool(get_fastest_laps)
mcp.tool(get_race_pace)
mcp.tool(get_stint_analysis)
mcp.tool(get_pit_stops)
mcp.tool(get_qualifying_breakdown)

# =============================================================================
# Utility Tools
# =============================================================================

mcp.tool(list_events)
mcp.tool(list_drivers)
mcp.tool(get_cache_status)
mcp.tool(clear_cache)

# =============================================================================
# Telemetry Tools (FastF1 Live Timing, telemetry-loaded sessions)
# =============================================================================

mcp.tool(get_lap_telemetry)
mcp.tool(compare_telemetry)
mcp.tool(get_speed_trap_data)
mcp.tool(get_sector_times)

# =============================================================================
# Resources (Ergast API — reference data at well-known URIs)
# =============================================================================

mcp.resource("f1://schedule/{year}")(schedule_resource)
mcp.resource("f1://drivers/{year}")(drivers_resource)
mcp.resource("f1://constructors/{year}")(constructors_resource)
mcp.resource("f1://circuits")(circuits_resource)


# =============================================================================
# Prompts (guided multi-step workflows)
# =============================================================================

mcp.prompt(race_recap)
mcp.prompt(qualifying_analysis)
mcp.prompt(driver_comparison)
mcp.prompt(strategy_analysis)
mcp.prompt(weekend_preview)

# =============================================================================
# Entry Point
# =============================================================================


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
