"""The one account an admin cannot delete or demote.

CTFd refuses to let an admin delete *themselves* (`api/v1/users.py`, "You
cannot delete yourself") and stops there. Two admins can therefore delete each
other, and a single misclick in the admin panel's user list — where deletion is
a checkbox and a button, with a confirmation that names a count rather than a
person — removes the account the instance was set up with. Nothing recreates
it: `/admin/reset` does, but only by wiping every account and sending the
instance back to the setup wizard.

So one account is pinned. Which one is **not** decided by `id`: an instance
restored from a CTFd backup keeps the account and loses the number, and `admin`
comes back as id 520 with somebody else at id 1. Two rules, in order:

  1. `workshop_protected_user`, when it holds the id of an existing admin. Set
     from `/admin/workshop/plugin`, and the answer for an instance whose first
     account was renamed, or one that would rather pin a different admin.
  2. otherwise the admin **named** `admin`, case-insensitively. That is CTFd's
     own setup default, it is what survives an import, and requiring `type` to
     be `admin` too keeps a participant who signed up as "Admin" from being
     mistaken for it.

Neither rule invents a protected account: if the name was changed and nothing
was pinned, this protects nothing and says so on the page, rather than quietly
guarding whichever row happens to sort first.

The refusals come in two groups. The first is on `DELETE`/`PATCH
/api/v1/users/<id>`, the single endpoint every path to a user row goes through
— the user's own page, the list's bulk delete and the list's bulk edit all loop
over it (themes/admin/assets/js/pages/*.js), as does anything driving the API
by hand. On the protected account:

  delete   refused. The account cannot be removed.
  demote   refused. Its `type` cannot become anything but `admin`, which is
           what would otherwise make the first refusal a formality: demote,
           then delete the ordinary user that is left.
  ban      refused. Not a demotion, but the same outcome from the same panel:
           a banned admin cannot sign in, and the instance is as locked as if
           the row were gone.
  rename   refused. Rule 2 below finds this account *by its name*, so a rename
           would silently drop the protection — the quietest bypass of all.
  take over  changing its password or its email is refused for everyone except
           that account itself. Otherwise any admin walks in through the front
           door and the four refusals above are decoration.

The second group is instance-wide, and applies while a protected account
exists: **only that account** may `POST /admin/reset` or `POST /admin/import`.
Both end with every account replaced or gone, so an admin who cannot delete the
protected account one row at a time must not be able to do it in one upload.
Exporting is untouched, and so is the CSV import, which adds rows rather than
replacing them.

Everything else stays editable by any admin, and the protected account can
still change its own name... no: its name is fixed while it is protected. Unpin
it at `/admin/workshop/plugin` first, rename it, then pin it back by id.

This is deliberately **not** subject to the workshop switch (toggle.py, §39).
An instance with the plugin turned off is still an instance whose admin account
should survive a misclick, so `toggle.ALWAYS_ON` carries this blueprint and the
guard hangs on the app rather than on a blueprint.
"""
from flask import (Blueprint, abort, g, jsonify, redirect, request,
                   session, url_for)
from sqlalchemy import func

from CTFd.models import Users
from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only

# The one CTFd endpoint that touches a single user row. Named rather than
# matched on the path so a deployment under APPLICATION_ROOT still lands, and
# so a CTFd upgrade that renames it fails loudly here instead of silently
# letting the account through.
USER_ENDPOINT = "api.users_user_public"

# The two operations that end with every account replaced or gone. Named rather
# than matched on the path, so a deployment under APPLICATION_ROOT still lands
# and a CTFd rename surfaces here instead of quietly opening the door.
# `admin.export_ctf` is not among them: reading the instance out is not losing
# it. Neither is `admin.import_csv`, which adds rows rather than replacing them.
INSTANCE_ENDPOINTS = ("admin.reset", "admin.import_ctf")

CONFIG_KEY = "workshop_protected_user"
DEFAULT_NAME = "admin"

workshop_protect = Blueprint("workshop_protect", __name__)


def _admin(user_id):
    """That account, if it exists and is an admin. Nothing otherwise: a pinned
    id that has since been demoted stops being the protected account, which is
    the deliberate way to release the pin."""
    if not user_id:
        return None
    return Users.query.filter_by(id=user_id, type="admin").first()


def protected_user():
    """The account nothing may delete, demote or ban. None if there is none."""
    if "ws_protected" not in g:
        pinned = get_config(CONFIG_KEY)
        try:
            pinned = int(pinned) if pinned not in (None, "") else None
        except (TypeError, ValueError):
            pinned = None
        g.ws_protected = _admin(pinned) or (
            Users.query
            .filter(func.lower(Users.name) == DEFAULT_NAME, Users.type == "admin")
            .first())
    return g.ws_protected


def admins():
    """Every admin account, for the picker on the settings page."""
    return Users.query.filter_by(type="admin").order_by(Users.name).all()


