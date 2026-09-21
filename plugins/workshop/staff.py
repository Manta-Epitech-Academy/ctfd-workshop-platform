"""The supervisor tier (PLAN.md §32) — the role CTFd does not have.

CTFd knows two kinds of account, `user` and `admin`, and `is_admin()` is the
literal `type == "admin"` (utils/user/__init__.py:203). A third `Users.type`
is not an option: the admin user form hardcodes the two choices, and every
schema's `views[view]` lookup (schemas/users.py, teams.py, tokens.py) raises
`KeyError` on a value it has never heard of — a supervisor typed that way
would 500 the user API. PLAN.md §23.2 settled this before a line was written.

So the tier lives here, entirely. A supervisor is a normal `user` row that
`workshop_staff` names, and the only thing that changes about what they can
reach is what `staff_only` guards: this plugin's own read-only pages, and
nothing under /admin. `is_admin()` stays false for them, which is the safety
property the whole design rests on — a route nobody thought about is closed,
never open, and there is no write route behind `staff_only` at all.

Two properties of the account are load-bearing:

  hidden=True    keeps a supervisor off the scoreboard, out of the answer
                 sheet's "who is where" (answers.py filters on it), out of
                 every report here, and out of CTFd's `num_users` cap. Their
                 own solves, if they walk the subject, count for nothing.
  verified=True  email verification is for participants; the code is what
                 proved who a supervisor is.

The way in is a code, not the instance's registration. `/supervisor/join`
deliberately ignores `registration_visibility`, the same call jump.py makes
for the same reason: a production instance keeps registration closed
(`/register` is a 404 there), and the people running the room still have to
get in. An empty code closes the door, and empty is the default, so an
instance changes in no way until an admin fills it in on
/admin/workshop/settings. That page is also where the accounts are managed:
created outright without a code, promoted from an existing account, revoked
(the role goes, the account stays) or deleted (the account goes, the way
core's own user API deletes one). Nothing here needs /admin/users, though a
supervisor is listed there too, as an ordinary hidden user.
"""
import functools
import hmac
from datetime import datetime

from flask import (Blueprint, abort, g, redirect, render_template, request,
                   session, url_for)
from flask_babel import lazy_gettext as _l
from sqlalchemy.exc import IntegrityError

from CTFd.cache import clear_challenges, clear_standings, clear_user_session
from CTFd.models import (Awards, Notifications, Solves, Submissions, Tracking,
                         Unlocks, Users, db)
from CTFd.utils import email as email_util
from CTFd.utils import get_config, set_config, validators
from CTFd.utils.decorators import admins_only, ratelimit
from CTFd.utils.logging import log
from CTFd.utils.security.auth import login_user
from CTFd.utils.user import authed, is_admin

CODE_KEY = "workshop_supervisor_code"
SUPERVISOR = "supervisor"

workshop_staff = Blueprint("workshop_staff", __name__, template_folder="templates")


class WorkshopStaff(db.Model):
    """One row per account that holds a role. Today the only role is
    `supervisor`; the column is there so a second tier is a value, not a
    table."""
    __tablename__ = "workshop_staff"

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = db.Column(db.String(16), nullable=False, default=SUPERVISOR)
    created = db.Column(db.DateTime, default=datetime.utcnow)
    # `code` for a self-registration, `admin:<id>` for a grant on the settings
    # page: who let this account in, kept on the row because the code can be
    # rotated and the config is not a history.
    granted_by = db.Column(db.String(32), nullable=True)


# ---------------------------------------------------------------------------
# The predicate, and the guard
# ---------------------------------------------------------------------------

def supervisor_code():
    return (get_config(CODE_KEY) or "").strip()


def joining_open():
    """Whether /supervisor/join answers at all. Empty code, closed door."""
    return bool(supervisor_code())


def is_supervisor():
    """Does the signed-in account hold the role? One query per request: the
    navbar, the guard and a page's own links all ask, and `g` is what makes
    them one lookup rather than three."""
    if not authed():
        return False
    if "ws_supervisor" not in g:
        g.ws_supervisor = db.session.query(WorkshopStaff.user_id).filter_by(
            user_id=session["id"]).first() is not None
    return g.ws_supervisor


