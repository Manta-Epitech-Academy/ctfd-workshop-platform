"""Record the label a link row was created under

Revision ID: 4c1e97a2b0d5
Revises: 1a0c7f3b9d21
Create Date: 2026-09-15

A label namespaces the synthetic email of every account one Jump environment
creates, which is what keeps a dev talent off a production scoreboard. Until
this revision the only record of "who owns this label" was
`workshop_jump_keys`, and a configuration is not a history: a key id can be
renamed, or removed and replaced, and `tools/provision.py` rewrites the whole
map on every `setup`. Either one hands a label that already has accounts
behind it to a new key id, and the next returning talent meets the `user_id`
unique constraint on `workshop_jump_link` — a 403 they cannot act on, for
ever, with nothing but the login log to say why.

The column moves that fact into the row, where it cannot be rewritten by a
config edit. `jump.py:_link_namespaces` reads it and the settings page refuses
the pairing; `resolve_account` refuses it a second time for the provisioning
path, which never sees the form.

The backfill reads the label back out of the account's own address, because
`f"{talentId}@{label}.jump.invalid"` is what created it — exact for every row
that exists today, rather than a guess. That couples this file to the email
scheme as it stands in September 2026, which is the right place for such a
coupling: a one-shot, dated, and never read again. A row whose address does
not parse keeps a NULL and guards nothing, which is the honest failure — see
`_link_namespaces`.
"""
import sqlalchemy as sa

from CTFd.plugins.migrations import get_all_tables, get_columns_for_table

revision = "4c1e97a2b0d5"
down_revision = "1a0c7f3b9d21"
branch_labels = None
depends_on = None

TABLE = "workshop_jump_link"
EMAIL_SUFFIX = ".jump.invalid"


def label_of(email):
    """The namespace `synthetic_email()` put into this address, or None.

    Split on the *last* `@`: a talent id is any string the ticket carries, so
    `a@b@prod.jump.invalid` is reachable and its namespace is `prod`.
    """
    local, at, domain = (email or "").rpartition("@")
    if not local or not at or not domain.endswith(EMAIL_SUFFIX):
        return None
    return domain[:-len(EMAIL_SUFFIX)] or None


def upgrade(op=None):
    if TABLE not in get_all_tables(op=op):
        # A fresh instance: `create_all()` built the table from the model in
        # this same `load()`, column included, so there is nothing to alter and
        # nothing to backfill.
        return
    if "jump_label" in get_columns_for_table(op, TABLE, names_only=True):
        return

    op.add_column(TABLE, sa.Column("jump_label", sa.String(length=32),
                                   nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        f"SELECT l.id, u.email FROM {TABLE} l JOIN users u ON u.id = l.user_id"
    )).fetchall()
    for row in rows:
        label = label_of(row[1])
        if label:
            conn.execute(sa.text(
                f"UPDATE {TABLE} SET jump_label = :label WHERE id = :id"
            ).bindparams(label=label, id=row[0]))


def downgrade(op=None):
    if TABLE in get_all_tables(op=op) and "jump_label" in get_columns_for_table(
            op, TABLE, names_only=True):
        op.drop_column(TABLE, "jump_label")
