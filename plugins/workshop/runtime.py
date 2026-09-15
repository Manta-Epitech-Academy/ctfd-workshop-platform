"""Runtime panes — serving the runtime and exposing its declaration.

A "runtime" is an existing standalone web app (tic80-web-editor, a v86 shell,
a p5.js editor…) shown next to the workshop instructions in a hideable pane.
See PLAN.md §14 for the decision record; docs/RUNTIME_PROTOCOL.md for the
message contract.

**Same origin by construction.** The built dist is served from CTFd's own
origin at `/runtime/<id>/<version>/`, which buys three things that a
cross-origin iframe cannot: the runtime keeps using `localStorage` exactly as
it does standalone (tic80 autosaves the cart there), clipboard and focus work
without permission grants, and the host can inject the adapter into the frame
— so the runtime's own repository needs no modification at all.

Dists are not vendored: `tools/build_runtime.sh` produces
`plugins/workshop/runtimes/<id>/<version>/` (gitignored). In production this
directory is what nginx would serve directly; Flask serving it is fine for a
session-sized deployment and keeps the compose file to one service.
"""
import json
import mimetypes
import re
from pathlib import Path

from flask import Blueprint, abort, current_app, send_from_directory

from CTFd.utils import get_config

RUNTIME_ROOT = Path(__file__).parent / "runtimes"
CONFIG_KEY = "workshop_runtime"
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

workshop_runtime = Blueprint("workshop_runtime", __name__)

# The glyph on the launcher, keyed by runtime id — the same knowledge the adapter
# path below is keyed on, so it lives here rather than in the content. A subject
# repository therefore needs no new field and no asset to get a mark on the
# button, and a runtime nobody mapped still gets a sensible one.
#
# Font Awesome, not jump's Lucide: DESIGN.md records that deviation for the
# header (`color_mode_switcher.js` dereferences `.theme-switch i.fas`
# unguarded), and one icon set per page is the point of it.
ICONS = {
    "tic80": "gamepad",
    "pacman": "ghost",
    "shell-1": "terminal",
    "shell-rpg": "terminal",
    "miniasm": "microchip",
    "mock": "flask",
}
DEFAULT_ICON = "play"

# Warned-about (id, version) pairs. `declared_runtime()` runs on every workshop
# request, so an unconditional warning would be one log line per page view.
_warned = set()


def _dist(runtime_id, version):
    if not (SAFE.match(runtime_id) and SAFE.match(version)):
        abort(404)
    path = RUNTIME_ROOT / runtime_id / version
    if not path.is_dir():
        abort(404)
    return path


@workshop_runtime.route("/runtime/<runtime_id>/<version>/",
                        defaults={"path": "index.html"})
@workshop_runtime.route("/runtime/<runtime_id>/<version>/<path:path>")
def serve(runtime_id, version, path):
    """Static dist. Deliberately unauthenticated: these are public web apps,
    and requiring a session here would break the WASM/asset fetches the frame
    makes. Nothing about the workshop lives in them."""
    return send_from_directory(_dist(runtime_id, version), path)


def declared_runtime():
    """The runtime this instance declares, or None.

    Written at sync time from the subject's `runtime:` block (see
    tools/sync_subject.py). Returns None when nothing is declared or when the
    dist is missing, so a page never renders a pane pointing at a 404.
    """
    raw = get_config(CONFIG_KEY)
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
    except (TypeError, ValueError):
        return None

    runtime_id, version = cfg.get("id"), cfg.get("version")
    if not (runtime_id and version):
        return None
    if not cfg.get("external") and not (RUNTIME_ROOT / runtime_id / version).is_dir():
        # The dist is gitignored and built per instance, so this is a deployment
        # state, not a content error — but it removes the pane, the launcher and
        # the script from every page, and it used to do so in complete silence.
        # That silence is how an instance ends up looking correctly deployed
        # while having no runtime at all.
        if (runtime_id, version) not in _warned:
            _warned.add((runtime_id, version))
            current_app.logger.warning(
                "workshop: subject declares runtime %s@%s but %s does not "
                "exist — the runtime pane is disabled on every page. Build it "
                "with tools/build_runtime.sh %s",
                runtime_id, version, RUNTIME_ROOT / runtime_id / version,
                runtime_id,
            )
        return None

    pane = cfg.get("pane") or {}
    return {
        "params": cfg.get("params") or {},
        # Per-subject overrides, keyed by subject slug (PLAN.md §19.2): one dist
        # serves several subjects and what differs between them is data.
        "subjects": cfg.get("subjects") or {},
        "id": runtime_id,
        "version": version,
        "title": cfg.get("title") or runtime_id,
        "src": cfg.get("src") or f"/runtime/{runtime_id}/{version}/",
        "adapter": f"/plugins/workshop/assets/runtime/adapters/{runtime_id}.js",
        "icon": ICONS.get(runtime_id, DEFAULT_ICON),
        "placement": pane.get("placement", "side"),
        "open": bool(pane.get("open", False)),
        "size": int(pane.get("size", 55)),
    }


def missing_dist():
    """(id, version, path) when a runtime is declared and its dist is absent.

    `declared_runtime()` answers None in that case, which is the right answer
    for a page — it must never point a frame at a 404 — but the wrong one for an
    admin, who needs to be told the difference between "this subject has no
    runtime" and "this subject's runtime is not built here". The sync page asks
    this; nothing else should branch on it.
    """
    raw = get_config(CONFIG_KEY)
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
    except (TypeError, ValueError):
        return None
    runtime_id, version = cfg.get("id"), cfg.get("version")
    if not (runtime_id and version) or cfg.get("external"):
        return None
    path = RUNTIME_ROOT / runtime_id / version
    return None if path.is_dir() else (runtime_id, version, str(path))


def load_runtime(app):
    # Emscripten uses WebAssembly.instantiateStreaming, which rejects anything
    # not served as application/wasm. Python's mimetypes does not know .wasm on
    # every platform, so state it.
    mimetypes.add_type("application/wasm", ".wasm")
    app.register_blueprint(workshop_runtime)
