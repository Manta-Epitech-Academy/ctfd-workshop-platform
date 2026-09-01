"""Workshop plugin — custom CTFd behavior for the workshop platform.

Phase 1 scope: the `quiz` challenge type (see quiz.py). Later phases add the
session endpoint, workshop config, checkpoint validation, and the review queue
(PLAN.md §9-12) without ever modifying CTFd core.

Deployed by bind-mounting this directory into CTFd/CTFd/plugins/workshop
(see docker-compose.yml) so the CTFd checkout stays pristine.
"""
from CTFd.plugins import (
    register_admin_plugin_menu_bar,
    register_plugin_assets_directory,
    register_plugin_script,
    register_plugin_stylesheet,
)
from CTFd.plugins.challenges import CHALLENGE_CLASSES

from .graph import load_graph
from .landing import load_landing
from .page import load_page
from .runtime import load_runtime
from .workspace import load_workspace
from .feedback import load_feedback
from .answers import load_answers
from .mode import load_mode
from .checkpoint import load_checkpoint
from .syncpage import load_syncpage
from .quiz import QuizChallenge


def load(app):
    # Creates the plugin's missing tables (quiz model). Idempotent — existing
    # tables are untouched. If a later phase alters a column, switch to
    # CTFd.plugins.migrations.upgrade() with a migrations/ directory.
    app.db.create_all()
    CHALLENGE_CLASSES["quiz"] = QuizChallenge
    load_graph(app)  # GET /api/v1/workshop/graph — challenge DAG for the user
    # The participant-facing view: the whole workshop as one page, steps as
    # accordions with progress steppers, instead of a modal per challenge.
    load_page(app)
    # No static "Workshop" entry: the sync creates one Page per document, so the
    # navbar reads <CTF name> | Parcours | <part 1> … <part N> | Users | … . A
    # fixed entry here would always sort *after* those (CTFd builds the menu as
    # get_pages() + plugin entries) and would duplicate the only part of a
    # single-document subject. `/workshop` stays the post-login landing page.
    # ...and it is where a signed-in participant lands (`/` and post-login).
    # The challenge board stays reachable at /challenges — see landing.py.
    load_landing(app)
    # Serves the runtime dists at /runtime/<id>/<version>/ on CTFd's own origin
    # — same-origin is what makes localStorage, clipboard and adapter injection
    # work (PLAN.md §14.2). See runtime.py.
    load_runtime(app)
    # Work in progress, kept server-side per participant: the frame is
    # same-origin, so the host reads the runtime's own localStorage keys and
    # posts them here (PLAN.md §16). Fixes the shared classroom PC, where two
    # students on one browser profile otherwise share a cart.
    load_workspace(app)
    # Two rating populations land in CTFd's own table and nothing surfaced them
    # (PLAN.md §17). The report sits in the admin menu bar, where an instructor
    # already looks, rather than on a URL somebody has to be told about.
    load_feedback(app)
    register_admin_plugin_menu_bar("Feedback", "/admin/workshop/feedback")
    # Every step's answer, and how far each participant got (PLAN.md §23). The
    # codes are minted per instance and the sheet only ever existed on the
    # maintainer's laptop, so an instructor in the room had no way to read one.
    # Admin-only until the instructor role lands with instructor-led mode:
    # CTFd has no third user type, so that tier has to be plugin-side.
    load_answers(app)
    register_admin_plugin_menu_bar("Answers", "/admin/workshop/answers")
    # Instructor-led or self-serve (PLAN.md §25). One instance, one mode: it
    # decides whether a checkpoint step asks for the instructor's code or
    # offers a button, and what the page says under the step. Read at render
    # and submit time, so flipping it after a session is a toggle rather than
    # a re-import.
    load_mode(app)
    register_admin_plugin_menu_bar("Workshop mode", "/admin/workshop/settings")
    # The two admin-only routes the importer needs for checkpoint steps: read
    # the codes back (they live in a column no API serializes) and convert the
    # `standard` challenges of an instance synced before §25 in place, without
    # touching a single solve. See checkpoint.py.
    load_checkpoint(app)
    # Import this instance's workshop straight from its repository (PLAN.md
    # §26): clone the subject repo, edit, push, press Sync. The importer is the
    # same tools/sync_subject.py the CLI runs, mounted read-only; an encrypted
    # answers file is opened in the admin's browser, so the passphrase never
    # reaches the server. See syncpage.py and source.py.
    load_syncpage(app)
    register_admin_plugin_menu_bar("Sync content", "/admin/workshop/sync")
    register_plugin_assets_directory(app, base_path="/plugins/workshop/assets/")
    # Caps hint images (the theme only caps description images) — see
    # assets/workshop.css. Injected via {{ Plugins.styles }} in base.html.
    register_plugin_stylesheet(url="/plugins/workshop/assets/workshop.css")
    # Makes free (cost-0) hints read as free and skip the unlock dialog, while
    # keeping CTFd's native HintUnlocks reveal tracking — see assets/hints.js.
    register_plugin_script(url="/plugins/workshop/assets/hints.js")
    # Branching next-challenge button + the Parcours path graph — see
    # assets/graph.js, backed by GET /api/v1/workshop/graph.
    register_plugin_script(url="/plugins/workshop/assets/graph.js")
