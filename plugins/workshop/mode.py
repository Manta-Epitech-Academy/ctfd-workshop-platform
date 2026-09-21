"""The instance's usage mode (PLAN.md §25).

Two usages, one platform:

  instructor_led   a room, at a time, with somebody teaching. A `checkpoint`
                   step is validated by the code that instructor reads out.
  self_serve       whoever finds the URL, whenever. The same step is validated
                   by a button, because there is nobody to ask for a code.

The mode is **instance-wide** — §10 already puts one instance behind one
session, and the mode describes the room rather than the content — and it is
read at render and submit time rather than baked into the imported content, so
that flipping it after a session is a toggle and not a re-import. Nothing about
the DAG, the points or the solves moves with it, which is what makes the flip
safe in both directions.

Default `instructor_led`: an instance that self-validates is a deliberate act,
so it is the one that has to be asked for.
"""
from flask import Blueprint, redirect, render_template, request, url_for

from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only

from .staff import supervisor_code, supervisors

INSTRUCTOR_LED = "instructor_led"
SELF_SERVE = "self_serve"
MODES = (INSTRUCTOR_LED, SELF_SERVE)
CONFIG_KEY = "workshop_mode"

LABELS = {
    INSTRUCTOR_LED: "Instructor-led",
    SELF_SERVE: "Self-serve",
}

workshop_mode = Blueprint("workshop_mode", __name__, template_folder="templates")


def current_mode():
    """The instance's mode, falling back to the default for anything unset.

    An instance provisioned before this key existed reads as instructor-led,
    which is what it in fact was: its checkpoint steps only ever accepted the
    code.
    """
    value = get_config(CONFIG_KEY)
    return value if value in MODES else INSTRUCTOR_LED


def is_self_serve():
    return current_mode() == SELF_SERVE


@workshop_mode.route("/admin/workshop/settings", methods=["GET", "POST"])
@admins_only
def settings():
    saved = False
    if request.method == "POST":
        wanted = request.form.get("workshop_mode")
        if wanted in MODES:
            set_config(CONFIG_KEY, wanted)
            saved = True
        else:
            return redirect(url_for("workshop_mode.settings"))
    return render_template(
        "workshop_settings.html", mode=current_mode(), labels=LABELS, saved=saved,
        # The supervisor tier is managed on this page too (staff.py handles
        # the POSTs, at /admin/workshop/supervisors); `notice` is what one of
        # them has to say on the way back.
        supervisor_code=supervisor_code(), supervisors=supervisors(),
        notice=request.args.get("notice"))


def load_mode(app):
    app.register_blueprint(workshop_mode)
