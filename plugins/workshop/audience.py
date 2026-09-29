"""Two populations on one instance, each seeing only its own.

A participant who came through Jump and a participant who came through
`/external/join` (external.py, §42) are in the same room and on the same
instance, but they are not in the same cohort: a visiting group's names and
scores have no business on the scoreboard the Jump talents read, and the
reverse is just as true.

**The audience of an account is the external code it carries**, or `""` for
everybody else — Jump, an admin creating an account by hand, a supervisor who
joined with the staff code. `RUN-EVENT-0929` sees `RUN-EVENT-0929`; `""` sees
`""`. Two external cohorts do not see each other either, which is the same rule
rather than a second one.

Three audiences that are not a code:

  * **staff** — an admin or a supervisor sees everything. They are the people
    who have to answer "where is everyone", and `/admin` and the supervision
    pages are unfiltered by design (§32).
  * **anonymous** — a visitor on a public scoreboard is given `""`. An external
    cohort is the group we were asked to keep out of sight; putting it in front
    of someone who has not even signed in would be the loudest way to fail that.
  * **teams mode** — nothing is filtered, and that is deliberate rather than an
    oversight: a scoreboard row is then a *team*, an audience is a property of a
    *user*, and a team with members from both cohorts has no correct answer.
    These instances run in users mode; a teams instance gets the behaviour it
    had before this existed.

Why interception and not a query: CTFd computes standings in `get_standings()`
with no hook, serves them from endpoints this plugin does not own, and its only
notion of a divided scoreboard is *brackets* — which annotate every row and let
the **client** filter, so the rows are all still in the response. That is a tab,
not a wall. `CTFd/` stays pristine (CLAUDE.md), so the wall is built here: 404
on another cohort's detail page, and the list responses filtered on the way out.
"""
import json

from flask import g, request, session

from CTFd.models import UserFieldEntries, UserFields, Users, db
from CTFd.utils import get_config
from CTFd.utils.user import authed, is_admin

from .external import FIELD_NAME
from .staff import is_staff

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

# Lists, filtered on the way out. `/scoreboard` and the score graph are Alpine
# components that read the first two (themes/core/templates/scoreboard.html), so
# filtering the API filters the page and no markup is touched.
LIST_ENDPOINTS = (
    "api.scoreboard_scoreboard_list",
    "api.scoreboard_scoreboard_detail",
    "api.users_user_list",
)


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


def audience_of(user_id):
    """The cohort an account belongs to. `""` is the default population."""
    return _coded().get(user_id, "")


def split_applies():
    """Is there anything to separate at all?

    No external accounts means one population, and then every hook below is a
    dictionary lookup that always agrees — cheaper to answer once here.
    """
    return get_config("user_mode") != "teams" and bool(_coded())


def viewer_audience():
    """The cohort of whoever is asking, or None for somebody who sees all."""
    if not split_applies():
        return None
    if authed() and (is_admin() or is_staff()):
        return None
    if not authed():
        return ""
    return audience_of(session.get("id"))


def visible(user_id, viewer=None):
    viewer = viewer if viewer is not None else viewer_audience()
    return viewer is None or audience_of(user_id) == viewer


# ---------------------------------------------------------------------------
# The two hooks
# ---------------------------------------------------------------------------

def _target_id():
    """The account a detail request is about, as an int, or None."""
    raw = (request.view_args or {}).get("user_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
        meta = (payload.get("meta") or {}).get("pagination")
        if meta:
            # The page is what it is; only the totals would otherwise count
            # accounts the reader is not being shown.
            meta["total"] = len(kept)
    elif isinstance(data, dict):
        # `/scoreboard/top/<n>`: keyed by place, so the keys are the ranking.
        kept = [row for _place, row in sorted(data.items(), key=lambda kv: int(kv[0]))
                if visible(row.get("id"), viewer)]
        payload["data"] = {str(i + 1): row for i, row in enumerate(kept)}
    return payload


def load_audience(app):
    # The `/users` listing is rendered by CTFd's own view, which queries before
    # the template runs; the template override (shell.py) drops the rows there
    # and asks this.
    app.jinja_env.globals["ws_visible"] = visible

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

    @app.after_request
    def _filter_lists(response):
        if request.endpoint not in LIST_ENDPOINTS:
            return response
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
        response.set_data(json.dumps(_filter_list(payload, viewer)))
        return response