def is_staff():
    return is_admin() or is_supervisor()


def staff_only(f):
    """`admins_only`, widened to supervisors. Same two refusals as core's —
    403 on a JSON request, the login page otherwise — so a page that swaps
    one decorator for the other changes nothing else about how it fails."""
    @functools.wraps(f)
    def staff_only_wrapper(*args, **kwargs):
        if is_staff():
            return f(*args, **kwargs)
        if request.is_json:
            abort(403)
        return redirect(url_for("auth.login", next=request.full_path))
    return staff_only_wrapper


def staff_base():
    """Which shell a staff page extends.

    An admin gets the admin theme's own `admin/base.html`, with its full nav.
    A supervisor gets this plugin's `workshop_staff_base.html`: the same head,
    styles and scripts, with the nav reduced to the pages `staff_only` opens.
    Core's admin nav is inline in `base.html` rather than in a Jinja block, so
    passing the base in from the view is the only way to keep Config, Users
    and Challenges out of a supervisor's sight without overriding a core
    template for everybody.
    """
    return "admin/base.html" if is_admin() else "workshop_staff_base.html"


# ---------------------------------------------------------------------------
# Granting and revoking
# ---------------------------------------------------------------------------

def supervisors():
    rows = (db.session.query(WorkshopStaff, Users)
            .join(Users, Users.id == WorkshopStaff.user_id)
            .order_by(Users.name).all())
    return [{"id": u.id, "name": u.name, "email": u.email,
             "created": s.created, "granted_by": s.granted_by}
            for s, u in rows]


def grant(user, granted_by):
    """Make `user` a supervisor. False if they already are one."""
    if WorkshopStaff.query.filter_by(user_id=user.id).first() is not None:
        return False
    # See the module docstring: hidden is what keeps them out of every count.
    user.hidden = True
    db.session.add(WorkshopStaff(user_id=user.id, granted_by=granted_by))
    db.session.commit()
    # `hidden` is among the attributes CTFd caches per session.
    clear_user_session(user_id=user.id)
    return True


def revoke(user_id):
    """Take the role away. The account stays, hidden, for /admin/users to
    delete or unhide: removing an account is a bigger decision than
    removing a role, and this page only makes the small one."""
    row = WorkshopStaff.query.filter_by(user_id=user_id).first()
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# Making the account: one path for the form and for the admin page
# ---------------------------------------------------------------------------

def account_errors(name, email_address, password):
    """The checks core's register runs, minus the fields neither form asks
    for (website, country, brackets, custom fields). A list of messages,
    empty when the three values would make a valid account."""
    errors = []
    if not name:
        errors.append(_l("Pick a longer user name"))
    if validators.validate_email(name):
        errors.append(_l("Your user name cannot be an email address"))
    if not validators.validate_email(email_address):
        errors.append(_l("Please enter a valid email address"))
    if (email_util.check_email_is_whitelisted(email_address) is False
            or email_util.check_email_is_blacklisted(email_address) is True):
        errors.append(_l("Your email address is not from an allowed domain"))
    if Users.query.filter_by(name=name).first():
        errors.append(_l("That user name is already taken"))
    if Users.query.filter_by(email=email_address).first():
        errors.append(_l("That email has already been used"))
    minimum = int(get_config("password_min_length", default=0))
    if not password:
        errors.append(_l("Pick a longer password"))
    elif minimum and len(password) < minimum:
        errors.append(_l("Password must be at least %(n)s characters", n=minimum))
    if len(password) > 128:
        errors.append(_l("Pick a shorter password"))
    return errors


def create_supervisor(name, email_address, password, granted_by):
    """A new, hidden, verified account holding the role. Returns the user,
    or None if two requests raced for the same name — the caller reports it
    the way the uniqueness check would have."""
    user = Users(name=name, email=email_address, password=password,
                 hidden=True, verified=True)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None
    grant(user, granted_by)
    return user


