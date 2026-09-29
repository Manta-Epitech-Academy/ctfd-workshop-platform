"""A second door, at an address nobody is told, with codes of its own.

`/supervisor/join` (staff.py) lets the people running the room make their own
accounts from a code. This is the same shape for the other case: participants
who do not come through Jump and must not wait for an admin to make accounts by
hand — a partner school, a visiting group, a room where the usual way in is not
available on the day.

Why not core's `/register`: that route is one switch for the whole instance
(`registration_visibility`), it knows nothing about codes that can be rotated or
revoked on their own, and turning it on opens the instance to anybody who finds
the URL. This door is independent of it in both directions.

**Several codes are live at once**, because several groups are. `RUN-EVENT-0929`
and `PAR-LYCEE-1234` can both admit people on the same afternoon, and each
account keeps the code it came in on — so a cohort can be counted, listed and
revoked without touching the other. The code is written to a CTFd **custom
field** rather than a table of this plugin's own: `/admin/users` already shows
and filters custom fields, the value travels with the account through an export,
and a plugin table would have meant a migration to store one string.

**The door is hidden, and that is a requirement rather than a side effect.**
Nothing participant-facing links here or mentions it: not the navbar, not the
login page, not an error anywhere else. The address is half the secret and the
code is the other half. Consequently:

  * switched off, or with no codes, the route is a **404** and not a closed
    page — an instance not using this must look like one that never heard of it;
  * too many wrong codes from one address is also a 404, not a 429, so
    exhausting the throttle tells a prober nothing that "no such route" does not;
  * the only place the route is named is `/admin/workshop/external`, which is
    `admins_only`.

The throttle is written out here rather than taken from `@ratelimit`. On this
fork that decorator is a no-op — `_ratelimit_check_and_increment` returns None
(commit 357509b2, "yolo: disable ratelimits") — so decorating this route would
have looked like a control and been nothing. A hidden door that answers
unlimited guesses is a door with a code and no lock.
"""
import hmac
import json

from flask import (Blueprint, abort, redirect, render_template, request,
                   url_for)
from flask_babel import lazy_gettext as _l
from sqlalchemy.exc import IntegrityError

from CTFd.cache import cache, clear_standings, clear_user_session
from CTFd.models import UserFieldEntries, UserFields, Users, db
from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only
from CTFd.utils.logging import log
from CTFd.utils.security.auth import login_user
from CTFd.utils.user import authed, get_ip

from .staff import WorkshopStaff, account_errors

CODES_KEY = "workshop_external_codes"
# Whether the door exists at all, as a decision of its own. Stored codes are not
# consent: an admin must be able to close this and reopen it later without
# throwing the codes away, and a code left over from a past event must not be
# what decides that the route answers today. Default off, so an instance that
# has never been told about this has no such route.
ENABLED_KEY = "workshop_external_enabled"
ROUTE = "/external/join"

# Wrong codes from one address before the door stops answering it, and for how
# long. Ten is enough for somebody reading a code off a slide and mistyping it,
# and far too few to search a code worth using. Fifteen minutes, because the
# person who genuinely mistyped is still in the room.
MAX_TRIES = 10
WINDOW = 900

# `public=False` so a code is never shown to other participants, `editable=False`
# so an account cannot rewrite its own provenance, `required=False` because every
# other way in leaves it empty and has to keep working.
FIELD_NAME = "External code"
FIELD_DESC = ("The registration code this account was created with, at "
              "/external/join. Set by the platform; not editable.")

workshop_external = Blueprint("workshop_external", __name__,
                              template_folder="templates")


# ---------------------------------------------------------------------------
# The codes, and whether the door is offered at all
# ---------------------------------------------------------------------------

def external_codes():
    """The live codes, in the order they were added. Never None."""
    raw = get_config(CODES_KEY)
    if not raw:
        return []
    try:
        codes = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [c for c in codes if isinstance(c, str) and c.strip()]


def set_external_codes(codes):
    set_config(CODES_KEY, json.dumps(list(codes)))


