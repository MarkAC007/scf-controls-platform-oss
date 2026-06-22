"""
Redis client configuration for CG SCF.
Provides connection management and health check utilities.
"""
import os
import logging
from typing import Optional
from contextlib import asynccontextmanager

import redis.asyncio as redis
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Redis connection URL from environment
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Global Redis client instance
_redis_client: Optional[Redis] = None


async def get_redis_client() -> Redis:
    """
    Get or create a Redis client instance.
    Uses connection pooling for efficiency.
    """
    global _redis_client

    if _redis_client is None:
        _redis_client = redis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            socket_keepalive=True,
            # Periodic ping pings the connection every N seconds; if the underlying
            # socket has gone stale (e.g., Redis force-rebooted on the Azure side),
            # the next health_check call will detect it and reconnect transparently
            # rather than blocking the next real request indefinitely.
            health_check_interval=30,
        )
        logger.info(f"Redis client created for: {REDIS_URL.split('@')[-1] if '@' in REDIS_URL else REDIS_URL}")

    return _redis_client


async def close_redis_client() -> None:
    """
    Close the Redis client connection.
    Should be called on application shutdown.
    """
    global _redis_client

    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis client connection closed")


async def redis_health_check() -> dict:
    """
    Check Redis connection health.
    Returns a dict with status and info.
    """
    try:
        client = await get_redis_client()
        await client.ping()
        info = await client.info("server")
        return {
            "status": "healthy",
            "redis_version": info.get("redis_version", "unknown"),
            "connected_clients": info.get("connected_clients", "unknown"),
        }
    except redis.ConnectionError as e:
        logger.error(f"Redis connection error: {e}")
        return {
            "status": "unhealthy",
            "error": "connection_failed",
            "message": str(e),
        }
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": "unknown",
            "message": str(e),
        }


@asynccontextmanager
async def redis_connection():
    """
    Context manager for Redis connections.
    Useful for one-off operations where you want explicit control.

    Usage:
        async with redis_connection() as r:
            await r.set("key", "value")
    """
    client = await get_redis_client()
    try:
        yield client
    except redis.RedisError as e:
        logger.error(f"Redis error: {e}")
        raise


async def get_redis_dependency() -> Redis:
    """
    FastAPI dependency for getting Redis client.
    Use with Depends(get_redis_dependency) in route functions.
    """
    return await get_redis_client()
