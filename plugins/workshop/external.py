"""What is left of the external door: the cohorts of the accounts it made.

`/external/join` was a hidden second way in, with codes of its own and an
account made from a name, an e-mail and a password (PLAN.md §42). It is
retired (§64): the token door admits the same groups and asks for none of
the three (§60, §61). The route, its form, its admin page and its two
settings are gone.

What stays is what its accounts still need:

  * **the accounts themselves.** They are ordinary participants with a
    password, so they sign in where staff do (`/login?staff=1`) and nothing
    about them changed;
  * **the custom field `External code`**, the code each one came in on. It is
    CTFd's own data, so it is in `/admin/users` and in an export whether this
    module exists or not, and nothing here writes it any more;
  * **the cohort it stands for.** An account that carries a code still sees
    its own cohort only (audience.py, §43), and the cohort can still be shut
    out in one click, from the registration codes' page (codes.py).

The stored settings `workshop_external_codes` and `workshop_external_enabled`
are left where they are: nothing reads them, and a migration to delete two
rows of `config` would be the only code here that touches them.
"""
from CTFd.models import UserFieldEntries, UserFields, Users, db

from .cohorts import CohortProvider
from .cohorts import register as register_cohorts

FIELD_NAME = "External code"


def code_field():
    """The custom field the door wrote, or None on an instance that never
    opened it."""
    return UserFields.query.filter_by(name=FIELD_NAME).first()


def accounts_by_code():
    """`{code: [Users]}`, busiest cohort first.

    Read from the field every time rather than kept in a counter: a counter
    drifts the first time somebody deletes an account from `/admin/users`, and
    this cannot.
    """
    field = code_field()
    if field is None:
        return {}
    rows = (db.session.query(UserFieldEntries, Users)
            .join(Users, Users.id == UserFieldEntries.user_id)
            .filter(UserFieldEntries.field_id == field.id).all())
    out = {}
    for entry, user in rows:
        # An empty value is no code: such an account is in nobody's cohort.
        if entry.value:
            out.setdefault(entry.value, []).append(user)
    for users in out.values():
        users.sort(key=lambda u: u.id)
    return dict(sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0])))


class ExternalCohorts(CohortProvider):
    """A cohort is a code: every account that came in on it (§43, §59).

    No campus and no session: the supervision pages' picker (scope.py) is
    about rooms, and a code says nothing of where its group sat. These
    accounts are counted when no scope is set, as they always were.
    """
    name = "code"

    def memberships(self):
        field = code_field()
        if field is None:
            return {}
        return {e.user_id: e.value
                for e in UserFieldEntries.query.filter_by(field_id=field.id).all()
                if e.value}


def load_external(app):
    register_cohorts(ExternalCohorts())
