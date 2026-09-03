"""Configuration for the NI Assembly MCP server.

Stripped down from parliament-mcp's settings.py: no SSM/boto3, no Azure, no Qdrant,
no auth (see PLAN.md 5b). Just the HTTP client knobs and cache/index paths.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _xdg(env_var: str, default_subdir: str) -> Path:
    """Resolve an XDG-style base dir, falling back to ~/<default_subdir>."""
    base = os.environ.get(env_var)
    if base:
        return Path(base).expanduser()
    return Path.home() / default_subdir


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NI_ASSEMBLY_MCP_", env_file=".env", extra="ignore")

    # --- data.niassembly.gov.uk ---
    base_url: str = "https://data.niassembly.gov.uk"

    # --- HTTP politeness (legacy IIS/ASP.NET backend; see PLAN.md 5e / 6.5 pt 4) ---
    http_max_rate_per_second: float = 3.0
    http_max_concurrency: int = 4
    http_timeout_seconds: float = 120.0
    http_max_retries: int = 3
    http_retry_initial_wait: float = 1.0
    http_retry_max_wait: float = 30.0

    # --- on-disk HTTP cache (survives restarts; see PLAN.md 6.5 pt 3) ---
    hishel_cache_dir: Path = Field(
        default_factory=lambda: _xdg("XDG_CACHE_HOME", ".cache") / "ni-assembly-mcp" / "http"
    )
    hishel_ttl: timedelta = timedelta(days=1)

    # --- FTS5 index (Phase 6b; not used yet) ---
    index_db_path: Path = Field(
        default_factory=lambda: _xdg("XDG_DATA_HOME", ".local/share") / "ni-assembly-mcp" / "index.db"
    )

    user_agent: str = "ni-assembly-mcp (+https://github.com/tobias-davidson/ni-assembly-mcp)"


settings = Settings()
