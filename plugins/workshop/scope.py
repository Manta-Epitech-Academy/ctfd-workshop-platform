"""Which room the supervision pages are about (PLAN.md §50).

The supervision pages (stats, answers, submissions, feedback) are unfiltered
by design (§32): staff are the people who have to answer "where is everyone".
On an instance that serves several events, "everyone" is every Coding Club
that ever ran the subject, and the supervisor standing in today's room wants
today's.
So the pages take a scope: a campus, then optionally one of its sessions, as
Jump named them in the tickets (jump.py `JumpSession`).

**One population, read by every page.** `participant_criteria(scope)` is the
filter each of the four applies to `Users`: participants only (no admin, no
hidden account, the §32 rule) and, when a scope is set, only accounts filed
under it. `per_challenge` is the per-step count the three reports share. The
pages cannot disagree about who is in the room because none of them decides.

**A campus is a Jump's campus.** One instance can serve a development and a
production Jump, whose ids are unrelated (jump.py `JumpSession`), so a campus
is named by its key id and its campus id together, `<kid>/<campus>` in the
query string: two Jumps that both call a campus `1` are two campuses here. A
key id is `[A-Za-z0-9_-]` (jump.py `KID_RE`), so the first `/` is the
separator whatever the campus id holds.

**Remembered, not repeated.** A choice made with `?campus=&session=` is kept
in the Flask session, so the supervisor picks their campus once and every
page, link and CSV export follows it; `?campus=` with no value goes back to
the whole instance. A scope that no longer names a session row (a session of
another campus, an id that never existed) falls back rather than filtering on
nothing, which would read as an empty room.

An account with no session (anyone who did not come in through Jump, or a Jump
account that has not entered since tickets started naming one) is in no
campus, so it is counted only when no scope is set.
"""
from dataclasses import dataclass
from typing import Optional

from flask import request, session

from CTFd.models import Users, db

from .jump import JumpLink, JumpSession

SESSION_KEY = "ws_staff_scope"


@dataclass(frozen=True)
class Scope:
    kid: Optional[str] = None
    campus: Optional[str] = None
    session_id: Optional[int] = None

    @property
    def campus_ref(self):
        """`<kid>/<campus>`, what the picker submits, or None for no campus."""
        return f"{self.kid}/{self.campus}" if self.campus is not None else None


WHOLE_INSTANCE = Scope()


def _rooms(kid, campus):
    return JumpSession.query.filter_by(jump_kid=kid, campus_key=campus)


def _checked(kid, campus, session_id):
    """The scope these values name, or the nearest one that exists."""
    if not kid or not campus or not _rooms(kid, campus).first():
        return WHOLE_INSTANCE
    if session_id is not None and not _rooms(kid, campus).filter_by(
            id=session_id).first():
        session_id = None
    return Scope(kid, campus, session_id)


def _parse_ref(ref):
    """`(kid, campus)` from `<kid>/<campus>`, or `(None, None)`."""
    kid, sep, campus = (ref or "").strip().partition("/")
    return (kid, campus) if sep and kid and campus else (None, None)


def current_scope():
    """The scope this request is about, recording a new choice if it makes one."""
    args = request.args
    if "campus" in args:
        scope = _checked(*_parse_ref(args.get("campus")),
                         args.get("session", type=int))
        session[SESSION_KEY] = [scope.kid, scope.campus, scope.session_id]
        return scope
    stored = session.get(SESSION_KEY) or [None, None, None]
    return _checked(*stored)


def participant_criteria(scope):
    """The `Users` filter every supervision page applies. See the docstring."""
    criteria = [Users.type != "admin", Users.hidden == False]  # noqa: E712
    if scope.campus is not None:
        rooms = db.session.query(JumpSession.id).filter(
            JumpSession.jump_kid == scope.kid,
            JumpSession.campus_key == scope.campus)
        if scope.session_id is not None:
            rooms = rooms.filter(JumpSession.id == scope.session_id)
        filed = db.session.query(JumpLink.user_id).filter(
            JumpLink.session_id.in_(rooms))
        criteria.append(Users.id.in_(filed))
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

    Campuses are listed by name and sessions newest first, which is the order
    a supervisor looks for today's room in. Nothing to draw (an instance no
    ticket has named a session on) is an empty list, and the template then
    draws nothing at all.

    A campus is labelled with its key id only on an instance that has heard
    from more than one Jump, where two campuses can share a name. One Jump is
    the normal case, and its picker reads as it always did.

    Only rooms somebody is filed in: a link follows the session its latest
    ticket names (`jump.file_session`), so the room of a content the instance
    has moved on from can empty out, and an empty room is noise in a picker.
    """
    scope = current_scope()
    filed = db.session.query(JumpLink.session_id).filter(
        JumpLink.session_id.isnot(None))
    rows = (JumpSession.query.filter(JumpSession.id.in_(filed))
            .order_by(JumpSession.created.desc()).all())
    several = len({row.jump_kid for row in rows}) > 1
    campuses = {}
    for row in rows:
        label = f"{row.campus_label} ({row.jump_kid})" if several else row.campus_label
        campuses.setdefault(f"{row.jump_kid}/{row.campus_key}", label)
    return {
        "scope": scope,
        "campuses": sorted(campuses.items(), key=lambda kv: kv[1].lower()),
        "sessions": [(row.id, row.label) for row in rows
                     if row.jump_kid == scope.kid and row.campus_key == scope.campus],
    }


def load_scope(app):
    app.jinja_env.globals["ws_scope_picker"] = picker
