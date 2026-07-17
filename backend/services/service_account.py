"""
Service-account seeding for static master API key attribution.
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import AsyncSessionLocal, DB_INIT_MODE
from models import User

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_EMAIL = "machine@scf.local"
SERVICE_ACCOUNT_GOOGLE_SUB = "static-api-key-service-account"
SERVICE_ACCOUNT_NAME = "API Service Account"

_service_account_id: Optional[str] = None


async def seed_service_account() -> None:
    """
    Ensure the static API key service-account user exists and cache its id.
    """
    global _service_account_id

    if DB_INIT_MODE != "alembic":
        logger.warning("Skipping service-account seed because DB_INIT_MODE=%s", DB_INIT_MODE)
        return

    async with AsyncSessionLocal() as session:
        stmt = (
            pg_insert(User)
            .values(
                google_sub=SERVICE_ACCOUNT_GOOGLE_SUB,
                email=SERVICE_ACCOUNT_EMAIL,
                display_name=SERVICE_ACCOUNT_NAME,
            )
            .on_conflict_do_update(
                index_elements=[User.google_sub],
                set_={
                    "email": SERVICE_ACCOUNT_EMAIL,
                    "display_name": SERVICE_ACCOUNT_NAME,
                },
            )
        )
        await session.execute(stmt)
        await session.commit()

        result = await session.execute(
            select(User.id).where(User.google_sub == SERVICE_ACCOUNT_GOOGLE_SUB)
        )
        service_account_id = result.scalar_one_or_none()
        if service_account_id is None:
            raise RuntimeError("Service-account seed did not resolve a user id")

        _service_account_id = str(service_account_id)
        logger.info("Service-account user ready: %s", SERVICE_ACCOUNT_EMAIL)


def get_service_account_id() -> Optional[str]:
    """
    Return the cached service-account user id without performing database I/O.
    """
    return _service_account_id
