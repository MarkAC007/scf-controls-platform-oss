"""Reading an organisation's assurance policy (#787, #803).

One resolver, because two call sites need the same answer and the interesting
part is what happens when there is no row. An organisation that has never
touched these settings has no policy row at all, and that absence has to read as
"today's behaviour" everywhere — not as `None`, not as an exception, and
certainly not as a different default in each caller.

So the absence is given a name. `DEFAULT_ASSURANCE_POLICY` is a real object with
both rules off, returned whenever the row is missing, and every caller works
with a policy object rather than a nullable row. There is nowhere left to forget
the default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import OrganizationAssurancePolicy


@dataclass(frozen=True)
class AssurancePolicy:
    """What an organisation requires before it trusts a review.

    Frozen because a policy read for one request must not be mutated by the code
    that acts on it — a rule that can be turned off downstream of the place it
    was read is not a policy.
    """

    require_evidence_attestation: bool = False
    require_reviewer_independence: bool = False


#: What an organisation with no policy row gets: today's behaviour, exactly.
DEFAULT_ASSURANCE_POLICY = AssurancePolicy()


async def get_assurance_policy(
    db: AsyncSession, organization_id: UUID
) -> AssurancePolicy:
    """The organisation's assurance policy, or the default when it has none."""
    row: Optional[OrganizationAssurancePolicy] = (
        await db.execute(
            select(OrganizationAssurancePolicy).where(
                OrganizationAssurancePolicy.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()

    if row is None:
        return DEFAULT_ASSURANCE_POLICY

    # Read through explicit fields rather than returning the ORM row: the
    # caller gets an immutable value it cannot accidentally write back through,
    # and a column added later cannot leak into behaviour without a decision.
    return AssurancePolicy(
        require_evidence_attestation=bool(row.require_evidence_attestation),
        require_reviewer_independence=bool(row.require_reviewer_independence),
    )
