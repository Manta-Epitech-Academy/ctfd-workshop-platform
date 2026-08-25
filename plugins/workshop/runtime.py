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

from flask import Blueprint, abort, send_from_directory

from CTFd.utils import get_config

RUNTIME_ROOT = Path(__file__).parent / "runtimes"
CONFIG_KEY = "workshop_runtime"
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

workshop_runtime = Blueprint("workshop_runtime", __name__)


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
        "placement": pane.get("placement", "side"),
        "open": bool(pane.get("open", False)),
        "size": int(pane.get("size", 55)),
    }


def load_runtime(app):
    # Emscripten uses WebAssembly.instantiateStreaming, which rejects anything
    # not served as application/wasm. Python's mimetypes does not know .wasm on
    # every platform, so state it.
    mimetypes.add_type("application/wasm", ".wasm")
    app.register_blueprint(workshop_runtime)
