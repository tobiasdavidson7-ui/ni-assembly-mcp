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

    # --- FTS5 index (Phase 6b) ---
    index_db_path: Path = Field(
        default_factory=lambda: _xdg("XDG_DATA_HOME", ".local/share") / "ni-assembly-mcp" / "index.db"
    )

    # --- offline indexer (PLAN.md §6.6 / §6.5 pt 4) ---
    # Hansard is ingested from TheyWorkForYou's static bulk XML (no API key); the NI
    # data API is used only for a short freshness top-up. The indexer runs gentler
    # than the tool client against both hosts.
    twfy_base_url: str = "https://www.theyworkforyou.com/pwdata/scrapedxml/ni"
    people_json_url: str = "https://raw.githubusercontent.com/mysociety/parlparse/master/members/people.json"
    index_http_max_rate_per_second: float = 2.0
    index_http_max_concurrency: int = 3
    index_circuit_break_failures: int = 5
    hansard_topup_days: int = 30

    user_agent: str = "ni-assembly-mcp (+https://github.com/tobias-davidson/ni-assembly-mcp)"

    # --- public HTTP hosting (Phase 10) ---
    # `serve --http` is a public surface: forms UI + the raw streamable-HTTP MCP
    # transport. One ASGI middleware rate-limits both (see ratelimit.py).
    http_rate_limit_enabled: bool = True
    http_rate_limit_per_minute: int = 120  # per client IP, rolling 60s window
    http_global_rate_limit_per_minute: int = 1200  # all IPs combined; trips the breaker
    http_global_cooldown_seconds: int = 30  # 503 for this long once the breaker trips
    # Behind a load balancer (Render/Fly/Railway/nginx), set this so the per-IP
    # limit keys on the real client, not the proxy. Leave off if the app is the
    # edge — X-Forwarded-For would then be spoofable.
    http_trust_proxy_headers: bool = False
    http_forwarded_allow_ips: str = "*"  # only consulted when trust_proxy_headers is on


settings = Settings()
