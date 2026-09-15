"""Reporting a solved step back to Jump, without making anyone wait (§31).

    solve()  ->  one guarded INSERT, return         (inside the request)
    drainer  ->  recount, sign, POST, mark sent     (outside it)

## Why an outbox and not a POST from `solve()`

Under the gevent worker `requests` does not block the process, but the greenlet
keeps its SQLAlchemy connection for the whole call. With `WORKERS=1` and a pool
of 5 plus 20 overflow (CTFd/config.py:284), thirty students solving while Jump
is slow exhaust the pool — and what then starts failing is *unrelated*
requests. The blast radius of the synchronous version is not "reporting is
slow", it is "the instance falls over because Jump is slow".

A bare background greenlet fixes the latency and loses events in silence on a
restart or a 500. XP that never arrive, with no trace anywhere, is the worst
failure this feature has: the student did the work, Jump disagrees, and nothing
says so. Hence a table, a retry ladder, and a page an instructor can look at.

## Counters are computed here, never in `solve()`

`get_solve_ids_for_user_id` is memoized for 60 s and its invalidation runs
*after* the challenge plugin's hook (CTFd/api/v1/challenges.py:855-859, then
:884-885). A count taken inside `solve()` is therefore the count from *before*
the solve, and may be a minute stale on top of that. Taking it here, from
progress.py, is also what makes the number Jump receives the number the
participant is reading on the page.

## What the hook does not see

`QuizChallenge.solve()` misses two paths: steps of the `standard` challenge
type, and an admin marking a submission correct from the admin panel
(CTFd/api/v1/submissions.py:206-213). A SQLAlchemy `after_insert` listener on
`Solves` would catch both, at the cost of a coupling the plugin README does not
list and an interaction with CTFd's end-of-request `db.session.close()` that
has not been verified.

The MVP content is 100% `quiz` — verified 15/09 on the pacman instance,
`SELECT type, COUNT(*) FROM challenges GROUP BY type` returns `quiz 36` and
nothing else — so overriding `solve()` covers all of it. The listener and the
pull reconciler close the gap together, before Halloween. **Re-run that query
before assuming this still holds on an instance whose content differs.**

## One worker

`WORKERS=1` today, so a module-level drainer is safe, the same assumption
syncpage.py already makes for its import job. Raising it would need
`SELECT ... FOR UPDATE SKIP LOCKED` (MariaDB 10.11 is what runs) or a Redis
lock, and this module would have to say which.
"""
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timedelta

import requests
from flask import Blueprint, request

from CTFd.models import db
from CTFd.utils.decorators import admins_only

from .jump import JumpEvent, JumpLink, derived_key, instance_slug, jump_keys, requeue
from .progress import progress_for_user

CALLBACK_PATH = "/api/workshops/callback"
# Connect, then read. Short on the connect because an unreachable Jump should
# be known about in seconds; longer on the read because a busy one is still
# going to answer.
TIMEOUT = (5, 10)
# Eight tries over roughly twenty minutes, then the row stops moving and shows
# up on /admin/workshop/jump with a button. A queue that retries forever is a
# queue nobody ever looks at.
MAX_ATTEMPTS = 8
BACKOFF_BASE = 5
BACKOFF_CAP = 600
# How often the drainer looks. Small, because the row written by a solve should
# reach Jump while the student is still looking at the screen that celebrated
# it.
POLL_SECONDS = 2
BATCH = 50

_drainer = None
_drainer_lock = threading.Lock()


# --------------------------------------------------------------------------
# The request half
# --------------------------------------------------------------------------