def delete_supervisor(user_id):
    """Remove the account outright, the way core's DELETE /api/v1/users does
    (api/v1/users.py `UserPublic.delete`): every table that names the user,
    then the user. The role row cascades. False if the id holds no role, so
    this page can only ever delete a supervisor, never a participant."""
    if WorkshopStaff.query.filter_by(user_id=user_id).first() is None:
        return False
    for model in (Notifications, Awards, Unlocks, Submissions, Solves, Tracking):
        model.query.filter_by(user_id=user_id).delete()
    Users.query.filter_by(id=user_id).delete()
    db.session.commit()
    clear_user_session(user_id=user_id)
    clear_standings()
    clear_challenges()
    return True


# ---------------------------------------------------------------------------
# The way in
# ---------------------------------------------------------------------------

@workshop_staff.route("/supervisor/join", methods=["GET", "POST"])
# Per subject (IP, in practice): the code is the secret and this is the only
# place it can be tried. Twenty, not core's fifty, and not fewer: a staff room
# behind one NAT signing up together must not lock itself out.
@ratelimit(method="POST", limit=20, interval=300)
def join():
    if authed():
        return redirect(url_for("workshop_page.workshop"))
    if not joining_open():
        abort(404)

    errors = []
    name = request.form.get("name", "").strip()
    email_address = request.form.get("email", "").strip().lower()
    if request.method == "POST":
        code = str(request.form.get("code", "")).strip()
        password = request.form.get("password", "").strip()

        # Constant-time, case-insensitive: core compares registration codes
        # case-insensitively too, and a code read out loud has no case.
        if not hmac.compare_digest(code.lower(), supervisor_code().lower()):
            errors.append(_l("That supervisor code is not right"))
        errors += account_errors(name, email_address, password)

        user = None
        if not errors:
            user = create_supervisor(name, email_address, password, "code")
            if user is None:
                # Two people, one name, same second: the loser sees the same
                # message the uniqueness check would have given them.
                errors.append(_l("That user name is already taken"))
        if user is not None:
            login_user(user)
            log("registrations",
                format="[{date}] {ip} - {name} joined as a supervisor with {email}",
                name=user.name, email=user.email)
            return redirect(url_for("workshop_stats.stats"))

    return render_template("workshop_supervisor_join.html", errors=errors,
                           name=name, email=email_address)


# ---------------------------------------------------------------------------
# Management, on the settings page (mode.py renders it; this handles the POST)
# ---------------------------------------------------------------------------

@workshop_staff.route("/admin/workshop/supervisors", methods=["POST"])
@admins_only
def manage():
    action = request.form.get("action")
    if action == "code":
        set_config(CODE_KEY, request.form.get("code", "").strip())
        notice = "code-open" if joining_open() else "code-closed"
    elif action == "grant":
        who = request.form.get("who", "").strip()
        user = Users.query.filter(
            (Users.name == who) | (Users.email == who.lower())).first() if who else None
        if user is None:
            notice = "nobody"
        elif user.type == "admin":
            # Already sees everything; a row here would only hide the account.
            notice = "is-admin"
        else:
            notice = "granted" if grant(user, f"admin:{session['id']}") else "already"
    elif action == "revoke":
        notice = "revoked" if revoke(request.form.get("user_id", type=int)) else "nobody"
    elif action == "create":
        name = request.form.get("name", "").strip()
        email_address = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        errors = account_errors(name, email_address, password)
        if not errors and create_supervisor(
                name, email_address, password, f"admin:{session['id']}") is None:
            errors.append(_l("That user name is already taken"))
        if errors:
            return redirect(url_for("workshop_mode.settings", notice="invalid",
                                    why=str(errors[0])))
        notice = "created"
    elif action == "delete":
        user_id = request.form.get("user_id", type=int)
        if user_id == session["id"]:
            notice = "nobody"
        else:
            notice = "deleted" if delete_supervisor(user_id) else "nobody"
    else:
        abort(400)
    return redirect(url_for("workshop_mode.settings", notice=notice))


def load_staff(app):
    app.register_blueprint(workshop_staff)
    # For the shell's navbar and the login page, which are templates of ours
    # and cannot import anything.
    app.jinja_env.globals["is_staff"] = is_staff
    app.jinja_env.globals["is_supervisor"] = is_supervisor
    app.jinja_env.globals["supervisor_joining_open"] = joining_open
