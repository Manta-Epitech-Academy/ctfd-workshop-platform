"""The way in from Jump: a signed ticket, an account, a session (PLAN.md §31).

Jump (the Epitech Academy talent platform) is the only door into a workshop
instance. A talent clicks the activity on their Jump dashboard; Jump mints a
short signed ticket and opens `<baseUrl>/jump/enter?t=...` in a new tab; this
module verifies it, creates the account on first arrival, opens the CTFd
session and drops the talent on the workshop. They never see a login form and
never hold a second password.

**This route deliberately bypasses the instance's registration path.** That is
the design, not an oversight: an instructor-led instance is registration-gated
or shut outright, and the whole point is that arriving through Jump is the way
in. Everything that gate would have decided is decided here instead — the user
cap, the team mode, the ban flag — and refused in French rather than in a
stack trace.

## The ticket, and why it is not a JWT

    b64url(json(claims)) + "." + b64url(hmac_sha256(ticketKey, part1))

    claims = {kid, sub: talentId, name: displayName, aud: "workshop:<slug>",
              iss: "jump", iat, exp, jti}      exp = iat + 120

There is no JWT library in the image and one cannot be added: the Dockerfile's
plugin-requirements loop runs at build time over the `./CTFd` context, while
this plugin arrives at run time as a bind mount, so a
`plugins/workshop/requirements.txt` is never read. Verified:
`docker exec <ctfd> python -c "import jwt"` raises `ModuleNotFoundError`.

A fixed-algorithm token verified with stdlib `hmac` is the consequence, and it
is a better token here: with no `alg` header, the whole "alg: none" /
"RS256 verified as HS256" class does not exist to be defended against.

## The key id is what makes one instance serve two Jumps

`workshop_jump_keys` is a map `{kid: {origin, secret, label}}` from day one.
The `kid` in the ticket selects the verification key, and **the callback origin
comes from that key's configuration, never from the token** — so nobody can
influence where this instance reports progress. `label` namespaces the
synthetic email, which is what keeps a dev talent off a production scoreboard
when one instance serves `epiboost.eu` and `epiboost.fr` at once.

## Two derived keys, one shared secret

    ticketKey   = hmac_sha256(secret, "jump/ticket").hexdigest()
    callbackKey = hmac_sha256(secret, "jump/callback").hexdigest()

Separate so that compromising one direction is not a forging capability in the
other. The derived key is the lowercase **hex digest as an ASCII string**,
which is node's `createHmac('sha256', secret).update(label).digest('hex')` —
that string is then the key of the next HMAC. This is half of a contract frozen
with the Jump side; changing it means changing both repositories.

## The accounts hold no personal data of a minor

The email is synthesised, `f"{talentId}@{label}.jump.invalid"`. `.invalid` is
reserved by RFC 2606, matches CTFd's own `EMAIL_REGEX` and can never resolve,
so these instances — third-party hosts, public scoreboards, under-18 audience —
store no address anybody can reach. `Users.name` is not unique in CTFd, so the
display name needs no disambiguation; the email carries the uniqueness.

`password=None` is load-bearing rather than incidental: `CTFd/auth.py:476-482`
refuses local sign-in to such an account, so "everybody comes through Jump" is
enforced by the data instead of by a note in a runbook.
"""
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
from sqlalchemy.exc import IntegrityError

from CTFd.cache import cache
from CTFd.models import Users, db
from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only
from CTFd.utils.logging import log
from CTFd.utils.security.auth import login_user
from CTFd.utils.user import get_ip

CONFIG_KEYS = "workshop_jump_keys"
CONFIG_INSTANCE = "workshop_jump_instance"

ISSUER = "jump"
AUDIENCE_PREFIX = "workshop:"
EMAIL_DOMAIN = "jump.invalid"

# Server-side ceiling on a ticket's life, applied whatever the token claims.
# Without it a bug on the Jump side could hand out a bearer valid for a month
# that this instance has no way to revoke.
MAX_LIFETIME = 120
# A ticket minted on a clock slightly ahead of ours is still fine; one minted
# well into the future is not.
MAX_CLOCK_SKEW = 30
# How long a spent `jti` is remembered. Its whole job is to outlive the ticket
# that carried it, so it is the remaining lifetime plus a margin.
JTI_MARGIN = 60

