"""Shared test fixtures and configuration."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_db_cache() -> None:
    """Clear the API module's DB cache between tests to prevent state leaks."""
    import icm_engine.api as api_module

    api_module._db_cache.clear()
