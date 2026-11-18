"""Registration codes: the cohorts of an instance that runs without Jump.

A workshop needs to know which room an account sat in, and Jump used to say
(§50). With Jump paused, a supervisor says instead: they create a
**registration code** before the session, put it on a slide, and every account
made with it belongs to that code's cohort (PLAN.md §60).

A code is the only thing a participant gives. No name, no e-mail: the account
gets a generated name (names.py) and a synthetic address nothing is sent to.
What says who the account belongs to is a secret their own browser makes
(tokendoor.py, §61), and this module only ever sees the value derived from it.

**A code carries the cohort's facts**, typed once by the admin rather than by
thirty lycéens: campus, event, date, and the content of the day. All optional.
The code itself is the cohort key, so the wall between cohorts (audience.py)
and the room picker of the supervision pages (scope.py) work with no Jump at
all: this module registers a cohort provider like the other ways in (§59).

**A code is typed by hand from a slide**, so it is compared forgivingly:
case, spaces and dashes do not count, and the letters that look like digits
are folded (`O` to `0`, `I` and `L` to `1`). `LILLE-2026`, `lille 2026` and
`Lille2026` are one code, and so two codes that differ only that way cannot
both exist.

**A code can be closed three ways:** retired by hand, outside its validity
window, or full. A closed code admits nobody new and its accounts are
untouched. **A code somebody came in on is never deleted**, because that would
orphan them: the cohort is the code. One that admitted nobody is a typo, and
that one can go.

**Every refusal is the same answer, bar one.** Unknown, retired, over, full:
one message and one status, so the door cannot be used to learn which codes
exist. The reason goes to the log, for the admin who is asked "why does my
code not work".

The one exception is a code that is **not open yet**: the door says the event
has not started and when the code opens. A room that arrives ten minutes
early was otherwise told its code was wrong, and went looking for another
one. What it gives away is that a code exists before it can be used, and its
opening time; a code that must stay unknown until then is made later instead.

**The throttle counts wrong codes, not addresses.** A classroom is one
address, and thirty lycéens behind it are not a prober; and an address is the
one thing about a participant this door has no reason to read. Two counters,
both in the cache:

  * **per string**: a text that is not a code stops being looked up after ten
    tries in a quarter of an hour. That is the slide with a typo on it, read
    by a whole room. It protects the database and keeps that room from
    tripping the brake below;
  * **the brake**: 300 wrong codes in one minute, whatever they were, and the
    door refuses every code, right ones included, until the minute is over.
    That is somebody running a list.

What this does not stop is said in PLAN.md §60.4 rather than hidden: a slow
guesser, and a holder of a real code making accounts in a loop. The answers to
those are a code long enough not to be guessed, a cap, and a validity window.
"""
import hashlib
import re
import secrets
import time
from datetime import date, datetime, timezone

from flask import (Blueprint, abort, jsonify, redirect, render_template,
                   request, url_for)
from flask_babel import gettext as _
from sqlalchemy.exc import IntegrityError

from CTFd.cache import cache
from CTFd.models import Users, db
from CTFd.utils import get_config
from CTFd.utils.decorators import admins_only
from CTFd.utils.logging import log
from CTFd.utils.user import authed

from .accounts import ban_accounts
from .cohorts import CohortProvider
from .cohorts import register as register_cohorts
from .doors import sign_in, workshop_is_open
from .external import accounts_by_code
from .names import new_name

# Named here and in the migration alike, so a later revision can drop it by
# name (the same rule as jump.py's).
FK_CODE = "fk_workshop_token_account_code"

# What a code is compared by: 6 to 24 letters and digits once folded. Six is
# the floor because a shorter one is guessed by hand; 24 is the ceiling
# because the token a participant is given is 28 (tokendoor.py), and the door
# tells the two apart by their shape.
KEY_MIN, KEY_MAX = 6, 24

# Crockford's base32: no I, L, O or U, so nothing in it is misread from a
# slide. The codes this module proposes are drawn from it.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# The throttle (see the docstring).
STRING_LIMIT, STRING_WINDOW = 10, 900
BRAKE_LIMIT, BRAKE_WINDOW = 300, 60

# The value the browser derives from the token and sends: 32 bytes, as hex.
SIGNIN_RE = re.compile(r"[0-9a-f]{64}")

