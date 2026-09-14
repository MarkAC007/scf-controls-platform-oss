"""Record the bundled-Keycloak identity created for an organisation invite.

Issue #984. Inviting someone created a row in `organization_invites` and an
email, but no identity in the bundled Keycloak realm — so the invitee followed
the link, was bounced to the OP, and had no account to log in with. Provisioning
now happens at invite time when the platform runs the bundled IdP, and these two
columns are the record that it happened.

Both are nullable, and on a bring-your-own-OIDC install both stay null forever:
the platform does not own that directory and must not write to it. A null
`idp_user_id` therefore means one of two different things — "external IdP, not
our business" or "bundled IdP, not provisioned" — and it is the *configuration*,
not the column, that says which. The pending-invite list resolves that pair into
`external` / `not_in_idp` for the reader.

`idp_user_id` is a plain varchar rather than a UUID column on purpose. It holds
an opaque identifier minted by an external system; Keycloak happens to mint
UUIDs today, but nothing here should break if a future realm or a different OP
mints something else, and there is no foreign key it could ever point at.

Revision ID: invidpcols1
Revises: evstorcfg1a1
Create Date: 2026-09-14 15:43:51
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "invidpcols1"
down_revision: Union[str, None] = "evstorcfg1a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "organization_invites"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("idp_user_id", sa.String(64), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("idp_provisioned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(TABLE, "idp_provisioned_at")
    op.drop_column(TABLE, "idp_user_id")
