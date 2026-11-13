"""Which room the supervision pages are about (PLAN.md §50).

The supervision pages (stats, answers, submissions, feedback) are unfiltered
by design (§32): staff are the people who have to answer "where is everyone".
On an instance that serves several events, "everyone" is every Coding Club
that ever ran the subject, and the supervisor standing in today's room wants
today's.
So the pages take a scope: a campus, then optionally one of its sessions.

**One population, read by every page.** `participant_criteria(scope)` is the
filter each of the four applies to `Users`: participants only (no admin, no
hidden account, the §32 rule) and, when a scope is set, only accounts filed
under it. `per_challenge` is the per-step count the three reports share. The
pages cannot disagree about who is in the room because none of them decides.

**Campuses and sessions come from the cohort providers** (cohorts.py, §59),
and this module names none of them. A campus is a `ref`, the provider's own
text, opaque here and unique across providers; Jump's is `<kid>/<campus>`
(jump.py `JumpCohorts`), because one instance can serve a development and a
production Jump whose ids are unrelated. A session is an id that means
something only inside its campus.

**Remembered, not repeated.** A choice made with `?campus=&session=` is kept
in the Flask session, so the supervisor picks their campus once and every
page, link and CSV export follows it; `?campus=` with no value goes back to
the whole instance.

**A scope names a room somebody is in, or it falls back.** The rooms that
exist, for the pages as for the picker, are the rooms a provider lists, and a
provider lists only rooms somebody is filed in. A stored scope that names no
such room (one emptied since it was picked, a session of another campus, an
id that never existed) falls back to the campus, and a campus with none to
the whole instance. Filtering on it instead would show an empty room under a
picker that cannot name it.

An account no provider has filed (an admin's hand-made account, a Jump
account that has not entered since tickets started naming a session) is in no
campus, so it is counted only when no scope is set.
"""
from dataclasses import dataclass
from typing import Optional

from flask import request, session

from CTFd.models import Users, db

from .cohorts import providers

SESSION_KEY = "ws_staff_scope"


@dataclass(frozen=True)
class Scope:
    # The campus, as the ref its provider handed out and the picker submits,
    # or None for the whole instance.
    campus: Optional[str] = None
    session_id: Optional[int] = None


WHOLE_INSTANCE = Scope()


def _owner(ref):
    """The provider that lists this campus, or None."""
    if not ref:
        return None
    for provider in providers():
        if any(ref == known for known, _label in provider.groups()):
            return provider
    return None


def _checked(ref, session_id):
    """The scope these values name, or the nearest one somebody is in."""
    owner = _owner(ref)
    if owner is None:
        return WHOLE_INSTANCE
    if session_id is not None and not any(
            session_id == known for known, _label in owner.rooms(ref)):
        session_id = None
    return Scope(ref, session_id)


def _stored():
    """`(ref, session_id)` as the Flask session holds it.

    A session written before §59 holds `[kid, campus, session_id]`, the two
    halves of Jump's ref apart. It is read as what it meant, so a supervisor
    signed in across the upgrade keeps their room.
    """
    stored = session.get(SESSION_KEY) or [None, None]
    if len(stored) == 3:
        kid, campus, session_id = stored
        return (f"{kid}/{campus}" if kid and campus else None), session_id
    if len(stored) == 2:
        return stored[0], stored[1]
    return None, None


def current_scope():
    """The scope this request is about, recording a new choice if it makes one."""
    args = request.args
    if "campus" in args:
        scope = _checked((args.get("campus") or "").strip(),
                         args.get("session", type=int))
        session[SESSION_KEY] = [scope.campus, scope.session_id]
        return scope
    return _checked(*_stored())


def participant_criteria(scope):
    """The `Users` filter every supervision page applies. See the docstring."""
    criteria = [Users.type != "admin", Users.hidden == False]  # noqa: E712
    owner = _owner(scope.campus)
    if owner is not None:
        criteria.append(Users.id.in_(
            owner.members(scope.campus, scope.session_id)))
    return criteria


def per_challenge(model, scope):
    """challenge id -> how many rows of `model` the scoped participants produced.

    `Solves`, `Fails` or `Ratings`: any table with a `user_id` and a
    `challenge_id`. A solve is unique per account and step, so on `Solves`
    this is also the number of people who solved the step.
    """
    rows = (db.session.query(model.challenge_id, db.func.count(model.id))
            .join(Users, Users.id == model.user_id)
            .filter(*participant_criteria(scope))
            .group_by(model.challenge_id).all())
    return dict(rows)


def picker():
    """What `workshop_scope.html` draws: the choices, and which one is current.

    Campuses are listed by name, and a campus's sessions in the order its
    provider gives them, newest first: the order a supervisor looks for
    today's room in. Nothing to draw (an instance where no provider has filed
    anybody) is an empty list, and the template then draws nothing at all.

    Only rooms somebody is filed in, the same rooms `_checked` accepts, so
    the current scope is always one of the choices drawn.
    """
    scope = current_scope()
    campuses = [group for provider in providers() for group in provider.groups()]
    owner = _owner(scope.campus)
    return {
        "scope": scope,
        "campuses": sorted(campuses, key=lambda kv: kv[1].lower()),
        "sessions": owner.rooms(scope.campus) if owner is not None else [],
    }


def load_scope(app):
    app.jinja_env.globals["ws_scope_picker"] = picker
