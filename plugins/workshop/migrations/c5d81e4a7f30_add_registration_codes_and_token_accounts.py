"""Add registration codes and the accounts they admit

Revision ID: c5d81e4a7f30
Revises: 8b3e6f1d9a24
Create Date: 2026-11-10

The way in that needs no Jump (PLAN.md §60): `workshop_registration_code`
holds one row per code, which is one cohort, and `workshop_token_account`
says which code an account came in on and what it signs in with.

Idempotent like the others. On a fresh instance, and on an existing one,
`create_all()` has built both tables from the models by the time this runs:
they are new tables, and new tables are the one thing it does. So this file
creates nothing in practice. It is here because the ledger is the record of
what the schema is, and because the next change to either table will be an
ALTER that has to start from somewhere.
"""
import sqlalchemy as sa

from CTFd.plugins.migrations import get_all_tables

revision = "c5d81e4a7f30"
down_revision = "8b3e6f1d9a24"
branch_labels = None
depends_on = None

CODES = "workshop_registration_code"
ACCOUNTS = "workshop_token_account"
# Named here and on the model alike, so a downgrade finds the constraint
# whichever of `create_all()` or this file created it.
FK_CODE = "fk_workshop_token_account_code"
INDEX_CODE = "ix_workshop_token_account_code_id"


def upgrade(op=None):
    tables = get_all_tables(op=op)
    if CODES not in tables:
        op.create_table(
            CODES,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("code_key", sa.String(length=32), nullable=False),
            sa.Column("campus", sa.String(length=128), nullable=True),
            sa.Column("event", sa.String(length=128), nullable=True),
            sa.Column("event_date", sa.Date(), nullable=True),
            sa.Column("content", sa.String(length=64), nullable=True),
            sa.Column("valid_from", sa.DateTime(), nullable=True),
            sa.Column("valid_until", sa.DateTime(), nullable=True),
            sa.Column("cap", sa.Integer(), nullable=True),
            sa.Column("retired", sa.Boolean(), nullable=False),
            sa.Column("created", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code_key"),
        )
    if ACCOUNTS not in tables:
        op.create_table(
            ACCOUNTS,
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("code_id", sa.Integer(), nullable=False),
            sa.Column("signin_hash", sa.String(length=64), nullable=False),
            sa.Column("created", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                    ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id"),
            sa.UniqueConstraint("signin_hash"),
        )
        # The index before the constraint: MariaDB gives a foreign key an
        # index of its own when the column has none, and this one would then
        # be a second.
        op.create_index(INDEX_CODE, ACCOUNTS, ["code_id"])
        op.create_foreign_key(FK_CODE, ACCOUNTS, CODES, ["code_id"], ["id"])


def downgrade(op=None):
    tables = get_all_tables(op=op)
    if ACCOUNTS in tables:
        op.drop_table(ACCOUNTS)
    if CODES in tables:
        op.drop_table(CODES)