def add_code(code):
    """Add one. Returns the stored list. Case-insensitively unique: the codes
    are compared that way at the door, so two that differ only in case would be
    one code wearing two labels, and the cohorts would be indistinguishable."""
    code = (code or "").strip()
    if not code:
        return external_codes()
    codes = external_codes()
    if code.lower() not in [c.lower() for c in codes]:
        codes.append(code)
        set_external_codes(codes)
    return codes


def remove_code(code):
    """Stop a code admitting anybody new.

    The accounts it already admitted keep it: the field is their provenance,
    not their permission, and erasing it would lose the only record of which
    group they belong to.
    """
    codes = [c for c in external_codes() if c.lower() != (code or "").lower()]
    set_external_codes(codes)
    return codes


def match_code(given):
    """The stored code equal to `given`, or None.

    Compared in constant time and case-insensitively, like core's registration
    code and like the supervisor one: a code read out loud has no case. Every
    candidate is compared even after a hit, so the time taken does not say
    which code matched or how many exist.
    """
    given = (given or "").strip().lower()
    found = None
    for code in external_codes():
        if hmac.compare_digest(given, code.lower()):
            found = code
    return found


def external_enabled():
    """Has an admin turned this door on? Unset is off."""
    value = get_config(ENABLED_KEY)
    return False if value is None else bool(value)


def set_external_enabled(on):
    # "true"/"false", because `_get_config` turns exactly those two strings back
    # into booleans (CTFd/utils/__init__.py).
    set_config(ENABLED_KEY, "true" if on else "false")


def joining_open():
    """Whether the route answers at all.

    Both halves, and an admin owns both: the switch says whether this instance
    offers the door, the codes say who may walk through it.
    """
    return external_enabled() and bool(external_codes())


# ---------------------------------------------------------------------------
# The throttle
# ---------------------------------------------------------------------------

def _throttle_key():
    return f"ws_external_tries_{get_ip()}"


def _too_many_tries():
    return int(cache.get(_throttle_key()) or 0) >= MAX_TRIES


def _record_wrong_code():
    key = _throttle_key()
    cache.set(key, int(cache.get(key) or 0) + 1, timeout=WINDOW)


def _forget_wrong_codes():
    """A correct code clears the count.

    Somebody who mistypes twice and then gets it right is not a prober, and
    leaving them two tries from a fifteen-minute lockout would punish the next
    person on that address — a classroom is one address.
    """
    cache.delete(_throttle_key())


# ---------------------------------------------------------------------------
# Which code an account came in on
# ---------------------------------------------------------------------------

def code_field(create=False):
    """The custom field, made the first time somebody actually uses the door.

    Not created at plugin load: an instance that never opens this door should
    not grow a field in its user schema for a feature it does not use.
    """
    field = UserFields.query.filter_by(name=FIELD_NAME).first()
    if field is None and create:
        field = UserFields(name=FIELD_NAME, description=FIELD_DESC,
                           field_type="text", required=False, public=False,
                           editable=False)
        db.session.add(field)
        db.session.commit()
    return field


