"""Record the Jump session an account entered from

Revision ID: 8b3e6f1d9a24
Revises: 3a6016a2732e
Create Date: 2026-10-08

An instance lives for about two years and one campus can run the same subject
at several Coding Clubs, so one scoreboard ended up holding every room that
ever passed through. The ticket now names the Jump session (PLAN.md §50):
`workshop_jump_session` holds one row per session, and each link points at the
session its account first entered from.

Idempotent like the others. On a fresh instance `create_all()` has built the
table and the column from the models by the time this runs. On an existing one
it has built the new table, never an ALTER, so the column is added here. No
backfill: nothing recorded a session before this, and a link is filed on the
account's next entry.
"""
import sqlalchemy as sa

from CTFd.plugins.migrations import get_all_tables, get_columns_for_table

revision = "8b3e6f1d9a24"
down_revision = "3a6016a2732e"
branch_labels = None
depends_on = None

SESSIONS = "workshop_jump_session"
LINKS = "workshop_jump_link"
COLUMN = "session_id"
# Named here and on the model alike, so a downgrade finds the constraint
# whichever of `create_all()` or this file created it.
FK_NAME = "fk_workshop_jump_link_session"
INDEX_NAME = "ix_workshop_jump_link_session_id"


def upgrade(op=None):
    tables = get_all_tables(op=op)
    if SESSIONS not in tables:
        op.create_table(
            SESSIONS,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("jump_kid", sa.String(length=64), nullable=False),
            sa.Column("session_key", sa.String(length=64), nullable=False),
            sa.Column("label", sa.String(length=128), nullable=False),
            sa.Column("campus_key", sa.String(length=64), nullable=False),
            sa.Column("campus_label", sa.String(length=128), nullable=False),
            sa.Column("created", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("jump_kid", "session_key"),
        )
        op.create_index("ix_workshop_jump_session_campus_key", SESSIONS,
                        ["campus_key"])
    if LINKS not in tables:
        return
    if COLUMN in get_columns_for_table(op, LINKS, names_only=True):
        return
    op.add_column(LINKS, sa.Column(COLUMN, sa.Integer(), nullable=True))
    # The index before the constraint: MariaDB gives a foreign key an index of
    # its own when the column has none, and this one would then be a second.
    op.create_index(INDEX_NAME, LINKS, [COLUMN])
    op.create_foreign_key(FK_NAME, LINKS, SESSIONS, [COLUMN], ["id"],
                          ondelete="SET NULL")


def downgrade(op=None):
    tables = get_all_tables(op=op)
    if LINKS in tables and COLUMN in get_columns_for_table(
            op, LINKS, names_only=True):
        op.drop_constraint(FK_NAME, LINKS, type_="foreignkey")
        op.drop_index(INDEX_NAME, table_name=LINKS)
        op.drop_column(LINKS, COLUMN)
    if SESSIONS in tables:
        op.drop_table(SESSIONS)
