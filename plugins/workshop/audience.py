"""Several populations on one instance, each seeing only its own.

A participant who came through Jump and a participant who came through
`/external/join` (external.py, §42) are in the same room and on the same
instance, but they are not in the same cohort: a visiting group's names and
scores have no business on the scoreboard the Jump talents read, and the
reverse is just as true. Nor are two Jump sessions the same cohort (§50): an
instance serves several events over its life, and the Coding Club that ran it
last spring is not the room a talent is sitting in today.

**The audience of an account is, in this order:**

  * the external code it carries, `("code", "RUN-EVENT-0929")`;
  * else the Jump session its link was filed under, `("session", 12)`
    (jump.py `file_session`);
  * else `DEFAULT`, for everybody else: an admin creating an account by hand,
    a supervisor who joined with the staff code, and a Jump account created
    before tickets named a session, until its next entry files it.

An account sees its own audience and nothing else. Two external cohorts do not
see each other, two sessions do not see each other, and neither sees the
other door's people: one rule, applied to three kinds of key. The keys are
tagged tuples rather than bare strings because a code is any text an admin
typed, so nothing could guarantee a code and a session id never spell the
same value.

Three audiences that are not a key:

  * **staff** — an admin or a supervisor sees everything. They are the people
    who have to answer "where is everyone", and `/admin` and the supervision
    pages are unfiltered by design (§32).
  * **anonymous** — a visitor on a public scoreboard is given `DEFAULT`. An
    external cohort is the group we were asked to keep out of sight, and a
    session is a room of minors; putting either in front of someone who has not
    even signed in would be the loudest way to fail that.
  * **teams mode** — nothing is filtered, and that is deliberate rather than an
    oversight: a scoreboard row is then a *team*, an audience is a property of a
    *user*, and a team with members from both cohorts has no correct answer.
    These instances run in users mode; a teams instance gets the behaviour it
    had before this existed.

Why not brackets: CTFd's only notion of a divided scoreboard annotates every
row and lets the **client** filter, so the rows are all still in the response.
That is a tab, not a wall. `CTFd/` stays pristine (CLAUDE.md), so the wall is
built here, in three ways, each chosen by where the list is cut into a page:

  * **a detail request** for another cohort's account answers 404;
  * **a list CTFd pages itself** (`/users`, `/api/v1/users`) has its own query
    narrowed to the reader's audience before it runs, with SQLAlchemy's
    `with_loader_criteria`, so CTFd's pagination counts only what the reader
    may see. Filtering the fifty rows it had already picked would leave a
    talent paging through screens of other rooms' blanks;
  * **a list CTFd caches** cannot be narrowed that way: `get_standings()` is
    memoized on its arguments, so a narrowed query would be stored under the
    instance's key and served to everybody. The full list is filtered on the
    way out, and the top-N graph is rebuilt from the full standings, because
    the instance's top ten is not a room's top ten.
"""
import json
from collections import defaultdict

from flask import g, has_request_context, request, session
from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from CTFd.models import Awards, Solves, UserFieldEntries, UserFields, Users, db
from CTFd.utils import get_config
from CTFd.utils.dates import isoformat, unix_time_to_utc
from CTFd.utils.modes import generate_account_url
from CTFd.utils.scores import get_standings
from CTFd.utils.user import authed, is_admin

from .external import FIELD_NAME
from .jump import JumpLink
from .staff import is_staff

# Whoever carries no code and no session.
DEFAULT = ("default",)

# One account: opening it, or anything hanging off it. Answered with 404 rather
# than 403 for a foreign cohort, so the reply is the same one a deleted account
# gives and says nothing about who else is on the instance.
DETAIL_ENDPOINTS = (
    "users.public",
    "api.users_user_public",
    "api.users_user_public_solves",
    "api.users_user_public_fails",
    "api.users_user_public_awards",
)

# Lists CTFd pages itself, so their query is narrowed before it runs and the
# page counts only the reader's own audience. `/users` is rendered by CTFd's
# view and `/api/v1/users` by its API; neither template nor response is touched.
QUERY_ENDPOINTS = (
    "users.listing",
    "api.users_user_list",
)