def set_protected_user(user_id):
    """Pin an admin account, or clear the pin with a falsy id. Returns what is
    protected afterwards, so the caller can report it rather than guess."""
    set_config(CONFIG_KEY, str(user_id) if _admin(user_id) else "")
    g.pop("ws_protected", None)
    return protected_user()


def _refusal(message):
    # CTFd's own shape for this exact case ("You cannot delete yourself"), so
    # the admin theme and any API client read it the way they already do.
    response = jsonify({"success": False, "errors": {"id": message}})
    response.status_code = 403
    return response


# Anything the admin form may send for "banned" that means yes. The form
# serializes a checkbox, the API takes a JSON bool, and a hand-written call
# takes whatever its author typed.
TRUTHY = (True, "true", "True", 1, "1", "on")

TAIL = "Change which account is protected at /admin/workshop/plugin first."


def _patch_refusal(payload, keeper, actor_id):
    """Why this PATCH is refused, or None if it may go through.

    Every test compares against the value already stored, because the admin
    panel's edit form serializes the **whole** form: `name`, `email`, `type`
    and `banned` arrive on every save, unchanged, and refusing their mere
    presence would make the account uneditable rather than protected.
    """
    if not isinstance(payload, dict):
        return None
    name = keeper.name
    if "type" in payload and payload["type"] != "admin":
        return (f"{name} is this instance's protected administrator and cannot "
                f"be demoted. {TAIL}")
    if payload.get("banned") in TRUTHY and not keeper.banned:
        return (f"{name} is this instance's protected administrator and cannot "
                f"be banned: a banned admin cannot sign in to undo it. {TAIL}")
    if "name" in payload and payload["name"] != name:
        return (f"{name} is this instance's protected administrator and cannot "
                "be renamed: the protection finds it by name, so a rename would "
                f"quietly remove it. {TAIL}")
    if actor_id == keeper.id:
        # The account itself may still rotate its own credentials, which is the
        # one case where changing the protected admin's password is not a
        # takeover.
        return None
    if payload.get("password"):
        return (f"Only {name} can change its own password. Another admin doing "
                "it would be taking the account over, which the refusals above "
                f"exist to prevent. {TAIL}")
    if "email" in payload and payload["email"] != keeper.email:
        return f"Only {name} can change its own email address. {TAIL}"
    return None


def load_protect(app):
    @app.before_request
    def _guard_protected_admin():
        # Endpoint first: this runs on every request in the instance and must
        # not cost a query to learn it is not about a user row or a wipe.
        endpoint = request.endpoint
        if endpoint not in (USER_ENDPOINT, *INSTANCE_ENDPOINTS):
            return None
        keeper = protected_user()
        if keeper is None:
            # Nothing pinned and no admin named `admin`. The page says so; this
            # is not the place to invent one.
            return None

        if endpoint in INSTANCE_ENDPOINTS:
            if request.method != "POST" or session.get("id") == keeper.id:
                return None
            # An HTML page, not the API: CTFd renders errors/403.html with the
            # description, so the reason reaches the person who pressed it.
            abort(403, f"Only {keeper.name}, this instance's protected "
                       "administrator, can reset this instance or restore it "
                       "from a backup. Both replace every account, including "
                       f"{keeper.name}'s. {TAIL}")

        if request.method not in ("DELETE", "PATCH"):
            return None
        if request.view_args.get("user_id") != keeper.id:
            return None
        if request.method == "DELETE":
            return _refusal(
                f"{keeper.name} is this instance's protected administrator and "
                f"cannot be deleted. {TAIL}")
        # `silent=True`: a malformed body is CTFd's to reject, not ours, and a
        # PATCH we cannot read is a PATCH we cannot prove is harmful.
        refusal = _patch_refusal(request.get_json(silent=True), keeper,
                                 session.get("id"))
        return _refusal(refusal) if refusal else None

    app.register_blueprint(workshop_protect)


@workshop_protect.route("/admin/workshop/protected", methods=["POST"])
@admins_only
def choose():
    """Pin the protected admin, from the picker on /admin/workshop/plugin.

    Its own route rather than a second action on that page's view: the switch
    and this guard are independent — one is about whether the workshop runs,
    the other about whether the instance keeps its administrator — and the
    switch's view has no business knowing about accounts. Same shape as
    staff.py, whose supervisor forms also post beside the page that shows them.
    """
    raw = request.form.get("user_id", "").strip()
    keeper = set_protected_user(int(raw) if raw.isdigit() else None)
    return redirect(url_for("workshop_toggle.plugin",
                            notice="pinned" if keeper else "unpinned"))


@workshop_protect.route("/api/v1/workshop/protected-user")
@admins_only
def protected():
    """Which account the admin panel should not offer to delete.

    Read by assets/protect-admin.js, which takes the control away before it is
    clicked: CTFd's own delete handlers check `response.success` and do nothing
    at all when it is false, so a refusal with no UI reads as a broken button.
    """
    keeper = protected_user()
    return jsonify({"success": True,
                    "data": {"id": keeper.id, "name": keeper.name}
                    if keeper else None})
