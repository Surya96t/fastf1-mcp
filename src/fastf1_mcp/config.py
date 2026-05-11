import sys
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server configuration."""

    model_config = SettingsConfigDict(env_prefix="FASTF1_MCP_")

    # Cache settings
    fastf1_cache_path: Path = Path.home() / ".fastf1_cache"
    max_cached_sessions: int = 10

    # Telemetry settings
    default_telemetry_samples: int = 200
    max_telemetry_samples: int = 500

    # Export directory for full-dataset CSV exports. Relative paths are
    # resolved against the server's working directory at tool-call time —
    # under Claude Desktop, that's the `cwd` set in the MCP config (i.e.
    # the user's project root).
    export_dir: Path = Path("fastf1-exports")

    # When the bulk array of a heavy tool's response would exceed this many
    # rows, auto-export to CSV instead of returning the data inline. The
    # response then carries `exportPath` + a `note` instead of the array.
    # Set to 0 to disable auto-export (data is always inline unless the
    # caller explicitly passes export_path).
    auto_export_rows: int = 50

    # Logging
    log_level: str = "INFO"


try:
    settings = Settings()
except ValidationError as e:
    print(
        "fastf1-mcp: invalid configuration. "
        "Check your FASTF1_MCP_* environment variables.\n"
        f"{e}",
        file=sys.stderr,
    )
    sys.exit(2)