# Lists served whole from a cache, filtered on the way out. `/scoreboard` and
# the score graph are Alpine components that read the first two
# (themes/core/templates/scoreboard.html), so filtering the API filters the page
# and no markup is touched.
#
# A step's solver list names every account that solved it, so it is a list of
# people like the others. The solve COUNT on the step stays the whole
# instance's: "47 people have already done this one" is encouragement and
# names nobody (§50).
LIST_ENDPOINTS = (
    "api.scoreboard_scoreboard_list",
    "api.scoreboard_scoreboard_detail",
    "api.challenges_challenge_solves",
)

# What core's `ScoreboardDetail.get` clamps `<count>` to
# (CTFd/api/v1/scoreboard.py), kept so a rebuilt graph is never longer.
TOP_MAX = 50


def _coded():
    """`{user_id: code}` for every account that came through the other door.

    One query per request, not one per row: the scoreboard is a list and a
    lookup per entry would turn a page into a hundred round trips.
    """
    if "ws_coded" not in g:
        field = UserFields.query.filter_by(name=FIELD_NAME).first()
        if field is None:
            g.ws_coded = {}
        else:
            g.ws_coded = {
                e.user_id: (e.value or "")
                for e in UserFieldEntries.query.filter_by(field_id=field.id).all()
            }
    return g.ws_coded


def _sessions():
    """`{user_id: session_id}` for every Jump account filed under a session.

    One query per request, like `_coded`.
    """
    if "ws_sessions" not in g:
        g.ws_sessions = dict(
            db.session.query(JumpLink.user_id, JumpLink.session_id)
            .filter(JumpLink.session_id.isnot(None)).all())
    return g.ws_sessions


def audience_of(user_id):
    """The cohort an account belongs to, as a tagged key (see the docstring)."""
    code = _coded().get(user_id)
    if code:
        return ("code", code)
    session_id = _sessions().get(user_id)
    if session_id is not None:
        return ("session", session_id)
    return DEFAULT


def split_applies():
    """Is there anything to separate at all?

    No external account and no filed session means one population, and then
    every hook below is a dictionary lookup that always agrees — cheaper to
    answer once here.
    """
    return get_config("user_mode") != "teams" and bool(_coded() or _sessions())


def viewer_audience():
    """The cohort of whoever is asking, or None for somebody who sees all."""
    if not split_applies():
        return None
    if authed() and (is_admin() or is_staff()):
        return None
    if not authed():
        return DEFAULT
    return audience_of(session.get("id"))


def visible(user_id, viewer=None):
    viewer = viewer if viewer is not None else viewer_audience()
    return viewer is None or audience_of(user_id) == viewer


def _members(key):
    """`audience_of(Users.id) == key`, as a criterion on `Users.id`.

    Built from the same two maps `audience_of` reads, so a narrowed query and a
    filtered list cannot disagree about who is in a room. Not a subquery: the
    code is CTFd's `FieldEntries.value`, a JSON column, which SQL would compare
    as JSON text rather than as the string `audience_of` sees.
    """
    keyed = {u for u in set(_coded()) | set(_sessions())
             if u is not None and audience_of(u) != DEFAULT}
    if key == DEFAULT:
        return Users.id.notin_(sorted(keyed))
    return Users.id.in_(sorted(u for u in keyed if audience_of(u) == key))


# ---------------------------------------------------------------------------
# The hooks
# ---------------------------------------------------------------------------

