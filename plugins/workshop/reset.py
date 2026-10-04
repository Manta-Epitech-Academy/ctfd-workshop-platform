"""An admin starts the subject again (PLAN.md §47).

    POST /api/v1/workshop/progress/reset

An admin tests a subject by doing it, and a subject done once cannot be tested
twice: every step is solved, every hint is open, and the page opens on the
closing step. Until now the way back was to delete rows from the challenge
list by hand, or to make a second account.

The button is in the account menu, for admins. It removes **the signed-in
admin's own** solves, attempts, hint unlocks, awards and ratings, the same
tables CTFd's own reset empties for everybody (CTFd/admin/__init__.py), and
the work in progress saved for the runtime (workspace.py).

**The account comes from the session, never from the body.** There is no user
id to pass, so this cannot be pointed at a participant: resetting somebody
else's work is a different feature, with a different confirmation, and it is
not this one.

**The runtime's code goes too**, because the point is to see what somebody
new sees, and somebody new does not open the editor on a finished game. That
code lives in two places: a row here, and the browser's localStorage, which
the page would post straight back (assets/runtime.js). So this deletes the
row and answers with the names of the keys it held, and assets/reset.js
removes those keys from the browser before the page reloads. A second browser
the admin left open on another machine still holds its copy, and will restore
it: that one has to be closed.
"""
from flask import Blueprint, jsonify

from CTFd.cache import clear_challenges, clear_ratings, clear_standings
from CTFd.models import Awards, Ratings, Solves, Submissions, Unlocks, db
from CTFd.utils.decorators import admins_only
from CTFd.utils.user import get_current_user

from .workspace import WorkspaceState

workshop_reset = Blueprint("workshop_reset", __name__)


@workshop_reset.route("/api/v1/workshop/progress/reset", methods=["POST"])
@admins_only
def reset():
    user = get_current_user()
    removed = {}
    # Solves before Submissions: a solve is a submission with a row of its own,
    # and this is the order CTFd's reset uses.
    for name, model in (("solves", Solves), ("attempts", Submissions),
                        ("hints", Unlocks), ("awards", Awards), ("ratings", Ratings)):
        removed[name] = model.query.filter_by(user_id=user.id).delete(
            synchronize_session=False)
    # The key names, not the values: the browser needs to know what to remove
    # from its own storage, and has no use for the code it is about to erase.
    runtime_keys = set()
    for row in WorkspaceState.query.filter_by(user_id=user.id).all():
        runtime_keys.update((row.data or {}).keys())
        db.session.delete(row)
        removed["runtime"] = removed.get("runtime", 0) + 1
    removed.setdefault("runtime", 0)
    db.session.commit()
    # The page reads solves through a memoized helper (progress.py); without
    # this the reset would not show for up to a minute.
    clear_standings()
    clear_challenges()
    clear_ratings()
    return jsonify({"success": True, "data": {**removed,
                                              "runtime_keys": sorted(runtime_keys)}})


def load_reset(app):
    app.register_blueprint(workshop_reset)
