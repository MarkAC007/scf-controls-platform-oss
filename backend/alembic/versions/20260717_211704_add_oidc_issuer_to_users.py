"""Add users.oidc_issuer and composite (oidc_issuer, google_sub) identity

Issue #699 (bundled user directory / generic OIDC). The user identity moves from
a single-issuer google_sub to the composite (oidc_issuer, google_sub) so the same
subject value from two different IdPs is two distinct users.

upgrade:
  - add nullable users.oidc_issuer;
  - backfill existing real logins to the Google Accounts issuer (pending-link
    placeholders keep NULL — the first-login relink path stamps their issuer);
  - drop the old single-column unique on google_sub (Postgres auto-named
    'users_google_sub_key' — the inline unique=True in models.py) and replace it
    with the composite unique 'uq_users_oidc_issuer_google_sub'.

Revision ID: wx4y5z6a7b8c
Revises: uv3w4x5y6z7a
Create Date: 2026-07-17 21:17:04
"""
from alembic import op
import sqlalchemy as sa

revision = 'wx4y5z6a7b8c'
down_revision = 'uv3w4x5y6z7a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('oidc_issuer', sa.String(length=255), nullable=True))

    # Backfill real logins to the Google issuer. Pending-link placeholders
    # (google_sub LIKE 'pending:%') stay NULL until first login relinks them.
    op.execute(
        "UPDATE users SET oidc_issuer = 'https://accounts.google.com' "
        "WHERE google_sub NOT LIKE 'pending:%'"
    )

    # 'users_google_sub_key' is the Postgres auto-generated name for the inline
    # unique=True previously declared on User.google_sub.
    op.drop_constraint('users_google_sub_key', 'users', type_='unique')
    op.create_unique_constraint(
        'uq_users_oidc_issuer_google_sub', 'users', ['oidc_issuer', 'google_sub']
    )


def downgrade() -> None:
    op.drop_constraint('uq_users_oidc_issuer_google_sub', 'users', type_='unique')
    op.create_unique_constraint('users_google_sub_key', 'users', ['google_sub'])
    op.drop_column('users', 'oidc_issuer')
