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

**A third state: the board, with codes** (PLAN.md §71). An ordinary CTF can
still want what the token door gives: nobody types a name, and each
registration code is a cohort that sees only itself. So off comes in two
kinds, told apart by a second key, `workshop_board_codes`:

  board  everything above stays off, except the way in. The token door is on
         the login page, `/` sends an anonymous visitor to it, and the two
         blueprints behind it answer (`BOARD_BLUEPRINTS`). The admin bar
         keeps "Registration codes" beside this page. Whoever comes in lands
         on the challenge board.

`workshop_enabled()` is false in that state too. Everything that asks it is
asking "is there a workshop here", and there is not; the few places that ask
about the door instead ask `door_enabled()`.
"""
from flask import Blueprint, abort, redirect, render_template, request, url_for

from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only

CONFIG_KEY = "workshop_enabled"
BOARD_KEY = "workshop_board_codes"
ROUTE = "/admin/workshop/plugin"
CODES_ROUTE = "/admin/workshop/codes"

WORKSHOP, BOARD, OFF = "workshop", "board", "off"
# What still answers in the board state: the token door's three requests, the
# waiting page and the codes' admin page (codes.py, tokendoor.py).
BOARD_BLUEPRINTS = frozenset({"workshop_codes", "workshop_tokendoor"})

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


def board_with_codes():
    """Is this a plain board that keeps the token door (PLAN.md §71)?

    Only ever true with the workshop off: the second key is not read while the
    first one says on, so an instance that never saw this state is unchanged.
    """
    return not workshop_enabled() and bool(get_config(BOARD_KEY))


def door_enabled():
    """Does the login page have a participant side at all?"""
    return workshop_enabled() or board_with_codes()


def plugin_state():
    if workshop_enabled():
        return WORKSHOP
    return BOARD if board_with_codes() else OFF


def set_plugin_state(state):
    # "true"/"false", because `_get_config` turns exactly those two strings back
    # into booleans (CTFd/utils/__init__.py) and an int would read back as 1/0.
    set_config(CONFIG_KEY, "true" if state == WORKSHOP else "false")
    set_config(BOARD_KEY, "true" if state == BOARD else "false")


def set_workshop_enabled(on):
    set_plugin_state(WORKSHOP if on else OFF)


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
        kept = {ROUTE, CODES_ROUTE} if board_with_codes() else {ROUTE}
        return iter([m for m in entries
                     if not m.route.startswith("/admin/workshop/")
                     or m.route in kept])


@workshop_toggle.route(ROUTE, methods=["GET", "POST"])
@admins_only
def plugin():
    """The only workshop page that answers while the plugin is off.

    Admins only, never `staff_only`: a supervisor runs the room, and whether
    the room exists at all is not theirs to decide.
    """
    if request.method == "POST":
        state = request.form.get("state")
        if state in (WORKSHOP, BOARD, OFF):
            set_plugin_state(state)
        else:
            # The form this page had while there were two states. Scripts
            # still post it (scripts/toggle_check.py).
            set_workshop_enabled(request.form.get("enabled") == "on")
        return redirect(url_for("workshop_toggle.plugin", saved=1))
    return render_template("workshop_plugin.html",
                           state=plugin_state(),
                           saved=request.args.get("saved"))


def guard_routes(app):
    """404 every route of this plugin while it is off. Called last, once every
    blueprint is registered, because the set is read off the app."""
    root = __package__
    blocked = frozenset(
        name for name, bp in app.blueprints.items()
        if (getattr(bp, "import_name", "") or "") == root
        or (getattr(bp, "import_name", "") or "").startswith(root + ".")
    ) - {workshop_toggle.name}

    @app.before_request
    def _workshop_off():
        # Blueprint first: this runs on every request in the instance, and a
        # core one must not pay for a config lookup to learn it is not ours.
        if request.blueprint not in blocked or workshop_enabled():
            return
        if request.blueprint in BOARD_BLUEPRINTS and board_with_codes():
            return
        abort(404)


def load_toggle(app):
    app.register_blueprint(workshop_toggle)
    # Templates ask; the navbar drops the workshop entries and shows Challenges
    # to everyone, and login.html loses its doors through `open_doors` (doors.py).
    app.jinja_env.globals["workshop_enabled"] = workshop_enabled
    app.jinja_env.globals["workshop_board_codes"] = board_with_codes
    app.admin_plugin_menu_bar = _MenuWhenEnabled(app.admin_plugin_menu_bar)
