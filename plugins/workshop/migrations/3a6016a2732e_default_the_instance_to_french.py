"""Default the instance to French

Revision ID: 3a6016a2732e
Revises: 7d2f5a9c41be
Create Date: 2026-09-29

The participant path is translated (PLAN.md §29.4) and the content is French,
so an instance should speak French to a visitor who has not chosen otherwise
(PLAN.md §42). Until this revision `default_locale` was written by
`tools/provision.py setup`, which only the Compose instances go through. The
k8s instances are configured by `PRESET_CONFIGS` in their overlay and never
had it: ctf-1000 served English to any browser not set to French, and
`get_locale()` fell through to `Accept-Language`.

A migration rather than the two other places this could live:

  - `CTFd.constants.setup.DEFAULTS` would answer for a missing key, but
    `_get_config` also reports an *empty* value as missing — so the blank
    choice on Admin → Config → Localization, "auto-detect", would silently
    read as French, and the admin page would be lying about what it does.
  - A `set_config` in `load()` runs on every boot of every worker, and would
    race itself on a fresh instance.

A revision runs once per instance and is recorded. It only inserts when there
is no `default_locale` row at all, so whatever an admin chose — a language,
or the blank one stored as `""` — is theirs, on this deploy and every later
one. A participant still overrides it per account in /settings, which
`get_locale()` checks first.

`upgrade_plugin` skips migrations on SQLite (it only runs `create_all()`), so a
SQLite instance does not get this. None is deployed.
"""
import sqlalchemy as sa

from CTFd.cache import clear_config

revision = "3a6016a2732e"
down_revision = "7d2f5a9c41be"
branch_labels = None
depends_on = None

KEY = "default_locale"
LOCALE = "fr"

# Core's `config` table, declared here rather than imported: a migration is
# dated, and the model may move on. Core constructs also quote `key`, which is
# a reserved word in MySQL.
config = sa.table("config", sa.column("key", sa.Text), sa.column("value", sa.Text))


def upgrade(op=None):
    conn = op.get_bind()
    if conn.execute(sa.select(config.c.key).where(config.c.key == KEY)).first():
        return
    conn.execute(config.insert().values(key=KEY, value=LOCALE))
    # A k8s pod restart keeps Redis, which may still hold the missing value
    # memoized from before this ran — for as long as the cache timeout, five
    # minutes of English on an instance that is already French.
    clear_config()


def downgrade(op=None):
    # Nothing to undo. By the time anyone downgrades, the row may just as well
    # be an admin's own choice, and deleting it would reset their instance to
    # the browser's language without a word.
    pass
