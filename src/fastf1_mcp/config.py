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
