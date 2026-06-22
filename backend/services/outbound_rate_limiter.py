"""
Thread-safe rate limiter for outbound API calls to external sources.

Each source has its own rate limit:
    - HIBP: 1.5 seconds between calls (paid API tier)
    - NVD: 6 seconds without API key, 0.6 seconds with key
    - CISA KEV: No rate limit (public feed, but 2s courtesy delay)
    - Regulatory: 2 seconds (generic web fetch)
"""
import os
import time
import threading
from typing import Dict


class OutboundRateLimiter:
    """Thread-safe per-source rate limiter using simple time-based gating."""

    # Default intervals in seconds
    DEFAULT_INTERVALS: Dict[str, float] = {
        "hibp": 1.5,
        "nvd": 6.0,
        "nvd_keyed": 0.6,
        "cisa_kev": 2.0,
        "regulatory": 2.0,
    }

    def __init__(self):
        self._locks: Dict[str, threading.Lock] = {}
        self._last_call: Dict[str, float] = {}
        self._global_lock = threading.Lock()

    def _get_lock(self, source: str) -> threading.Lock:
        """Get or create a lock for a specific source."""
        if source not in self._locks:
            with self._global_lock:
                if source not in self._locks:
                    self._locks[source] = threading.Lock()
        return self._locks[source]

    def wait(self, source: str) -> None:
        """Block until the rate limit window has elapsed for the given source.

        Args:
            source: One of 'hibp', 'nvd', 'cisa_kev', 'regulatory'.
                    For NVD, automatically uses 'nvd_keyed' interval if NVD_API_KEY is set.
        """
        # Resolve NVD interval based on API key presence
        interval_key = source
        if source == "nvd" and os.getenv("NVD_API_KEY"):
            interval_key = "nvd_keyed"

        interval = self.DEFAULT_INTERVALS.get(interval_key, 2.0)
        lock = self._get_lock(source)

        with lock:
            now = time.monotonic()
            last = self._last_call.get(source, 0.0)
            elapsed = now - last
            if elapsed < interval:
                time.sleep(interval - elapsed)
            self._last_call[source] = time.monotonic()


# Singleton instance
rate_limiter = OutboundRateLimiter()
