"""Server-side persistence of work in progress (PLAN.md §16).

The runtime frame is served from CTFd's own origin, so the frame and the
workshop page share one `localStorage` bucket (PLAN.md §14.2). That means the
host can read and write the runtime's keys **directly** — no message, no
cooperation from the runtime, no change to its repository. This module is only
the other half: somewhere to put those keys, per participant.

Why it matters: without it, work in progress lives in the browser, so two
students sharing a classroom PC share a cart, and a student who moves machines
starts from nothing. The client-side rules that decide which copy wins live in
`assets/runtime.js`; everything here is deliberately dumb storage.

Endpoints (both `authed_only`, both scoped to the session user):

    GET  /api/v1/workshop/workspace   -> {runtime, keys, updated}
    POST /api/v1/workshop/workspace   -> upsert, returns {updated}

**The user id comes from the session, never from the body.** A body-supplied id
would turn this into "read another student's work in progress".
"""
import json
from datetime import datetime

from flask import Blueprint, request
from sqlalchemy.exc import IntegrityError

from CTFd.models import db
from CTFd.utils.decorators import authed_only, ratelimit
from CTFd.utils.user import get_current_user

from .runtime import declared_runtime

# Caps. A TIC-80 cart with sprite and map data is tens of KB, so this is
# generous — the limit is here so a stuck client cannot fill the disk, not to
# ration legitimate work. Over it, the client is told to stop rather than left
# retrying forever.
MAX_KEYS = 16
MAX_BYTES = 512 * 1024
# The largest body we are willing to pull off the wire: the keys plus the JSON
# envelope around them. The check has to happen *before* the parse (see write),
# so it is a bound on bytes read, not on the decoded object.
MAX_BODY = MAX_BYTES + 8 * 1024


class WorkspaceState(db.Model):
    """One row per (participant, runtime): their current work in progress.

    Not per step and not versioned — see PLAN.md §16.5. `data` holds the
    runtime's own `localStorage` keys verbatim, which is what makes this blind
    to the runtime's schema: the platform never parses a cart.
    """
    __tablename__ = "workshop_workspace"
    __table_args__ = (db.UniqueConstraint("user_id", "runtime_id"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True)
    runtime_id = db.Column(db.String(64), nullable=False)
    data = db.Column(db.JSON)
    updated = db.Column(db.DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


workshop_workspace = Blueprint("workshop_workspace", __name__)


def _runtime_id():
    rt = declared_runtime()
    return (rt or {}).get("id")


def _payload(row):
    return {
        "runtime": row.runtime_id if row else _runtime_id(),
        "keys": (row.data if row else None) or {},
        "updated": row.updated.isoformat() + "Z" if row and row.updated else None,
    }


@workshop_workspace.route("/api/v1/workshop/workspace", methods=["GET"])
@authed_only
def read():
    user = get_current_user()
    runtime_id = _runtime_id()
    row = None
    if runtime_id:
        row = WorkspaceState.query.filter_by(
            user_id=user.id, runtime_id=runtime_id).first()
    return {"success": True, "data": _payload(row)}


@workshop_workspace.route("/api/v1/workshop/workspace", methods=["POST"])
@authed_only
# A client flushes at most once every MIN_SAVE_MS (assets/runtime.js) — four
# times a minute, a few more with several tabs open. The subject is the session
# user, not the IP (CTFd/utils/decorators/__init__.py:173), so a whole room
# behind one NAT does not share this budget.
@ratelimit(method="POST", limit=60, interval=60)
def write():
    user = get_current_user()
    runtime_id = _runtime_id()
    if not runtime_id:
        return {"success": False, "errors": {"": ["no runtime declared"]}}, 400

    # Read the body bounded instead of through `request.get_json()`, which
    # materializes all of it first: checking the size after the parse means an
    # oversized POST is already in memory by the time it is refused, and one
    # gevent worker holds the whole instance. Reading from the stream also
    # covers a body that understates its Content-Length or sends none at all.
    raw = request.stream.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        return {"success": False,
                "errors": {"keys": ["workspace too large to save"]}}, 413
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    keys = body.get("keys")
    # Only the instance's own runtime, so a stray client cannot accumulate rows
    # for runtimes this instance has never heard of.
    if body.get("runtime") != runtime_id:
        return {"success": False, "errors": {"runtime": ["unknown runtime"]}}, 400
    if not isinstance(keys, dict) or len(keys) > MAX_KEYS:
        return {"success": False, "errors": {"keys": ["expected a small object"]}}, 400
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in keys.items()):
        return {"success": False, "errors": {"keys": ["expected strings"]}}, 400
    if len(json.dumps(keys)) > MAX_BYTES:
        return {"success": False,
                "errors": {"keys": ["workspace too large to save"]}}, 413

    row = WorkspaceState.query.filter_by(
        user_id=user.id, runtime_id=runtime_id).first()
    if row is None:
        row = WorkspaceState(user_id=user.id, runtime_id=runtime_id)
        db.session.add(row)
    row.data = keys
    row.updated = datetime.utcnow()
    try:
        db.session.commit()
    except IntegrityError:
        # Two tabs racing the first save of the same bucket. One of them wins,
        # and losing is harmless: the next flush carries the same content.
        db.session.rollback()
        row = WorkspaceState.query.filter_by(
            user_id=user.id, runtime_id=runtime_id).first()
    return {"success": True, "data": _payload(row)}


def load_workspace(app):
    app.register_blueprint(workshop_workspace)