# The content slug of the last sync (tools/sync_subject.py writes it). Read
# here only to pre-fill the form.
CONFIG_CONTENT = "workshop_content"

TRIES = 8

workshop_codes = Blueprint("workshop_codes", __name__, template_folder="templates")


# ---------------------------------------------------------------------------
# The two tables
# ---------------------------------------------------------------------------

class RegistrationCode(db.Model):
    """One code, and so one cohort.

    `code` is what the admin typed and what goes on the slide; `code_key` is
    what it is compared by, and the unique one. The facts are free text on
    purpose: a campus list of this plugin's own would be a second place to
    keep in step with Jump's, for a label.

    The window is naive UTC, like every date CTFd stores.
    """
    __tablename__ = "workshop_registration_code"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), nullable=False)
    code_key = db.Column(db.String(32), nullable=False, unique=True)
    campus = db.Column(db.String(128))
    event = db.Column(db.String(128))
    event_date = db.Column(db.Date)
    content = db.Column(db.String(64))
    valid_from = db.Column(db.DateTime)
    valid_until = db.Column(db.DateTime)
    cap = db.Column(db.Integer)
    retired = db.Column(db.Boolean, nullable=False, default=False)
    created = db.Column(db.DateTime, default=datetime.utcnow)

    def room_label(self):
        """What the room picker calls this cohort: the event and its date, or
        the code when the admin gave neither. The code is no secret from the
        staff, who are the ones handing it out."""
        parts = [(self.event or "").strip(),
                 self.event_date.isoformat() if self.event_date else ""]
        return " · ".join(p for p in parts if p) or self.code

    def label(self):
        """The campus in front of that, for the admin page."""
        parts = [(self.campus or "").strip(), (self.event or "").strip(),
                 self.event_date.isoformat() if self.event_date else ""]
        return " · ".join(p for p in parts if p)


class TokenAccount(db.Model):
    """An account made at the token door: its cohort, and how it signs in.

    `signin_hash` is the SHA-256 of the value the browser derives from the
    token (§61). The token itself never reaches the server, and neither that
    value nor the token can be worked back from this column.

    Keyed on the user like `WorkshopStaff`: one row per account, gone with it.
    The code has no cascade: a code with accounts is never deleted, and this
    key is what refuses it.
    """
    __tablename__ = "workshop_token_account"

    user_id = db.Column(db.Integer,
                        db.ForeignKey("users.id", ondelete="CASCADE"),
                        primary_key=True)
    code_id = db.Column(db.Integer,
                        db.ForeignKey("workshop_registration_code.id",
                                      name=FK_CODE),
                        nullable=False, index=True)
    signin_hash = db.Column(db.String(64), nullable=False, unique=True)
    created = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# A code, as text
# ---------------------------------------------------------------------------

def code_key(text):
    """What a code is compared by. See the docstring."""
    key = re.sub(r"[^0-9A-Z]", "", str(text or "").upper())
    return key.replace("O", "0").replace("I", "1").replace("L", "1")


