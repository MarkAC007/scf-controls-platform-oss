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
    _reset_storage_config()
    yield
    secrets.reset_caches()
    try:
        from services import crypto

        crypto.reset()
    except ImportError:
        pass
    _reset_storage_config()


def _reset_storage_config():
    """Cold storage-configuration cache, and no database behind it.

    Evidence storage resolves through `services.storage_config`, which reads
    configuration rows from the database and caches them for a minute. In the
    unit suite there is no database — and on a developer's machine there may be
    one listening on the default port that these tests have no business
    touching — so the shared resolver is pointed at an empty row set. A test
    that wants stored rows supplies its own loader, or builds its own
    `StorageConfigResolver`.
    """
    try:
        from services import storage_config
    except ImportError:
        return
    storage_config.reset_caches()
    storage_config.use_loader(lambda: [])
