"""The switch that turns this plugin off, and the one page that turns it back on.

An instance is not always running a workshop. Between two sessions, or when the
box is reused for an ordinary CTF, everything this plugin adds is in the way:
`/` lands on the workshop instead of the board, the navbar leads to a subject
that is not there, and the admin bar carries eight entries about a room nobody
is in.

So: one config key, `workshop_enabled`, default **on**. Unset reads as on, which
is what every instance provisioned before this existed in fact was.

What switching it off does, and what it deliberately does not do:

  off   every route this plugin registers answers 404, as if the blueprint had
        never been registered: the workshop pages, the toolbox, the runtime
        dist, the workspace API, the graph API, Jump, /supervisor/join, and the
        seven admin pages. `/` and post-login stop being retargeted, so a
        participant lands on the challenge board. The navbar drops the workshop
        entries and shows Challenges to everyone. The admin bar keeps exactly
        one entry: this page.

  kept  the Epitech theme, in full — the shell overrides and both stylesheets.
        Turning the workshop off is not the same as wanting CTFd's own dark
        navbar back, and an instance that changes its face between sessions
        would be a different decision from the one asked for.

  kept  the `quiz` challenge type. Removing it from `CHALLENGE_CLASSES` would
        make every quiz challenge already in the database raise a `KeyError` on
        the board — the same failure mode CTFd's own missing user types have
        (see staff.py). A disabled plugin must never cost an instance its data.

  kept  authored Pages. "Parcours", a subject's index and the per-part Pages are
        *content*, created by the sync; they are the admin's to delete, and a
        switch that deleted content would not be a switch.

The guard is one `before_request`, and the set it checks is computed from the
app rather than typed out: every blueprint whose `import_name` is inside this
package, minus this module's own. A new blueprint is therefore covered the day
it is written, with nothing to remember.
"""
from flask import Blueprint, abort, redirect, render_template, request, url_for

from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only

from .protect import admins, protected_user

CONFIG_KEY = "workshop_enabled"
ROUTE = "/admin/workshop/plugin"

# Blueprints the switch never touches. The switch's own, obviously — it is the
# way back. And `workshop_protect` (PLAN.md §40): an instance with the workshop
# turned off is still an instance whose first admin account should survive a
# misclick, so that guard is not part of what "off" means.
ALWAYS_ON = ("workshop_toggle", "workshop_protect")

workshop_toggle = Blueprint("workshop_toggle", __name__,
                            template_folder="templates")


def workshop_enabled():
    """Is the workshop side of this instance switched on?

    Unset means on. An instance that has never seen this page must behave
    exactly as it did before the page existed, and every instance in the field
    is in that state.
    """
    value = get_config(CONFIG_KEY)
    return True if value is None else bool(value)


def set_workshop_enabled(on):
    # "true"/"false", because `_get_config` turns exactly those two strings back
    # into booleans (CTFd/utils/__init__.py) and an int would read back as 1/0.
    set_config(CONFIG_KEY, "true" if on else "false")


class _MenuWhenEnabled(list):
    """`app.admin_plugin_menu_bar`, minus this plugin's entries while it is off.

    The admin theme does `{% for menu in get_admin_plugin_menu_bar() %}` on
    whatever that attribute holds, so filtering in `__iter__` is the whole
    change and no core template moves. A list subclass and not a property
    because `register_admin_plugin_menu_bar` appends to this object, including
    for a plugin that loads after this one — those entries belong to somebody
    else and are never filtered.
    """

    def __iter__(self):
        entries = list(list.__iter__(self))
        if workshop_enabled():
            return iter(entries)
        return iter([m for m in entries
                     if not m.route.startswith("/admin/workshop/")
                     or m.route == ROUTE])


@workshop_toggle.route(ROUTE, methods=["GET", "POST"])
@admins_only
def plugin():
    """The only workshop page that answers while the plugin is off.

    Admins only, never `staff_only`: a supervisor runs the room, and whether
    the room exists at all is not theirs to decide.
    """
    if request.method == "POST":
        set_workshop_enabled(request.form.get("enabled") == "on")
        return redirect(url_for("workshop_toggle.plugin", saved=1))
    return render_template("workshop_plugin.html",
                           enabled=workshop_enabled(),
                           saved=request.args.get("saved"),
                           # The protected-administrator picker lives on this
                           # page because this is the plugin's instance-level
                           # page and the only one that answers in both states
                           # — protect.py owns the rule and the POST (§40).
                           keeper=protected_user(), admins=admins(),
                           notice=request.args.get("notice"))


def guard_routes(app):
    """404 every route of this plugin while it is off. Called last, once every
    blueprint is registered, because the set is read off the app."""
    root = __package__
    blocked = frozenset(
        name for name, bp in app.blueprints.items()
        if (getattr(bp, "import_name", "") or "") == root
        or (getattr(bp, "import_name", "") or "").startswith(root + ".")
    ) - set(ALWAYS_ON)

    @app.before_request
    def _workshop_off():
        # Blueprint first: this runs on every request in the instance, and a
        # core one must not pay for a config lookup to learn it is not ours.
        if request.blueprint in blocked and not workshop_enabled():
            abort(404)


def load_toggle(app):
    app.register_blueprint(workshop_toggle)
    # Templates ask; the navbar drops the workshop entries and shows Challenges
    # to everyone, and login.html loses the Jump button through `jump_enabled`.
    app.jinja_env.globals["workshop_enabled"] = workshop_enabled
    app.admin_plugin_menu_bar = _MenuWhenEnabled(app.admin_plugin_menu_bar)
