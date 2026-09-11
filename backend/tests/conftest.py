"""
Pytest configuration for backend tests.
"""
import os
import sys

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _reset_secret_caches():
    """Every test starts with cold secret caches.

    Without this a value resolved by one test survives into the next, so a test
    that sets an environment variable and asserts the new value passes for the
    wrong reason — or fails depending on file order.
    """
    from services import secrets

    secrets.reset_caches()
    try:
        from services import crypto

        crypto.reset()
    except ImportError:
        pass
    yield
    secrets.reset_caches()
    try:
        from services import crypto

        crypto.reset()
    except ImportError:
        pass