# Flood brake, ahead of any verification, on the IP. Deliberately generous: a
# class of thirty behind one school NAT is one IP, and this is not the real
# limit — see `_LIMIT_SUB`.
_LIMIT_IP = (240, 60)
# The real limit, on the talent the ticket names, applied after the signature
# is known good. CTFd's own @ratelimit cannot express this: its
# `get_ratelimit_subject` only extracts an identity for the three auth
# endpoints and falls back to the IP everywhere else, which would give that
# whole class a single shared bucket.
_LIMIT_SUB = (30, 60)

KID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
LABEL_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")
ORIGIN_RE = re.compile(r"\Ahttps?://[A-Za-z0-9.-]+(:\d+)?\Z")


class JumpLink(db.Model):
    """One row per (Jump environment, talent): which CTFd account is theirs.

    A table of our own rather than CTFd's `Fields` / `FieldEntries`, so no
    talent id leaks into the admin user form or a public profile.

    It is the identity, and the email is not. Resolution looks here first,
    which is what will let the email scheme change later without orphaning a
    single account.
    """
    __tablename__ = "workshop_jump_link"
    __table_args__ = (db.UniqueConstraint("jump_kid", "jump_talent_id"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True)
    jump_kid = db.Column(db.String(64), nullable=False, index=True)
    jump_talent_id = db.Column(db.String(64), nullable=False)
    created = db.Column(db.DateTime, default=datetime.utcnow)


class JumpEvent(db.Model):
    """The outbox: one row per solved step, waiting to reach Jump.

    Written inside the solve request and sent from a background drainer, so a
    slow or absent Jump costs a participant nothing (see jumpqueue.py, and
    PLAN.md §31 for why a synchronous POST is an availability risk rather than
    merely a slow one).

    `payload` is filled at send time, not at enqueue time: the counters are
    only correct once CTFd has invalidated its solve cache, which happens
    after the hook returns.

    Statuses:
      pending  waiting for its turn, `next_attempt` says when
      sent     Jump acknowledged it
      failed   out of attempts; it shows on /admin/workshop/jump with a button
      dropped  its `kid` is no longer configured, so there is nowhere to send it
    """
    __tablename__ = "workshop_jump_event"
    __table_args__ = (db.UniqueConstraint("user_id", "challenge_id"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True)
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"),
        nullable=False)
    jump_kid = db.Column(db.String(64), nullable=False)
    jump_talent_id = db.Column(db.String(64), nullable=False)
    payload = db.Column(db.JSON)
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    next_attempt = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    last_error = db.Column(db.Text)
    created = db.Column(db.DateTime, default=datetime.utcnow)
    sent = db.Column(db.DateTime)


class TicketError(Exception):
    """A ticket this instance will not act on, with the reason for the log.

    The reason never reaches the talent: every refusal renders the same page,
    because telling an unauthenticated caller *which* check failed is how a
    token gets ground down one field at a time.
    """


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def jump_keys():
    """The allowlist, `{kid: {origin, secret, label}}`, or empty.

    Empty means this instance refuses every ticket. Failing closed is the only
    safe default: a misconfigured instance must not be a door.
    """
    raw = get_config(CONFIG_KEYS)
    if not raw:
        return {}
    try:
        keys = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(keys, dict):
        return {}
    return {k: v for k, v in keys.items() if isinstance(v, dict)
            and v.get("origin") and v.get("secret") and v.get("label")}


def set_jump_keys(keys):
    set_config(CONFIG_KEYS, json.dumps(keys, sort_keys=True))


def instance_slug():
    """This instance's slug, as the ticket's audience names it.

    `str()` is not decoration: `get_config` turns an all-digit value into an
    `int` (CTFd/utils/__init__.py:44-46), so a slug like `2026` would compare
    unequal to its own string and this instance would silently stop accepting
    every ticket.
    """
    raw = get_config(CONFIG_INSTANCE)
    return str(raw).strip() if raw not in (None, "") else ""


def jump_enabled():
    """Is there a configured way in? Read by login.html."""
    return bool(jump_keys()) and bool(instance_slug())


def derived_key(secret, purpose):
    """`ticketKey` or `callbackKey` — the hex digest, as an ASCII string.

    Mirrors node's `createHmac('sha256', secret).update(purpose).digest('hex')`
    on the Jump side. Frozen contract; see the module docstring.
    """
    return hmac.new(secret.encode(), purpose.encode(),
                    hashlib.sha256).hexdigest().encode()


# --------------------------------------------------------------------------
# The ticket
# --------------------------------------------------------------------------

def _b64url_decode(part):
    """Decode a b64url segment, padded or not.

    Jump sends it unpadded, which is the usual convention; accepting padding
    costs one line and removes a whole class of "works on my environment".
    """
    part = part.encode() if isinstance(part, str) else part
    return base64.urlsafe_b64decode(part + b"=" * (-len(part) % 4))


def verify_ticket(token, now=None):
    """`(claims, key)` for a ticket this instance accepts, else `TicketError`.

    The order of the checks is the design. The `kid` is resolved **before any
    cryptographic decision**, and there is deliberately no "well, there is only
    one secret configured" fallback: that would turn a single-origin instance
    into a verification oracle the day a second origin is added.
    """
    now = now if now is not None else time.time()
    if not token or len(token) > 4096:
        raise TicketError("missing or oversized token")
    parts = token.split(".")
    if len(parts) != 2:
        raise TicketError("malformed token")
    head, signature = parts

    try:
        claims = json.loads(_b64url_decode(head))
    except (ValueError, TypeError, base64.binascii.Error):
        raise TicketError("undecodable claims")
    if not isinstance(claims, dict):
        raise TicketError("claims are not an object")

    kid = claims.get("kid")
    key = jump_keys().get(kid) if isinstance(kid, str) else None
    if key is None:
        raise TicketError(f"unknown kid {kid!r}")

    try:
        given = _b64url_decode(signature)
    except (ValueError, TypeError, base64.binascii.Error):
        raise TicketError("undecodable signature")
    expected = hmac.new(derived_key(key["secret"], "jump/ticket"),
                        head.encode(), hashlib.sha256).digest()
    # compare_digest and never ==, so the comparison does not leak the prefix
    # length of a correct signature through its own timing.
    if not hmac.compare_digest(given, expected):
        raise TicketError("bad signature")

    slug = instance_slug()
    if not slug:
        raise TicketError("this instance has no slug configured")
    if claims.get("aud") != AUDIENCE_PREFIX + slug:
        raise TicketError(f"audience {claims.get('aud')!r} is not this instance")
    if claims.get("iss") != ISSUER:
        raise TicketError(f"issuer {claims.get('iss')!r} is not {ISSUER}")

    iat, exp = claims.get("iat"), claims.get("exp")
    if not isinstance(iat, (int, float)) or not isinstance(exp, (int, float)):
        raise TicketError("iat or exp missing")
    if exp <= now:
        raise TicketError("expired")
    if iat > now + MAX_CLOCK_SKEW:
        raise TicketError("issued in the future")
    # The claimed lifetime is checked against our own ceiling, not merely
    # against itself: a token claiming a month is refused even while it is
    # inside its own window.
    if exp - iat > MAX_LIFETIME:
        raise TicketError(f"claimed lifetime {exp - iat}s over the {MAX_LIFETIME}s cap")

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 64:
        raise TicketError("no usable sub")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti or len(jti) > 128:
        raise TicketError("no usable jti")

    return claims, key


def burn_jti(jti, exp, now=None):
    """Spend a ticket, returning False if it was already spent.

    `cache.add` is SETNX, so it is atomic, and atomicity is the whole point:
    two requests arriving at the same instant with the same `jti` must produce
    exactly one session. **Do not replace this with the get-then-set in
    CTFd/utils/decorators/__init__.py:209-217** — that one has a window
    between the read and the write, and single-use is the property this route
    exists to guarantee.
    """
    now = now if now is not None else time.time()
    timeout = max(int(exp - now), 0) + JTI_MARGIN
    return bool(cache.add(f"ws:jump:jti:{jti}", 1, timeout=timeout))


def _hit(key, limit, interval):
    """Count one hit and say whether it is still under `limit`."""
    if cache.add(key, 1, timeout=interval):
        return True
    try:
        current = int(cache.inc(key))
    except (NotImplementedError, AttributeError, TypeError, ValueError):
        # A non-redis cache (a dev instance on SimpleCache). Not atomic, but
        # this is a flood brake and not an authorisation decision.
        current = int(cache.get(key) or 0) + 1
        cache.set(key, current, timeout=interval)
    return current <= limit


# --------------------------------------------------------------------------
# The account
# --------------------------------------------------------------------------

def synthetic_email(talent_id, label):
    return f"{talent_id}@{label}.{EMAIL_DOMAIN}"


def resolve_account(claims, key):
    """The CTFd account this ticket names, creating it on first arrival.

    In this order, because the link row and not the email is the identity:

      1. the link row on `(kid, talentId)` — found, done;
      2. otherwise the synthetic email;
      3. found by email, no link row, and a password set: **refuse**. Barely
         reachable behind `.invalid`, but the absence of this branch is an
         account-takeover primitive and the branch is three lines;
      4. not found: create with no password, link it, commit.
    """
    kid = claims["kid"]
    talent_id = claims["sub"]
    email = synthetic_email(talent_id, key["label"])

    link = JumpLink.query.filter_by(jump_kid=kid, jump_talent_id=talent_id).first()
    if link:
        user = Users.query.filter_by(id=link.user_id).first()
        if user is None:
            # The account was deleted under the link (CASCADE should have taken
            # the row with it, so this is a repair path rather than a workflow).
            db.session.delete(link)
            db.session.commit()
        else:
            return user

    user = Users.query.filter_by(email=email).first()
    if user is not None and user.password is not None:
        raise TicketError(f"{email} exists with a password set")

    if user is None:
        limit = int(get_config("num_users", default=0) or 0)
        if limit and Users.query.filter_by(banned=False, hidden=False).count() >= limit:
            raise TicketError(f"instance is at its {limit}-user cap")
        display = (claims.get("name") or talent_id)
        user = Users(name=str(display)[:128], email=email,
                     password=None, verified=True)
        db.session.add(user)
        db.session.commit()

    db.session.add(JumpLink(user_id=user.id, jump_kid=kid, jump_talent_id=talent_id))
    try:
        db.session.commit()
    except IntegrityError:
        # Two first arrivals racing each other. One of them wrote the row; the
        # loser re-reads it and carries on with the same account. workspace.py
        # already handles its own first-write race in this shape.
        db.session.rollback()
        link = JumpLink.query.filter_by(jump_kid=kid, jump_talent_id=talent_id).first()
        if link is None:
            raise TicketError("could not link the account")
        user = Users.query.filter_by(id=link.user_id).first()
    return user


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------

workshop_jump = Blueprint("workshop_jump", __name__, template_folder="templates")


def _notice(title, message, status=200):
    """A whole page saying one thing, in the talent's own language.

    Not `info_for` plus a redirect to `/`: `get_infos()` filters flashes by
    `request.endpoint` and `views.static_html` never calls it, so a flash aimed
    at the front page renders nowhere at all. A page that says the thing cannot
    fail that way, and a full-page explanation is the better answer anyway for
    somebody who has just been bounced out of an activity.
    """
    return render_template("workshop_jump_notice.html",
                           notice_title=title, notice_message=message), status


def _refused(reason, status=403):
    """One page for every refusal, and the reason only in the log.

    Naming the failing check to an unauthenticated caller is how a token gets
    ground down a field at a time, and the talent could do nothing with the
    answer anyway: what they need is the one action that works, which is to
    start again from Jump.
    """
    log("logins", "[{date}] {ip} - jump ticket refused: " + reason.replace("{", "{{").replace("}", "}}"))
    return _notice(
        _("This link is no longer valid"),
        _("Go back to Jump and click the activity again to get a new one."),
        status=status)


@workshop_jump.route("/jump/enter")
def enter():
    if not _hit(f"ws:jump:ip:{get_ip()}", *_LIMIT_IP):
        return _refused("per-IP flood brake", status=429)

    try:
        claims, key = verify_ticket(request.args.get("t", ""))
    except TicketError as exc:
        return _refused(str(exc))

    if not _hit(f"ws:jump:sub:{claims['kid']}:{claims['sub']}", *_LIMIT_SUB):
        return _refused("per-talent rate limit", status=429)

    # Single use. After this point the ticket is spent whatever happens next,
    # which is the correct direction to fail in.
    if not burn_jti(claims["jti"], claims["exp"]):
        return _refused("jti already spent")

    if str(get_config("user_mode") or "users") != "users":
        # Teams mode needs a team before a solve counts, and nothing in the
        # ticket says which one. Refusing is honest; logging the talent into a
        # teamless account would look like it worked.
        return _refused("this instance runs in teams mode")

    try:
        user = resolve_account(claims, key)
    except TicketError as exc:
        return _refused(str(exc))

    if user.banned:
        # CTFd's own `banned()` guard only fires on the *next* request, so
        # without this the talent is logged in and then meets a 403 they have
        # no way to interpret.
        return _refused(f"user {user.id} is banned")

    # The local sign-in path (CTFd/auth.py:485-487), not the OAuth one at :663,
    # which skips the regeneration and leaves the pre-login session id valid.
    session.regenerate()
    login_user(user)
    log("logins", "[{date}] {ip} - {name} entered from Jump", name=user.name)

    if not _workshop_is_open():
        # Signed in, but with nowhere to go yet. Saying so beats redirecting
        # into the 403 that `/workshop` would raise.
        return _notice(
            _("The workshop is not open yet"),
            _("You are signed in. Come back when your instructor opens it."))
    return redirect(url_for("workshop_page.workshop"))


def _workshop_is_open():
    """Can this account actually open `/workshop` right now?

    Two instance states make it a raw error rather than a page:
    `challenge_visibility == "admins"`, and a CTF window that is closed or has
    not opened yet. Both are the operator's doing and both are temporary, so
    the talent gets a sentence in their own language instead of a 403 they
    cannot act on.
    """
    from CTFd.utils.dates import ctftime

    if str(get_config("challenge_visibility") or "private") == "admins":
        return False
    return bool(ctftime())


# --------------------------------------------------------------------------
# The admin page
# --------------------------------------------------------------------------

def _linked_kids():
    """Key ids an account is already bound to — their label cannot move."""
    return {row.jump_kid for row in
            db.session.query(JumpLink.jump_kid).distinct().all()}


def _parse_rows(form, stored):
    """The submitted key rows, or `(None, errors)`.

    Row `i` is `kid-i` / `origin-i` / `label-i` / `secret-i` / `remove-i`. A
    blank secret on an existing kid keeps the stored one, so an admin can fix a
    typo in an origin without being handed the secret back through the DOM.
    """
    keys, errors, labels = {}, [], {}
    linked = _linked_kids()
    for i in range(int(form.get("row_count") or 0)):
        kid = (form.get(f"kid-{i}") or "").strip()
        if not kid or form.get(f"remove-{i}"):
            continue
        origin = (form.get(f"origin-{i}") or "").strip().rstrip("/")
        label = (form.get(f"label-{i}") or "").strip().lower()
        secret = (form.get(f"secret-{i}") or "").strip()
        if not KID_RE.match(kid):
            errors.append(f"{kid!r} is not a usable key id (letters, digits, - and _)")
            continue
        if not ORIGIN_RE.match(origin):
            errors.append(f"{kid}: {origin!r} is not an origin "
                          f"(scheme and host only, no path)")
            continue
        if not LABEL_RE.match(label):
            errors.append(f"{kid}: {label!r} is not a usable label "
                          f"(lowercase letters, digits and -)")
            continue
        if label in labels:
            # The label namespaces every synthetic email, so two keys sharing
            # one would merge two Jump environments into one set of accounts.
            errors.append(f"{kid}: label {label!r} is already used by {labels[label]}")
            continue
        was = stored.get(kid)
        if was and kid in linked and was.get("label") != label:
            errors.append(f"{kid}: accounts are already bound to label "
                          f"{was['label']!r}; renaming it would orphan them")
            continue
        if not secret:
            if not was:
                errors.append(f"{kid}: a new key needs its shared secret")
                continue
            secret = was["secret"]
        labels[label] = kid
        keys[kid] = {"origin": origin, "secret": secret, "label": label}
    return (None, errors) if errors else (keys, [])


@workshop_jump.route("/admin/workshop/jump", methods=["GET", "POST"])
@admins_only
def settings():
    stored = jump_keys()
    errors, saved = [], False

    if request.method == "POST":
        if request.form.get("resend"):
            event = JumpEvent.query.filter_by(id=request.form["resend"]).first()
            if event is not None:
                requeue(event)
                db.session.commit()
            return redirect(url_for("workshop_jump.settings"))
        keys, errors = _parse_rows(request.form, stored)
        slug = (request.form.get("instance") or "").strip()
        if keys is not None:
            set_jump_keys(keys)
            set_config(CONFIG_INSTANCE, slug)
            stored, saved = keys, True

    events = (JumpEvent.query.filter(JumpEvent.status != "sent")
              .order_by(JumpEvent.next_attempt).limit(200).all())
    names = {u.id: u.name for u in Users.query.filter(
        Users.id.in_([e.user_id for e in events] or [0])).all()}
    return render_template(
        "workshop_jump.html",
        keys=stored, instance=instance_slug(), linked=_linked_kids(),
        errors=errors, saved=saved, events=events, names=names,
        pending=JumpEvent.query.filter_by(status="pending").count(),
        failed=JumpEvent.query.filter_by(status="failed").count(),
    )


def requeue(event):
    """Put a row back at the front of the queue. Caller commits."""
    event.status = "pending"
    event.attempts = 0
    event.last_error = None
    event.next_attempt = datetime.utcnow()


def load_jump(app):
    app.register_blueprint(workshop_jump)
    # login.html asks whether there is a door before drawing one.
    app.jinja_env.globals["jump_enabled"] = jump_enabled
    app.jinja_env.globals["jump_keys"] = jump_keys
