"""
Caching utilities for CG SCF.
Provides decorators and helpers for Redis-based caching.
"""
import json
import hashlib
import logging
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, Union
from datetime import timedelta

from redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Type variable for generic return type
T = TypeVar("T")

# Default cache TTL (time to live) in seconds
DEFAULT_TTL = 300  # 5 minutes

# Cache key prefixes for different data types
CACHE_PREFIX = "scf:cache:"
CACHE_VERSION = "v1"  # Increment to invalidate all caches


def make_cache_key(*args, prefix: str = "", **kwargs) -> str:
    """
    Generate a consistent cache key from arguments.

    Args:
        *args: Positional arguments to include in key
        prefix: Optional prefix for the key
        **kwargs: Keyword arguments to include in key

    Returns:
        A unique, deterministic cache key string
    """
    # Create a hashable representation of args and kwargs
    key_parts = [CACHE_VERSION, prefix] if prefix else [CACHE_VERSION]

    # Add positional args
    for arg in args:
        if arg is not None:
            key_parts.append(str(arg))

    # Add sorted kwargs for consistency
    for key in sorted(kwargs.keys()):
        value = kwargs[key]
        if value is not None:
            key_parts.append(f"{key}={value}")

    # Create a hash for long keys
    key_string = ":".join(key_parts)
    if len(key_string) > 200:
        key_hash = hashlib.md5(key_string.encode()).hexdigest()[:16]
        key_string = f"{CACHE_PREFIX}{prefix}:{key_hash}"
    else:
        key_string = f"{CACHE_PREFIX}{key_string}"

    return key_string


def cached(
    ttl: Union[int, timedelta] = DEFAULT_TTL,
    prefix: str = "",
    key_builder: Optional[Callable[..., str]] = None,
):
    """
    Decorator to cache async function results in Redis.

    Args:
        ttl: Time to live in seconds or timedelta
        prefix: Cache key prefix (e.g., "controls", "evidence")
        key_builder: Optional custom function to build cache key

    Usage:
        @cached(ttl=300, prefix="controls")
        async def get_control(control_id: str):
            return await fetch_from_db(control_id)

        @cached(ttl=timedelta(hours=1), prefix="stats")
        async def get_statistics():
            return await compute_stats()
    """
    if isinstance(ttl, timedelta):
        ttl_seconds = int(ttl.total_seconds())
    else:
        ttl_seconds = ttl

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            # Build cache key
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                # Use function name and arguments
                func_prefix = f"{prefix}:{func.__name__}" if prefix else func.__name__
                cache_key = make_cache_key(*args, prefix=func_prefix, **kwargs)

            try:
                redis = await get_redis_client()

                # Try to get from cache
                cached_value = await redis.get(cache_key)
                if cached_value is not None:
                    logger.debug(f"Cache hit: {cache_key}")
                    return json.loads(cached_value)

                logger.debug(f"Cache miss: {cache_key}")

            except Exception as e:
                # Log error but don't fail - fall through to execute function
                logger.warning(f"Cache get failed for {cache_key}: {e}")

            # Execute the function
            result = await func(*args, **kwargs)

            # Store in cache
            try:
                redis = await get_redis_client()
                await redis.setex(
                    cache_key,
                    ttl_seconds,
                    json.dumps(result, default=str),
                )
                logger.debug(f"Cached: {cache_key} (TTL: {ttl_seconds}s)")
            except Exception as e:
                logger.warning(f"Cache set failed for {cache_key}: {e}")

            return result

        # Add method to invalidate this specific cache
        wrapper.invalidate = lambda *args, **kwargs: invalidate_cache(
            make_cache_key(
                *args,
                prefix=f"{prefix}:{func.__name__}" if prefix else func.__name__,
                **kwargs,
            )
        )

        return wrapper

    return decorator


