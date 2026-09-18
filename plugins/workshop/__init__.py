"""Workshop plugin — custom CTFd behavior for the workshop platform.

Phase 1 scope: the `quiz` challenge type (see quiz.py). Later phases add the
session endpoint, workshop config, checkpoint validation, and the review queue
(PLAN.md §9-12) without ever modifying CTFd core.

Deployed by bind-mounting this directory into CTFd/CTFd/plugins/workshop
(see docker-compose.yml) so the CTFd checkout stays pristine.
"""
import os
from CTFd.plugins import (
    register_admin_plugin_menu_bar,
    register_plugin_assets_directory,
    register_plugin_script,
    register_plugin_stylesheet,
)
from CTFd.plugins.challenges import CHALLENGE_CLASSES
from CTFd.plugins.migrations import upgrade as upgrade_plugin

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
from .shell import load_shell
from .jump import load_jump
from .jumpqueue import load_jump_queue
from .external_links import load_external_links
from .quiz import QuizChallenge


def _load_translations(app):
    """Add this plugin's message catalogue to the ones flask-babel reads.

    CTFd ships a full French catalogue of its own and initialises Babel long
    before plugins load (CTFd/__init__.py). That is not a problem:
    `Domain.translation_directories` reads BABEL_TRANSLATION_DIRECTORIES at
    lookup time, not at init, and `get_translations` MERGES every directory in
    it — so appending here supplements CTFd's catalogue instead of shadowing it,
    and a string CTFd already translates ("Scoreboard", "Settings", "Logout")
    keeps its upstream translation for free.

    Semicolon-separated, absolute paths honoured, both per flask-babel's own
    parsing. `CTFd/` is untouched.
    """
    own = os.path.join(os.path.dirname(__file__), "translations")
    if not os.path.isdir(own):
        return
    current = app.config.get("BABEL_TRANSLATION_DIRECTORIES", "translations")
    parts = [p for p in current.split(";") if p]
    if own not in parts:
        parts.append(own)
        app.config["BABEL_TRANSLATION_DIRECTORIES"] = ";".join(parts)


def load(app):
    # The participant-facing strings are translatable, and French is what an
    # instance is set to by default (tools/provision.py). Registered first so
    # every blueprint below renders through it.
    _load_translations(app)
    # Creates the plugin's missing tables (quiz, workspace, the two Jump
    # tables). Idempotent — existing tables are untouched — and kept even
    # though migrations now exist, because the quiz and workspace tables
    # predate them and are in no revision.
    app.db.create_all()
    # And the ledger, for everything create_all() cannot do. It never issues an
    # ALTER, so the first column any of these tables gains later would surface
    # as an OperationalError at request time on every instance at once; a
    # migrations/ directory is where that change gets written instead. Revision
    # 1 is idempotent, so it is a no-op on an instance create_all() has already
    # served — what it really does is record `workshop_alembic_version`, which
    # is what revision 2 starts from.
    upgrade_plugin(plugin_name="workshop")
    CHALLENGE_CLASSES["quiz"] = QuizChallenge
    # A link out of the workshop (the Lua manual from a toolbox, say) opens in
    # a new tab, so coming back does not cost the runtime pane and the scroll
    # position. Wraps the one Markdown entry point every surface renders
    # through — see external_links.py.
    load_external_links(app)
    load_graph(app)  # GET /api/v1/workshop/graph — challenge DAG for the user
    # The participant-facing view: the whole workshop as one page, steps as
    # accordions with progress steppers, instead of a modal per challenge.
    load_page(app)
    # `/workshop` is where a signed-in participant lands (`/` and post-login);
    # the challenge board stays reachable at /challenges — see landing.py.
    load_landing(app)
    # The Epitech/Jump app shell, replacing core's dark fixed-top navbar. It is
    # what carries the "Workshop" nav entry: this used to be deliberately absent
    # because a plugin menu entry always sorts *after* the per-document Pages the
    # sync creates, leaving it in the wrong place. Those Pages are hidden from the
    # navbar now (tools/sync_subject.py) and the shell owns the item order
    # outright, so the entry can finally sit first, where it belongs — until now
    # /workshop was reachable only by clicking the logo. See shell.py.
    load_shell(app)
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
    # The way in from Jump, and the way progress gets back (PLAN.md §31).
    # Jump is the only door into a workshop instance: a signed ticket opens the
    # session at /jump/enter, and a solved step is queued and posted back so
    # the talent earns XP without a script being run by hand on the database.
    # Both halves are ours — nothing under CTFd/ moves for either.
    load_jump(app)
    load_jump_queue(app)
    register_admin_plugin_menu_bar("Jump", "/admin/workshop/jump")
    register_plugin_assets_directory(app, base_path="/plugins/workshop/assets/")
    # Caps hint images (the theme only caps description images) — see
    # assets/workshop.css. Injected via {{ Plugins.styles }} in base.html.
    register_plugin_stylesheet(url="/plugins/workshop/assets/workshop.css")
    # Epitech brand reskin (colors, fonts, radius) — token contract in
    # DESIGN.md. Loads after workshop.css so nothing here needs !important.
    register_plugin_stylesheet(url="/plugins/workshop/assets/epitech-theme.css")
    # Makes free (cost-0) hints read as free and skip the unlock dialog, while
    # keeping CTFd's native HintUnlocks reveal tracking — see assets/hints.js.
    register_plugin_script(url="/plugins/workshop/assets/hints.js")
    # Branching next-challenge button + the Parcours path graph — see
    # assets/graph.js, backed by GET /api/v1/workshop/graph.
    register_plugin_script(url="/plugins/workshop/assets/graph.js")
