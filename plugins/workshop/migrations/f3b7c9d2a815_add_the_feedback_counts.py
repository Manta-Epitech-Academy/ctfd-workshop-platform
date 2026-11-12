"""Add the feedback counts

Revision ID: f3b7c9d2a815
Revises: e2a94b6d1c73
Create Date: 2026-11-12

What a participant says about a step is a pick from a fixed list (choices.py,
PLAN.md §67). `workshop_step_feedback` counts each choice per cohort, content,
step and subject version, with no account on it; `workshop_feedback_given`
says an account has sent the list for a step, and not what it picked.

CTFd's own `ratings` table is left as it is: its rows are no longer read or
written by the plugin, and erasing what an instance already holds is that
instance's decision, not a migration's.

Idempotent like the others: `create_all()` has built both tables from the
models by the time this runs, so in practice it creates nothing.
"""
import sqlalchemy as sa

from CTFd.plugins.migrations import get_all_tables

revision = "f3b7c9d2a815"
down_revision = "e2a94b6d1c73"
branch_labels = None
depends_on = None

COUNTS = "workshop_step_feedback"
GIVEN = "workshop_feedback_given"


def upgrade(op=None):
    tables = get_all_tables(op=op)
    if COUNTS not in tables:
        op.create_table(
            COUNTS,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("cohort", sa.String(length=96), nullable=False),
            sa.Column("content", sa.String(length=64), nullable=False),
            sa.Column("step", sa.String(length=191), nullable=False),
            sa.Column("version", sa.String(length=40), nullable=False),
            sa.Column("choice", sa.String(length=32), nullable=False),
            sa.Column("count", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("cohort", "content", "step", "version",
                                "choice", name="uq_workshop_step_feedback"),
        )
    if GIVEN not in tables:
        op.create_table(
            GIVEN,
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"],
                                    ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id", "challenge_id"),
        )


def downgrade(op=None):
    tables = get_all_tables(op=op)
    for name in (GIVEN, COUNTS):
        if name in tables:
            op.drop_table(name)
