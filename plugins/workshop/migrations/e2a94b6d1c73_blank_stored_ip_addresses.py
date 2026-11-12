"""Blank the IP addresses already stored

Revision ID: e2a94b6d1c73
Revises: c5d81e4a7f30
Create Date: 2026-11-12

The plugin no longer lets an address reach `submissions` or `tracking`
(privacy.py, PLAN.md §65). This takes out the ones written before that.

Both tables are CTFd's own. No column changes: the value goes, the row stays,
so every solve, fail and score is what it was.

The tracker used to keep one row per account and address; with the address
gone an account may be left with several rows that differ only by date. They
are harmless and left in place: the tracker finds the first and moves its
date.

There is no way back: a downgrade cannot restore what was erased, and does
nothing.
"""
import sqlalchemy as sa

revision = "e2a94b6d1c73"
down_revision = "c5d81e4a7f30"
branch_labels = None
depends_on = None


def upgrade(op=None):
    for name in ("submissions", "tracking"):
        table = sa.table(name, sa.column("ip", sa.String))
        op.execute(table.update().where(table.c.ip.isnot(None)).values(ip=None))


def downgrade(op=None):
    pass
