from __future__ import annotations

import json
from pathlib import Path

import pytest

from ni_assembly_mcp.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    """Settings with cache + index redirected into a tmp dir, faster limits."""
    return Settings(
        base_url="https://data.niassembly.gov.uk",
        http_max_rate_per_second=1000.0,
        http_max_concurrency=8,
        http_max_retries=2,
        http_retry_initial_wait=0.01,
        http_retry_max_wait=0.05,
        hishel_cache_dir=tmp_path / "http-cache",
        index_db_path=tmp_path / "index.db",
    )


@pytest.fixture(autouse=True)
async def _reset_client():
    from ni_assembly_mcp.http_client import reset_http_client

    await reset_http_client()
    yield
    await reset_http_client()


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch, test_settings):
    """Point the module-global ``settings`` at a tmp-dir config for every test.

    Tools call ``niassembly_get`` without an explicit ``config=``; this keeps the
    real HTTP cache dir untouched and the limits fast.
    """
    for module in ("ni_assembly_mcp.niassembly_client", "ni_assembly_mcp.http_client"):
        monkeypatch.setattr(f"{module}.settings", test_settings)