async def invalidate_cache(key: str) -> bool:
    """
    Invalidate a specific cache key.

    Args:
        key: The cache key to invalidate

    Returns:
        True if the key was deleted, False otherwise
    """
    try:
        redis = await get_redis_client()
        result = await redis.delete(key)
        if result:
            logger.info(f"Cache invalidated: {key}")
        return bool(result)
    except Exception as e:
        logger.error(f"Failed to invalidate cache {key}: {e}")
        return False


async def invalidate_pattern(pattern: str) -> int:
    """
    Invalidate all cache keys matching a pattern.

    Args:
        pattern: Redis glob pattern (e.g., "ccf:cache:controls:*")

    Returns:
        Number of keys deleted
    """
    try:
        redis = await get_redis_client()

        # Find all matching keys
        keys = []
        async for key in redis.scan_iter(match=pattern, count=100):
            keys.append(key)

        if not keys:
            return 0

        # Delete all matching keys
        result = await redis.delete(*keys)
        logger.info(f"Invalidated {result} keys matching pattern: {pattern}")
        return result

    except Exception as e:
        logger.error(f"Failed to invalidate pattern {pattern}: {e}")
        return 0


async def invalidate_prefix(prefix: str) -> int:
    """
    Invalidate all cache keys with a specific prefix.

    Args:
        prefix: The prefix to match (e.g., "controls", "evidence")

    Returns:
        Number of keys deleted
    """
    pattern = f"{CACHE_PREFIX}{CACHE_VERSION}:{prefix}:*"
    return await invalidate_pattern(pattern)


async def clear_all_cache() -> int:
    """
    Clear all application cache entries.
    Use with caution!

    Returns:
        Number of keys deleted
    """
    pattern = f"{CACHE_PREFIX}*"
    return await invalidate_pattern(pattern)


async def get_cache_stats() -> dict:
    """
    Get cache statistics.

    Returns:
        Dict with cache info including key count, memory usage, etc.
    """
    try:
        redis = await get_redis_client()

        # Count cache keys
        key_count = 0
        async for _ in redis.scan_iter(match=f"{CACHE_PREFIX}*", count=100):
            key_count += 1

        # Get memory info
        info = await redis.info("memory")

        return {
            "cache_keys": key_count,
            "used_memory": info.get("used_memory_human", "unknown"),
            "used_memory_peak": info.get("used_memory_peak_human", "unknown"),
            "cache_prefix": CACHE_PREFIX,
            "cache_version": CACHE_VERSION,
        }

    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        return {
            "error": str(e),
            "cache_prefix": CACHE_PREFIX,
            "cache_version": CACHE_VERSION,
        }


class CacheNamespace:
    """
    Helper class for managing cache within a specific namespace.

    Usage:
        cache = CacheNamespace("controls")
        await cache.set("ctrl-001", control_data, ttl=300)
        data = await cache.get("ctrl-001")
        await cache.invalidate("ctrl-001")
        await cache.clear()
    """

    def __init__(self, namespace: str):
        self.namespace = namespace
        self.prefix = f"{CACHE_PREFIX}{CACHE_VERSION}:{namespace}"

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    async def get(self, key: str) -> Optional[Any]:
        """Get a value from cache."""
        try:
            redis = await get_redis_client()
            value = await redis.get(self._key(key))
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.warning(f"Cache get failed for {self._key(key)}: {e}")
            return None

    async def set(
        self, key: str, value: Any, ttl: int = DEFAULT_TTL
    ) -> bool:
        """Set a value in cache."""
        try:
            redis = await get_redis_client()
            await redis.setex(
                self._key(key),
                ttl,
                json.dumps(value, default=str),
            )
            return True
        except Exception as e:
            logger.warning(f"Cache set failed for {self._key(key)}: {e}")
            return False

    async def invalidate(self, key: str) -> bool:
        """Invalidate a specific key."""
        return await invalidate_cache(self._key(key))

    async def clear(self) -> int:
        """Clear all keys in this namespace."""
        return await invalidate_pattern(f"{self.prefix}:*")