def _target_id():
    """The account a detail request is about, as an int, or None."""
    raw = (request.view_args or {}).get("user_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _narrow_query(state):
    """`do_orm_execute`: narrow every `Users` read to the reader's audience.

    Active only while `g.ws_narrow` holds a criterion, which `_narrow_lists`
    sets on a `QUERY_ENDPOINTS` request and nowhere else, so no other query in
    the instance, and none in the drainer thread, ever meets it. Within such a
    request every `Users` read is narrowed, the reader's own account included;
    it is in the reader's audience by definition, so it is still found.

    The criterion is built before the view runs, never here: building it reads
    the database, and a read from inside this listener would come back through
    it.
    """
    if not has_request_context() or not state.is_select:
        return
    criterion = g.get("ws_narrow")
    if criterion is None:
        return
    state.statement = state.statement.options(
        with_loader_criteria(Users, criterion))


def _filter_list(payload, viewer):
    """Filter a list response in place, and renumber what is left.

    Places are recomputed rather than kept: a cohort that reads 4th, 7th, 9th
    is a cohort being told how many people it cannot see.
    """
    data = payload.get("data")
    if isinstance(data, list):
        kept = [row for row in data
                if visible(row.get("account_id", row.get("id")), viewer)]
        for i, row in enumerate(kept):
            if "pos" in row:
                row["pos"] = i + 1
        payload["data"] = kept
    return payload


def _top(count, viewer):
    """`/scoreboard/top/<count>` for a reader who sees one audience.

    Core cuts the instance's top `count` first (`get_scoreboard_detail`), so
    filtering its answer leaves a room with whichever of its own made the
    instance's top ten: on an instance that has served a season of rooms, an
    empty graph. This takes the full standings instead, which core memoizes
    already, keeps the reader's audience and cuts afterwards.

    The rows are core's, field for field: a copy of the body of
    `get_scoreboard_detail` (CTFd/utils/scoreboard/__init__.py), fed a list
    of accounts rather than a count, since core offers no way to pass one.
    **Re-read that function on a CTFd upgrade** (README, "Portability notes").
    """
    count = max(1, min(count, TOP_MAX))
    standings = [s for s in get_standings(bracket_id=request.args.get("bracket_id"))
                 if visible(s.account_id, viewer)][:count]
    ids = [s.account_id for s in standings]

    solves = Solves.query.filter(Solves.account_id.in_(ids))
    awards = Awards.query.filter(Awards.account_id.in_(ids))
    freeze = get_config("freeze")
    if freeze:
        solves = solves.filter(Solves.date < unix_time_to_utc(freeze))
        awards = awards.filter(Awards.date < unix_time_to_utc(freeze))

    events = defaultdict(list)
    for solve in solves.all():
        events[solve.account_id].append({
            "challenge_id": solve.challenge_id,
            "account_id": solve.account_id,
            "team_id": solve.team_id,
            "user_id": solve.user_id,
            "value": solve.challenge.value,
            "date": isoformat(solve.date),
        })
    for award in awards.all():
        events[award.account_id].append({
            "challenge_id": None,
            "account_id": award.account_id,
            "team_id": award.team_id,
            "user_id": award.user_id,
            "value": award.value,
            "date": isoformat(award.date),
        })

    return {
        str(place): {
            "id": s.account_id,
            "account_url": generate_account_url(account_id=s.account_id),
            "name": s.name,
            "score": int(s.score),
            "bracket_id": s.bracket_id,
            "bracket_name": s.bracket_name,
            "solves": sorted(events.get(s.account_id, []), key=lambda e: e["date"]),
        }
        for place, s in enumerate(standings, start=1)
    }


def load_audience(app):
    if not event.contains(Session, "do_orm_execute", _narrow_query):
        event.listen(Session, "do_orm_execute", _narrow_query)

    @app.before_request
    def _hide_other_cohorts():
        # Endpoint first: this runs on every request in the instance and must
        # not cost a query to learn it is not about one account.
        if request.endpoint not in DETAIL_ENDPOINTS:
            return None
        viewer = viewer_audience()
        if viewer is None:
            return None
        target = _target_id()
        if target is not None and not visible(target, viewer):
            from flask import abort
            abort(404)
        return None

    @app.before_request
    def _narrow_lists():
        if request.endpoint not in QUERY_ENDPOINTS:
            return None
        viewer = viewer_audience()
        if viewer is not None:
            g.ws_narrow = _members(viewer)
        return None

    @app.after_request
    def _filter_lists(response):
        if request.endpoint not in LIST_ENDPOINTS:
            return response
        # After core's view and its visibility decorators, never instead of
        # them: a hidden scoreboard answers 403 here and is left alone.
        if response.status_code != 200 or not response.is_json:
            return response
        viewer = viewer_audience()
        if viewer is None:
            return response
        try:
            payload = response.get_json()
        except Exception:                      # noqa: BLE001
            return response
        if not isinstance(payload, dict):
            return response
        if request.endpoint == "api.scoreboard_scoreboard_detail":
            payload["data"] = _top(request.view_args["count"], viewer)
        else:
            _filter_list(payload, viewer)
        response.set_data(json.dumps(payload))
        return response
