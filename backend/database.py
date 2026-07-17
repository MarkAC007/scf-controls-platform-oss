"""
Database configuration and session management for CG SCF.
Uses SQLAlchemy 2.0 with async support.
"""
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
import os
import logging

logger = logging.getLogger(__name__)

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf")

# Database initialisation mode:
# - "alembic" (default): Use Alembic migrations (recommended for production)
# - "create_all": Use SQLAlchemy create_all (for initial development only)
DB_INIT_MODE = os.getenv("DB_INIT_MODE", "alembic")

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("LOG_LEVEL", "info") == "debug",  # Log SQL in debug mode
    pool_pre_ping=True,  # Verify connections before using
    pool_size=5,  # Connection pool size
    max_overflow=10,  # Max connections beyond pool_size
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Create declarative base for models
Base = declarative_base()


async def get_db() -> AsyncSession:
    """
    Dependency for FastAPI to get database session.
    Yields a database session and closes it when done.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_db_session() -> AsyncSession:
    """
    Standalone async context manager for background tasks.
    Use this when you need a DB session outside of FastAPI dependency injection.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def run_alembic_migrations():
    """
    Run Alembic migrations programmatically.
    This is called during application startup to ensure the database schema is up to date.
    """
    from alembic.config import Config
    from alembic import command
    import os

    # Get the directory where this file is located
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    alembic_cfg_path = os.path.join(backend_dir, "alembic.ini")

    # Create Alembic config
    alembic_cfg = Config(alembic_cfg_path)

    # Set the script location relative to the config file
    alembic_cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))

    # Override the database URL from environment (security best practice)
    alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL)

    # Backend-side migration guard (upgrade design Part E): refuse illegal
    # version jumps / un-acked migrations BEFORE touching the schema, so the
    # default `compose up --build` fails closed. Runs only here (the lifespan
    # path); a direct `alembic upgrade head` on the CLI intentionally bypasses
    # it. run_migration_guard raises SystemExit(1) on refusal.
    from upgrade_guard import run_migration_guard, record_applied_version

    run_migration_guard(alembic_cfg, DATABASE_URL)

    logger.info("Running Alembic migrations...")
    try:
        # Upgrade to the latest revision
        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic migrations completed successfully")
    except Exception as e:
        logger.error(f"Alembic migration failed: {e}")
        raise

    # Record the applied platform version (append-only history) so the guard has
    # a floor to check on the next startup. Non-fatal on failure — the schema is
    # already up to date; a missing history row only makes the next guard run
    # treat this install as legacy.
    try:
        record_applied_version(DATABASE_URL)
    except Exception as e:
        logger.warning(f"Failed to record platform version in platform_upgrade_state: {e}")


async def init_db():
    """
    Initialise database schema.
    Called on application startup.

    The initialisation mode is controlled by the DB_INIT_MODE environment variable:
    - "alembic" (default): Run Alembic migrations (recommended)
    - "create_all": Use SQLAlchemy create_all (development fallback)
    """
    # Import models to ensure they're registered with Base.metadata
    # This includes both user data models and catalog models
    import models  # noqa: F401 - User data models
    import catalog_models  # noqa: F401 - SCF catalog models (reference data)

    if DB_INIT_MODE == "alembic":
        # Use Alembic migrations (recommended for production)
        logger.info("Database initialisation mode: Alembic migrations")
        run_alembic_migrations()
    else:
        # Fallback to create_all for initial development
        logger.warning("Database initialisation mode: create_all (not recommended for production)")
        async with engine.begin() as conn:
            logger.info("Creating database tables if they don't exist...")
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables ready")
