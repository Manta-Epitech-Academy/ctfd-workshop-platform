"""Add the Jump link and outbox tables

Revision ID: 1a0c7f3b9d21
Revises:
Create Date: 2026-09-15

This is revision 1, and it exists as much to start the ledger as to create
these two tables. `load()` calls `app.db.create_all()` first and always will —
the quiz and workspace tables predate this directory and are in no migration —
so on a fresh instance both tables are already there when this runs. That is
why it guards on `get_all_tables`: the useful work of revision 1 is recording
`workshop_alembic_version`, so revision 2 has somewhere to start from.

Revision 2 is what this is really for. `create_all()` never issues an ALTER, so
the first column the outbox gains (`last_error` was very nearly it) would
otherwise surface as an `OperationalError` at request time, on every instance
at once, with no migration path to write it into.
"""
import sqlalchemy as sa

from CTFd.plugins.migrations import get_all_tables

revision = "1a0c7f3b9d21"
down_revision = None
branch_labels = None
depends_on = None


def upgrade(op=None):
    tables = get_all_tables(op=op)

    if "workshop_jump_link" not in tables:
        op.create_table(
            "workshop_jump_link",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("jump_kid", sa.String(length=64), nullable=False),
            sa.Column("jump_talent_id", sa.String(length=64), nullable=False),
            sa.Column("created", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
            sa.UniqueConstraint("jump_kid", "jump_talent_id"),
        )
        op.create_index("ix_workshop_jump_link_jump_kid", "workshop_jump_link",
                        ["jump_kid"])

    if "workshop_jump_event" not in tables:
        op.create_table(
            "workshop_jump_event",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("jump_kid", sa.String(length=64), nullable=False),
            sa.Column("jump_talent_id", sa.String(length=64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("next_attempt", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created", sa.DateTime(), nullable=True),
            sa.Column("sent", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"],
                                    ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "challenge_id"),
        )
        op.create_index("ix_workshop_jump_event_user_id", "workshop_jump_event",
                        ["user_id"])
        op.create_index("ix_workshop_jump_event_status", "workshop_jump_event",
                        ["status"])
        # The drainer's only query is "pending and due", and it runs every few
        # seconds forever. This is the index it reads.
        op.create_index("ix_workshop_jump_event_next_attempt",
                        "workshop_jump_event", ["next_attempt"])


def downgrade(op=None):
    tables = get_all_tables(op=op)
    if "workshop_jump_event" in tables:
        op.drop_table("workshop_jump_event")
    if "workshop_jump_link" in tables:
        op.drop_table("workshop_jump_link")