def enqueue_solve(user_id, challenge_id):
    """Queue one solved step. Called from `QuizChallenge.solve()`.

    Two statements and no network, which is the whole contract with the
    request. A participant who did not arrive through Jump has no link row and
    costs one indexed lookup.

    The payload is deliberately left empty: what Jump needs is a ratio, and the
    ratio is only correct once CTFd has invalidated its solve cache, which
    happens after this returns. The drainer fills it in.
    """
    link = JumpLink.query.filter_by(user_id=user_id).first()
    if link is None:
        return None
    event = JumpEvent(
        user_id=user_id, challenge_id=challenge_id,
        jump_kid=link.jump_kid, jump_talent_id=link.jump_talent_id,
        status="pending", attempts=0, next_attempt=datetime.utcnow())
    db.session.add(event)
    try:
        db.session.commit()
    except Exception:
        # Already queued for this step — the unique (user_id, challenge_id)
        # doing its job on a re-solve. Nothing to repair: the row that is
        # already there reports the same thing, recounted at send time.
        db.session.rollback()
        return None
    return event


# --------------------------------------------------------------------------
# The sending half
# --------------------------------------------------------------------------

def build_payload(event, slug):
    solved, total = progress_for_user(event.user_id)
    return {
        "instanceSlug": slug,
        "talentId": event.jump_talent_id,
        "solvedSteps": solved,
        "totalSteps": total,
        "isComplete": bool(total) and solved == total,
    }


def sign(body, secret, ts):
    """`sha256=<hex>` over `f"{ts}.{body}"`, with the derived callback key.

    Mirrors `verifyCallbackSignature` in jump's `src/lib/server/hmac.ts`, which
    also refuses a timestamp more than 300 s from its own clock — so this is
    called at send time, from the live configuration, and never at enqueue
    time. Rotating the shared secret therefore does not fail the queue.
    """
    key = derived_key(secret, "jump/callback")
    return "sha256=" + hmac.new(key, f"{ts}.".encode() + body,
                                hashlib.sha256).hexdigest()


