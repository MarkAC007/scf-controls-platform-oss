"""
Alembic Environment Configuration for CG SCF Backend.

This module configures Alembic to work with async SQLAlchemy and
loads the database URL from environment variables for security.
"""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import the Base and all models to ensure metadata is populated
from database import Base
import models  # noqa: F401 - User data models
import catalog_models  # noqa: F401 - SCF catalog models

# This is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
# disable_existing_loggers=False preserves application loggers configured in main.py.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Set the SQLAlchemy URL from environment variable
# This overrides the placeholder in alembic.ini
database_url = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf"
)
config.set_main_option("sqlalchemy.url", database_url)

# Target metadata for autogenerate support
# This includes all models registered with Base
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine.
    Useful for generating SQL scripts without a database connection.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Include schema changes in autogenerate
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Execute migrations synchronously within a connection context."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Include schema changes in autogenerate
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine.

    Creates an async engine and associates a connection with the context.
    This is the primary mode for running migrations against a live database.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Handles both standalone execution (alembic CLI) and execution
    from within an existing async context (FastAPI startup).
    """
    try:
        # Check if there's already a running event loop
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop - safe to use asyncio.run()
        loop = None

    if loop is not None:
        # Already in an async context (e.g., FastAPI startup)
        # Create a new loop in a thread to avoid nested loop issues
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, run_async_migrations())
            future.result()  # Wait for completion
    else:
        # Standalone execution (alembic CLI)
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
