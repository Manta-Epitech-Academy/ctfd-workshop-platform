"""Add the supervisor notes

Revision ID: 9d4e2b7c6a10
Revises: f3b7c9d2a815
Create Date: 2026-11-15

A subject may carry a note per step for the supervisor (notes.py, PLAN.md
§69). `workshop_supervisor_note` holds one per step, and goes with the step
when the step is deleted.

Idempotent like the others: `create_all()` has built the table from the model
by the time this runs, so in practice it creates nothing.
"""
import sqlalchemy as sa

from CTFd.plugins.migrations import get_all_tables

revision = "9d4e2b7c6a10"
down_revision = "f3b7c9d2a815"
branch_labels = None
depends_on = None

NOTES = "workshop_supervisor_note"


def upgrade(op=None):
    if NOTES not in get_all_tables(op=op):
        op.create_table(
            NOTES,
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"],
                                    ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("challenge_id"),
        )


def downgrade(op=None):
    if NOTES in get_all_tables(op=op):
        op.drop_table(NOTES)