def post_event(event, key, slug):
    """Send one row. Returns `(ok, detail)`; never raises."""
    payload = build_payload(event, slug)
    # Serialized once, and passed as `data=`. With `json=`, requests would
    # re-serialize the dict and the signature would stop covering the bytes
    # that actually go on the wire.
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": ts,
        "X-Signature": sign(body, key["secret"], ts),
        # The row's natural key, so a resend whose response was lost is
        # recognised rather than counted twice.
        "X-Idempotency-Key": f"{event.user_id}:{event.challenge_id}",
    }
    event.payload = payload
    try:
        response = requests.post(key["origin"] + CALLBACK_PATH, data=body,
                                 headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, f"{type(exc).__name__}: {exc}"[:500]
    if 200 <= response.status_code < 300:
        return True, None
    return False, f"HTTP {response.status_code}: {response.text[:200]}"


def _back_off(event, detail):
    event.attempts += 1
    event.last_error = detail
    if event.attempts >= MAX_ATTEMPTS:
        event.status = "failed"
        return
    delay = min(BACKOFF_BASE * (2 ** (event.attempts - 1)), BACKOFF_CAP)
    event.next_attempt = datetime.utcnow() + timedelta(seconds=delay)


def send_one(event, keys=None, slug=None):
    """Attempt one row and record the outcome. Caller commits."""
    keys = jump_keys() if keys is None else keys
    slug = instance_slug() if slug is None else slug
    key = keys.get(event.jump_kid)
    if key is None:
        # Re-read at send time on purpose: retiring a dev origin must not leave
        # a queue retrying into nothing until the end of time.
        event.status = "dropped"
        event.last_error = f"key id {event.jump_kid!r} is no longer configured"
        return False
    if not slug:
        _back_off(event, "this instance has no slug configured")
        return False
    ok, detail = post_event(event, key, slug)
    if ok:
        event.status = "sent"
        event.sent = datetime.utcnow()
        event.last_error = None
    else:
        _back_off(event, detail)
    return ok


def drain_once():
    """One pass over everything that is due. Returns how many were sent."""
    keys, slug = jump_keys(), instance_slug()
    due = (JumpEvent.query
           .filter(JumpEvent.status == "pending",
                   JumpEvent.next_attempt <= datetime.utcnow())
           .order_by(JumpEvent.next_attempt)
           .limit(BATCH).all())
    sent = 0
    for event in due:
        if send_one(event, keys, slug):
            sent += 1
        db.session.commit()
    return sent


def _drain_forever(app):
    while True:
        try:
            with app.app_context():
                drain_once()
                # Hand the connection back and start the next pass on a fresh
                # transaction. Without this the drainer keeps one REPEATABLE
                # READ snapshot for its whole life and never sees a row written
                # after it started — a queue that silently stops draining.
                db.session.remove()
        except Exception:  # noqa: BLE001 — a drainer that dies is a silent outage
            try:
                app.logger.exception("workshop: the Jump drainer raised")
            except Exception:
                pass
        time.sleep(POLL_SECONDS)


def start_drainer(app):
    """Start the one drainer, once.

    A greenlet under the gevent worker, exactly like syncpage.py's import job.
    Guarded so a second `load()` (a reload, a test harness) cannot end up with
    two threads competing for the same rows.
    """
    global _drainer
    with _drainer_lock:
        if _drainer is not None and _drainer.is_alive():
            return _drainer
        _drainer = threading.Thread(target=_drain_forever, args=(app,),
                                    daemon=True, name="workshop-jump-drainer")
        _drainer.start()
        return _drainer


# --------------------------------------------------------------------------
# What an admin, and scripts/jump_check.py, can read back
# --------------------------------------------------------------------------

workshop_jump_api = Blueprint("workshop_jump_api", __name__)


def _event_json(event):
    return {
        "id": event.id,
        "user_id": event.user_id,
        "challenge_id": event.challenge_id,
        "kid": event.jump_kid,
        "talent_id": event.jump_talent_id,
        "status": event.status,
        "attempts": event.attempts,
        "payload": event.payload,
        "last_error": event.last_error,
        "next_attempt": event.next_attempt.isoformat() + "Z" if event.next_attempt else None,
        "sent": event.sent.isoformat() + "Z" if event.sent else None,
    }


@workshop_jump_api.route("/api/v1/workshop/jump/events", methods=["GET"])
@admins_only
def events():
    """The outbox, for the admin page's sake and for the check script's.

    A plugin table is invisible to CTFd's own API, and no script in `scripts/`
    reaches the database directly — checkpoint.py made the same call for the
    same reason: the plugin runs inside CTFd and has the database, so it is the
    thing that should answer.
    """
    query = JumpEvent.query
    if request.args.get("user_id"):
        query = query.filter(JumpEvent.user_id == request.args["user_id"])
    if request.args.get("status"):
        query = query.filter(JumpEvent.status == request.args["status"])
    rows = query.order_by(JumpEvent.id).limit(500).all()
    return {"success": True, "data": [_event_json(e) for e in rows]}


@workshop_jump_api.route("/api/v1/workshop/jump/events/<int:event_id>/resend",
                         methods=["POST"])
@admins_only
def resend(event_id):
    event = JumpEvent.query.filter_by(id=event_id).first()
    if event is None:
        return {"success": False, "errors": {"": ["no such event"]}}, 404
    requeue(event)
    db.session.commit()
    return {"success": True, "data": _event_json(event)}


@workshop_jump_api.route("/api/v1/workshop/jump/links", methods=["GET"])
@admins_only
def links():
    """Which account belongs to which talent.

    `has_password` rather than anything resembling one: it is the one fact
    worth asserting about these accounts — a Jump account has no password, so
    local sign-in cannot reach it — and there is no version of this endpoint
    that should be able to hand back the other thing.
    """
    from CTFd.models import Users

    rows = JumpLink.query.order_by(JumpLink.id).limit(500).all()
    users = {u.id: u for u in Users.query.filter(
        Users.id.in_([r.user_id for r in rows] or [0])).all()}
    out = []
    for row in rows:
        user = users.get(row.user_id)
        out.append({
            "user_id": row.user_id,
            "kid": row.jump_kid,
            "talent_id": row.jump_talent_id,
            "name": user.name if user else None,
            "email": user.email if user else None,
            "has_password": bool(user.password) if user else None,
        })
    return {"success": True, "data": out}


def load_jump_queue(app):
    app.register_blueprint(workshop_jump_api)
    start_drainer(app)
