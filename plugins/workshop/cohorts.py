"""Who is in which cohort: one registry, asked by everything that separates people.

Two parts of this plugin need to know which room an account belongs to, and
neither should know where rooms come from (PLAN.md §59):

  * audience.py, the wall between cohorts on the scoreboard and the user
    pages (§43, §50);
  * scope.py, the campus and session picker of the supervision pages (§50).

Both used to import Jump's tables and the external door's field. A third way
in would have meant a third import in each, and taking Jump out of this
plugin would have meant editing both. So the ways in come here instead: each
one registers a `CohortProvider`, and audience.py and scope.py read only this
module.

**A provider answers four questions**, two for each reader:

  * `memberships()`: `{user_id: value}` for every account it has filed. The
    audience key of such an account is `(provider.name, value)`. The name is
    the tag that keeps two providers' values apart (a code and a session id
    can spell the same thing);
  * `groups()`: the campuses somebody is in, as `(ref, label)`;
  * `rooms(ref)`: the sessions of one campus somebody is in, as `(id, label)`,
    newest first;
  * `members(ref, room_id)`: the ids of the accounts in that campus, or in
    that one session of it, as a query `Users.id.in_()` takes.

A `ref` is the provider's own text and nobody else reads into it. It has to be
unique across providers, since it travels in a query string with nothing
beside it: Jump's is `<kid>/<campus>` and a key id starts with a letter or a
digit, so any other provider starts its refs with something else.

**Order is precedence.** An account two providers both claim belongs to the
first one registered. `__init__.py` loads the doors in the order that has
always been true: the external code first, then the sessions.

A provider with nothing to say about one of the two readers leaves the
default: no membership, no group.
"""
from flask import g

_PROVIDERS = {}


class CohortProvider:
    """One source of cohorts. Subclass it and `register()` an instance."""

    # The tag of the audience keys this provider hands out. Unique.
    name = ""

    def memberships(self):
        """`{user_id: value}`: one query, whatever the number of accounts."""
        return {}

    def groups(self):
        """`[(ref, label)]`: the campuses somebody is in."""
        return []

    def rooms(self, ref):
        """`[(id, label)]`: the sessions of that campus, newest first."""
        return []

    def members(self, ref, room_id=None):
        """A query of the user ids in that campus, or in one session of it."""
        raise NotImplementedError


def register(provider):
    """Add a provider, or replace the one of the same name.

    Replacing rather than refusing: `load()` runs once per app, and a test
    process that builds two apps registers everything twice.
    """
    _PROVIDERS[provider.name] = provider


def providers():
    """The registered providers, in registration order."""
    return list(_PROVIDERS.values())


def memberships():
    """`[(name, {user_id: value})]` in precedence order, read once per request.

    The scoreboard is a list, and a lookup per row would turn one page into a
    hundred round trips.
    """
    if "ws_cohorts" not in g:
        g.ws_cohorts = [(p.name, p.memberships()) for p in providers()]
    return g.ws_cohorts