def new_code():
    """A code nobody will guess, for the form to propose: `K7QM-2XHD`.

    Forty bits. An admin may type their own instead (`LILLE-2026` is easier
    to say aloud), and the page tells them what that costs.
    """
    raw = "".join(secrets.choice(ALPHABET) for _i in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def signin_hash(signin):
    """The stored form of the value the browser sends."""
    return hashlib.sha256(bytes.fromhex(signin)).hexdigest()


# ---------------------------------------------------------------------------
# Open or not
# ---------------------------------------------------------------------------

def counts():
    """code id -> how many accounts it has admitted, banned ones included.

    Banned ones count toward a cap: the cap is how many accounts a code may
    make, and banning a cohort must not hand its places out again.
    """
    return dict(db.session.query(TokenAccount.code_id,
                                 db.func.count(TokenAccount.user_id))
                .group_by(TokenAccount.code_id).all())


def state_of(row, count, now=None):
    """`open`, or why not: `retired`, `early`, `expired`, `full`."""
    now = now or datetime.utcnow()
    if row.retired:
        return "retired"
    if row.valid_from is not None and now < row.valid_from:
        return "early"
    if row.valid_until is not None and now >= row.valid_until:
        return "expired"
    if row.cap is not None and count >= row.cap:
        return "full"
    return "open"


def _admitted(code_id):
    return TokenAccount.query.filter_by(code_id=code_id).count()


# ---------------------------------------------------------------------------
# The throttle
# ---------------------------------------------------------------------------

def _bump(key, window):
    """Add one to a counter that expires. `cache.inc` is not used: the cache
    pickles its values, and redis cannot increment a pickle (jump.py `_hit`)."""
    if cache.add(key, 1, timeout=window):
        return 1
    current = int(cache.get(key) or 0) + 1
    cache.set(key, current, timeout=window)
    return current


def _string_key(key):
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"ws:door:code:{digest}:{int(time.time() // STRING_WINDOW)}"


def _brake_key():
    return f"ws:door:wrong:{int(time.time() // BRAKE_WINDOW)}"


def _braked():
    return int(cache.get(_brake_key()) or 0) >= BRAKE_LIMIT


def resolve(text):
    """`(code, None)` for a code that admits people now, else `(code, why)`
    for a real code that is closed and `(None, why)` for everything else.

    The order is the throttle's: what costs nothing is refused first, and
    only a text that is no code at all is counted. A real code that is closed
    is not: a room typing the right code five minutes early must not lock
    the door on itself.
    """
    key = code_key(text)
    if not KEY_MIN <= len(key) <= KEY_MAX:
        return None, "format"
    if _braked():
        return None, "brake"
    counter = _string_key(key)
    if int(cache.get(counter) or 0) >= STRING_LIMIT:
        return None, "throttled"
    row = RegistrationCode.query.filter_by(code_key=key).first()
    if row is None:
        _bump(counter, STRING_WINDOW)
        _bump(_brake_key(), BRAKE_WINDOW)
        return None, "unknown"
    state = state_of(row, _admitted(row.id))
    return row, (None if state == "open" else state)


# ---------------------------------------------------------------------------
# Admitting somebody
# ---------------------------------------------------------------------------

def account_for(digest):
    """The account a sign-in value belongs to, or None."""
    link = TokenAccount.query.filter_by(signin_hash=digest).first()
    return Users.query.filter_by(id=link.user_id).first() if link else None


def admit(code_id, digest):
    """Create the account of a new participant: `(user, None)` or `(None, why)`.

    The account has **no password**, and `Users(...)` is never given one:
    passing `None` would hash the string "None" (jump.py `resolve_account`).
    Core's own login form refuses an account without one, so the token door
    is the only way into it.

    The e-mail is a random address under `.invalid`, a domain reserved for
    never existing (RFC 2606): CTFd wants the column filled and unique, and
    this says nothing about anybody and can never receive anything.

    The code's row is locked while its accounts are counted, so a cap of 30
    admits 30 when a room of 40 presses the button together.
    """
    if str(get_config("user_mode") or "users") != "users":
        return None, "teams-mode"
    limit = int(get_config("num_users", default=0) or 0)
    if limit and Users.query.filter_by(banned=False, hidden=False).count() >= limit:
        return None, "instance-full"

    for attempt in range(TRIES):
        # A new transaction before the lock, not only after a failure. Under
        # REPEATABLE READ the count below reads the snapshot taken by the
        # first plain SELECT of the transaction, and this request has made
        # several already: without this, a request that waited for the lock
        # would count the accounts as they were before it waited.
        db.session.rollback()
        row = (RegistrationCode.query.filter_by(id=code_id)
               .with_for_update().first())
        state = state_of(row, _admitted(row.id)) if row is not None else "unknown"
        if state != "open":
            db.session.rollback()
            return None, state
        # Longer numbers once a few names were taken: a busy instance gets
        # « Renard-Bleu-4821 » rather than a loop.
        name = new_name(wide=attempt >= 5)
        if Users.query.filter_by(name=name).first() is not None:
            continue
        user = Users(name=name, verified=True,
                     email=f"{secrets.token_hex(16)}@token.invalid")
        db.session.add(user)
        try:
            db.session.flush()
            db.session.add(TokenAccount(user_id=user.id, code_id=row.id,
                                        signin_hash=digest))
            db.session.commit()
        except IntegrityError:
            # Two requests with one sign-in value (a double click), or two
            # accounts drawing one name. The first is answered with the
            # account that won; the second draws again.
            db.session.rollback()
            existing = account_for(digest)
            if existing is not None:
                return existing, None
            continue
        return user, None
    return None, "no-name"


# ---------------------------------------------------------------------------
# The two requests the door makes
# ---------------------------------------------------------------------------

def refusal(reason, code=None):
    """The one answer to everything that is not a yes, bar a code that is
    not open yet (see the module's docstring).

    The reason is logged and, that one case aside, never sent. The log line
    carries the code only when it is a real one: what was typed into the box
    of a page with no name field is the one free text a lycéen can put a name
    in, and a log is not where it should end up. No address either: `{ip}` is
    simply not in the format, which is all core's `log` needs to leave it out.

    For a code that is not open yet the body also carries `opens`, the UTC
    instant: the door writes it in the reader's own time zone, which only
    their browser knows. The message here is what a client that ignores
    `opens` shows.
    """
    if code is not None:
        log("registrations",
            "[{date}] token door: code {code} refused ({reason})",
            code=code.code, reason=reason)
    else:
        log("registrations", "[{date}] token door: refused ({reason})",
            reason=reason)
    if reason == "early" and code is not None and code.valid_from is not None:
        return jsonify({
            "success": False,
            "message": _("This event has not started yet. Come back when "
                         "it starts."),
            "opens": code.valid_from.isoformat(timespec="seconds") + "Z",
        }), 403
    return jsonify({
        "success": False,
        "message": _("This code is not valid. Check it or ask your "
                     "supervisor for the right one."),
    }), 403


def text_field(payload, field):
    value = payload.get(field) if isinstance(payload, dict) else None
    return value if isinstance(value, str) else ""


def after_entry():
    """Where a participant goes once signed in."""
    if workshop_is_open():
        return url_for("workshop_page.workshop")
    return url_for("workshop_codes.waiting")


@workshop_codes.route("/api/v1/workshop/door/code", methods=["POST"])
def check_code():
    """Is this a code that admits people now? Creates nothing.

    The door asks before it shows a participant their token, so nobody saves
    a token for an account that could never be made. The account is made by
    the next request, once they have said they saved it.
    """
    row, reason = resolve(text_field(request.get_json(silent=True), "code"))
    if reason is not None:
        return refusal(reason, code=row)
    return jsonify({"success": True})


@workshop_codes.route("/api/v1/workshop/door/register", methods=["POST"])
def register_account():
    """Make the account and sign this browser in.

    `signin` is looked up before the code is: the same value sent twice is
    the same participant pressing twice, and the second press must answer
    like the first even if theirs was the last place under the cap.
    """
    payload = request.get_json(silent=True)
    signin = text_field(payload, "signin")
    if not SIGNIN_RE.fullmatch(signin):
        return refusal("format")
    digest = signin_hash(signin)

    user = account_for(digest)
    if user is None:
        row, reason = resolve(text_field(payload, "code"))
        if reason is not None:
            return refusal(reason, code=row)
        user, reason = admit(row.id, digest)
        if user is None:
            return refusal(reason, code=row)
        log("registrations",
            "[{date}] token door: {name} registered on code {code}",
            name=user.name, code=row.code)
    if user.banned:
        return refusal("banned")
    sign_in(user)
    return jsonify({"success": True,
                    "data": {"next": after_entry(), "name": user.name}})


@workshop_codes.route("/door/waiting")
def waiting():
    """Signed in, and the workshop is not open: say so, rather than send a
    lycéen to core's "CTF has not started" page. The same page Jump's
    arrivals get (jump.py `_notice`)."""
    if not authed():
        return redirect(url_for("auth.login"))
    if workshop_is_open():
        return redirect(url_for("workshop_page.workshop"))
    return render_template(
        "workshop_jump_notice.html",
        notice_title=_("The workshop is not open yet"),
        notice_message=_("You are signed in. Come back when your instructor "
                         "opens it."))


# ---------------------------------------------------------------------------
# The admin page
# ---------------------------------------------------------------------------

def _instant(form, field):
    """A date and time of the form, as naive UTC: `(value, ok)`.

    The page sends two fields for one instant. `<field>` is filled by its
    script with the UTC instant, which is the browser's reading of what the
    admin typed in their own time zone; `<field>_local` is the box itself,
    read as UTC when the script did not run. A server cannot know which zone
    a `datetime-local` was typed in, and guessing Paris would be right until
    the first instance run from somewhere else.
    """
    for raw in (form.get(field), form.get(field + "_local")):
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None, False
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value, True
    return None, True


def _day(form, field, instant):
    """The calendar day of an instant of the form, as the admin sees it.

    The box is read first: it is the day they typed, in their own zone. The
    UTC instant is the fallback, right to within a day, for a form posted by
    something that is not the page.
    """
    raw = (form.get(field + "_local") or "").strip()
    try:
        return datetime.fromisoformat(raw).date() if raw else instant.date()
    except ValueError:
        return instant.date()


def _create(form):
    """Create a code from the form. Returns `(code, errors)`."""
    errors = []
    shown = " ".join(str(form.get("code") or "").split())[:64]
    key = code_key(shown)
    if not KEY_MIN <= len(key) <= KEY_MAX:
        errors.append(f"A code is {KEY_MIN} to {KEY_MAX} letters and digits. "
                      "Spaces and dashes are allowed and do not count.")
    elif RegistrationCode.query.filter_by(code_key=key).first() is not None:
        errors.append("That code exists already. Case, spaces and dashes do "
                      "not count, and neither does O for 0 or I and L for 1.")

    event_date = None
    raw = (form.get("event_date") or "").strip()
    if raw:
        try:
            event_date = date.fromisoformat(raw)
        except ValueError:
            errors.append("The event date is not a date.")

    valid_from, ok_from = _instant(form, "valid_from")
    valid_until, ok_until = _instant(form, "valid_until")
    if not (ok_from and ok_until):
        errors.append("The validity window is not a date and a time.")
    elif valid_from and valid_until and valid_until <= valid_from:
        errors.append("The code would close before it opens.")
    elif event_date is not None:
        # The date is a label, and a label that contradicts the window is a
        # typo in one of the two: either the room finds the door closed on
        # the day written on the cohort, or the cohort is filed under a day
        # nobody could come in. A side with no bound is not checked.
        if valid_from and event_date < _day(form, "valid_from", valid_from):
            errors.append("The event date is before the day the code opens. "
                          "Change the date or the validity window.")
        elif valid_until and event_date > _day(form, "valid_until", valid_until):
            errors.append("The event date is after the day the code closes. "
                          "Change the date or the validity window.")

    cap = None
    raw = (form.get("cap") or "").strip()
    if raw:
        if raw.isdigit() and int(raw) > 0:
            cap = int(raw)
        else:
            errors.append("The cap is a number of accounts, 1 or more.")

    if errors:
        return None, errors
    row = RegistrationCode(
        code=shown, code_key=key,
        campus=(form.get("campus") or "").strip()[:128] or None,
        event=(form.get("event") or "").strip()[:128] or None,
        event_date=event_date,
        content=(form.get("content") or "").strip()[:64] or None,
        valid_from=valid_from, valid_until=valid_until, cap=cap)
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None, ["That code exists already."]
    # A room may have been typing it before it existed: the slide went up
    # first. Their tries must not have closed it on them.
    cache.delete(_string_key(key))
    return row, []


def _accounts(code_id):
    return (Users.query.join(TokenAccount, TokenAccount.user_id == Users.id)
            .filter(TokenAccount.code_id == code_id)
            .order_by(Users.id).all())


def _page(form=None, errors=None, status=200):
    rows = RegistrationCode.query.order_by(RegistrationCode.created.desc(),
                                           RegistrationCode.id.desc()).all()
    cohorts = {}
    for user, code_id in (db.session.query(Users, TokenAccount.code_id)
                          .join(TokenAccount, TokenAccount.user_id == Users.id)
                          .order_by(Users.id).all()):
        cohorts.setdefault(code_id, []).append(user)
    now = datetime.utcnow()
    content = get_config(CONFIG_CONTENT)
    return render_template(
        "workshop_codes.html",
        codes=rows, cohorts=cohorts, external=accounts_by_code(),
        states={row.id: state_of(row, len(cohorts.get(row.id, [])), now)
                for row in rows},
        form=form or {"code": new_code(),
                      "content": str(content) if content not in (None, "") else ""},
        errors=errors or [],
        notice=request.args.get("notice"),
        subject=request.args.get("subject", "")), status


@workshop_codes.route("/admin/workshop/codes", methods=["GET", "POST"])
@admins_only
def manage():
    """`admins_only`: who may come in is not a supervisor's call.

    The cohorts of the retired external door are on this page too (§64), for
    the one thing still done to them: shutting one out, or letting it back.
    They are named by their code, a text, where a registration code is named
    by its id.
    """
    if request.method != "POST":
        return _page()

    action = request.form.get("action")
    if action == "create":
        row, errors = _create(request.form)
        if row is None:
            # Drawn again with what was typed, not redirected: nine fields
            # are too many to type twice for one of them.
            return _page(form=request.form, errors=errors, status=400)
        return redirect(url_for("workshop_codes.manage", notice="created",
                                subject=row.code))
    if action in ("ban-external", "unban-external"):
        code = request.form.get("code", "")
        users = accounts_by_code().get(code)
        if not users:
            abort(404)
        ban_accounts(users, action == "ban-external")
        return redirect(url_for(
            "workshop_codes.manage", subject=code,
            notice="banned-external" if action == "ban-external"
            else "unbanned"))

    row = RegistrationCode.query.filter_by(
        id=request.form.get("id", type=int)).first()
    if row is None:
        abort(404)
    if action in ("retire", "reopen"):
        row.retired = action == "retire"
        db.session.commit()
        notice = "retired" if row.retired else "reopened"
    elif action == "ban":
        # Banning retires the code too. The accounts have nothing but a
        # token, so a banned lycéen with the code still on the slide would
        # be back with a new account before the page had reloaded.
        row.retired = True
        db.session.commit()
        ban_accounts(_accounts(row.id), True)
        notice = "banned"
    elif action == "unban":
        ban_accounts(_accounts(row.id), False)
        notice = "unbanned"
    elif action == "delete":
        # Only a code nobody came in on: a typo, or a code made for a session
        # that never happened. The foreign key says no for the registration
        # that slipped in between the count and the delete.
        if _admitted(row.id):
            abort(400)
        subject = row.code
        db.session.delete(row)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            abort(400)
        return redirect(url_for("workshop_codes.manage", notice="deleted",
                                subject=subject))
    else:
        abort(400)
    return redirect(url_for("workshop_codes.manage", notice=notice,
                            subject=row.code))


# ---------------------------------------------------------------------------
# The cohorts, for the wall and the room picker
# ---------------------------------------------------------------------------

class CodeCohorts(CohortProvider):
    """A room is a code, a campus is what its code says (§59, §60).

    The campus is free text, so two codes are in the same campus when they
    say so the same way give or take case and outer spaces. Its ref starts
    with `@`, which no Jump key id can (cohorts.py): a campus this door calls
    `1` and Jump's campus `1` stay two campuses.

    Only codes somebody came in on are listed, like Jump's sessions: a code
    made for tomorrow is not a room yet.
    """
    name = "cohort"

    def memberships(self):
        return dict(db.session.query(TokenAccount.user_id,
                                     TokenAccount.code_id).all())

    @staticmethod
    def _ref(row):
        return "@" + (row.campus or "").strip().casefold()

    @staticmethod
    def _used():
        """The codes with at least one account, newest first."""
        return (RegistrationCode.query
                .filter(RegistrationCode.id.in_(
                    db.session.query(TokenAccount.code_id)))
                .order_by(RegistrationCode.created.desc(),
                          RegistrationCode.id.desc()).all())

    def groups(self):
        campuses = {}
        for row in self._used():
            campuses.setdefault(self._ref(row),
                                (row.campus or "").strip() or "No campus")
        return list(campuses.items())

    def rooms(self, ref):
        return [(row.id, row.room_label()) for row in self._used()
                if self._ref(row) == ref]

    def members(self, ref, room_id=None):
        ids = [row.id for row in RegistrationCode.query.all()
               if self._ref(row) == ref
               and (room_id is None or row.id == room_id)]
        return db.session.query(TokenAccount.user_id).filter(
            TokenAccount.code_id.in_(ids))


def load_codes(app):
    app.register_blueprint(workshop_codes)
    register_cohorts(CodeCohorts())
