"""Add the supervisor table

Revision ID: 7d2f5a9c41be
Revises: 4c1e97a2b0d5
Create Date: 2026-09-21

One row per account that holds a plugin-side role (PLAN.md §32). The role is
not a `Users.type` on purpose — see the staff.py docstring for why a third
type would break the user API — so it needs a table of its own. Cascades with
the account: deleting a supervisor in /admin/users takes the role with it.

Idempotent like the others: on a fresh instance `create_all()` has already
built the table by the time this runs.
"""
import sqlalchemy as sa

from CTFd.plugins.migrations import get_all_tables

revision = "7d2f5a9c41be"
down_revision = "4c1e97a2b0d5"
branch_labels = None
depends_on = None

TABLE = "workshop_staff"


def upgrade(op=None):
    if TABLE in get_all_tables(op=op):
        return
    op.create_table(
        TABLE,
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=True),
        sa.Column("granted_by", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade(op=None):
    if TABLE in get_all_tables(op=op):
        op.drop_table(TABLE)