def accounts_by_code():
    """`{code: [Users]}`, busiest cohort first.

    Read from the field every time rather than kept in a counter: a counter
    drifts the first time somebody deletes an account from `/admin/users`, and
    this cannot. Codes that have been removed still appear while they have
    accounts, which is the point — a cohort does not stop existing because its
    code did.
    """
    field = code_field()
    if field is None:
        return {}
    rows = (db.session.query(UserFieldEntries, Users)
            .join(Users, Users.id == UserFieldEntries.user_id)
            .filter(UserFieldEntries.field_id == field.id).all())
    out = {}
    for entry, user in rows:
        out.setdefault(entry.value or "", []).append(user)
    for users in out.values():
        users.sort(key=lambda u: u.id)
    return dict(sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def create_participant(name, email_address, password, code):
    """An ordinary participant, carrying the code that admitted them.

    Not hidden, unlike a supervisor: this is somebody doing the workshop, and
    they belong on the scoreboard and in the counters with everybody else.
    `verified`, because these instances send no mail and an unverified account
    could never do anything. No role row anywhere — the account has exactly the
    permissions of somebody who registered normally, which is none.
    """
    user = Users(name=name, email=email_address, password=password,
                 hidden=False, verified=True)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None
    # After the commit because it needs the id, and in its own transaction so a
    # field failure cannot undo an account somebody has already been told exists.
    db.session.add(UserFieldEntries(field_id=code_field(create=True).id,
                                    user_id=user.id, value=code))
    db.session.commit()
    return user


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------

@workshop_external.route(ROUTE, methods=["GET", "POST"])
def join():
    if authed():
        return redirect(url_for("workshop_page.workshop"))
    # Both of these are 404 on purpose. See the module docstring: the route has
    # to look identical to a route that is not there.
    if not joining_open() or _too_many_tries():
        abort(404)

    errors = []
    name = request.form.get("name", "").strip()
    email_address = request.form.get("email", "").strip().lower()
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        code = match_code(request.form.get("code", ""))
        if code is None:
            _record_wrong_code()
            errors.append(_l("That code is not right"))
        errors += account_errors(name, email_address, password)

        user = None
        if not errors:
            _forget_wrong_codes()
            user = create_participant(name, email_address, password, code)
            if user is None:
                # Two people, one name, same second: the loser sees the message
                # the uniqueness check would have given them.
                errors.append(_l("That user name is already taken"))
        if user is not None:
            login_user(user)
            log("registrations",
                format="[{date}] {ip} - {name} joined through the external door "
                       "with {email} on code {code}",
                name=user.name, email=user.email, code=code)
            return redirect(url_for("workshop_page.workshop"))

    return render_template("workshop_external_join.html", errors=errors,
                           name=name, email=email_address)


# ---------------------------------------------------------------------------
# The admin page: the only place any of this is named
# ---------------------------------------------------------------------------

def _is_staff(user):
    return user.type == "admin" or db.session.query(
        WorkshopStaff.user_id).filter_by(user_id=user.id).first() is not None


@workshop_external.route("/admin/workshop/external", methods=["GET", "POST"])
@admins_only
def manage():
    """`admins_only`, not `staff_only`: a supervisor runs the room, and whether
    the instance offers a second way in is not theirs."""
    if request.method == "POST":
        action = request.form.get("action")
        if action == "switch":
            set_external_enabled(request.form.get("enabled") == "on")
            notice = ("open" if joining_open()
                      else "no-code" if external_enabled() else "closed")
        elif action == "add":
            before = len(external_codes())
            add_code(request.form.get("code", ""))
            notice = "added" if len(external_codes()) > before else "duplicate"
        elif action == "remove":
            remove_code(request.form.get("code", ""))
            notice = "removed"
        elif action in ("revoke", "restore"):
            notice = _set_banned(request.form.get("code", ""),
                                 action == "revoke")
        else:
            abort(400)
        return redirect(url_for("workshop_external.manage", notice=notice,
                                subject=request.form.get("code", "")))
    return render_template("workshop_external.html",
                           enabled=external_enabled(),
                           codes=external_codes(),
                           cohorts=accounts_by_code(),
                           notice=request.args.get("notice"),
                           subject=request.args.get("subject", ""))


def _set_banned(code, banned):
    """Ban, or un-ban, every account that came in on one code.

    **Ban and not delete.** Shutting a cohort out is a decision made in a hurry,
    and deleting takes their solves and their history with them, irreversibly.
    Banning stops them signing in and is undone from the same page. Deleting one
    account stays where it already is, under `/admin/users`, where the
    confirmation names the person.

    An account that has since become an admin or a supervisor is skipped: a bulk
    action aimed at participants must not be able to lock out the room's staff.
    """
    users = [u for u in accounts_by_code().get(code, []) if not _is_staff(u)]
    for user in users:
        user.banned = banned
    db.session.commit()
    for user in users:
        clear_user_session(user_id=user.id)
    clear_standings()
    return "revoked" if banned else "restored"


def load_external(app):
    app.register_blueprint(workshop_external)
