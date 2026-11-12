"""The token door: one box, a code in, and a secret only the browser knows.

A participant who arrives without Jump types the code on the slide
(codes.py, PLAN.md §60) and is given a **token**: 128 random bits, written as
28 letters and digits. The token is the account. There is no name, no e-mail
and no password, so there is nothing else to sign in with, and nobody can
give a lost one back (§61).

**The server never sees the token.** The browser makes it
(assets/door.js, with the arithmetic in assets/token.js) and derives two
values from it with HKDF:

  * a **sign-in value**, which it sends. This module stores only its SHA-256
    (`TokenAccount.signin_hash`), so a copy of the database signs nobody in;
  * an **encryption key**, which never leaves the browser. Saved code is
    encrypted with it (workspace.py, PLAN.md §68).

So this module is small. Making the account is codes.py's
(`/api/v1/workshop/door/register`), because an account is made by a code.
What is here is coming back, the door's place on the login page, and the one
thing an account with no name must not be able to do: type one.

**The one box takes both.** A code from the slide and a token look different
(a token is 28 characters and carries a checksum), and it is the browser that
tells them apart, not the server: a token typed with a mistake is refused on
the page and never sent anywhere. See door.js.

**Coming back is not throttled.** The sign-in value is 256 bits derived from
128 random ones. There is no list to run against it, so a counter would only
be something else to get wrong.

**Every refusal is codes.py's one answer**, for the same reason as there: the
door says "no" and never "why".
"""
from flask import Blueprint, g, jsonify, redirect, request, session
from flask_babel import lazy_gettext as _l

from CTFd.utils.logging import log

from .codes import (SIGNIN_RE, TokenAccount, account_for, after_entry,
                    refusal, signin_hash, text_field)
from .doors import Door, sign_in
from .doors import register as register_door

workshop_tokendoor = Blueprint("workshop_tokendoor", __name__,
                               template_folder="templates")

# Core's settings page and the request it saves with (CTFd/views.py
# `settings`, CTFd/api/v1/users.py `UserPrivate`). Named here because they
# are the only two core endpoints this module reaches for: after a CTFd
# upgrade, these are the names to check.
SETTINGS_PAGE = "views.settings"
SETTINGS_API = "api.users_user_private"


@workshop_tokendoor.route("/api/v1/workshop/door/signin", methods=["POST"])
def signin():
    """Sign this browser in as the account a sign-in value belongs to.

    `sign_in` does nothing when this browser already holds the session (a
    token pasted again with the workshop open in another tab, issue #64), and
    then nothing is logged either: nobody signed in.
    """
    value = text_field(request.get_json(silent=True), "signin")
    if not SIGNIN_RE.fullmatch(value):
        return refusal("format")
    user = account_for(signin_hash(value))
    if user is None:
        return refusal("unknown-token")
    if user.banned:
        return refusal("banned")
    if sign_in(user):
        # No `{ip}` in the format, so core's `log` leaves the address out.
        log("logins", "[{date}] token door: {name} signed in", name=user.name)
    return jsonify({"success": True,
                    "data": {"next": after_entry(), "name": user.name}})


class TokenDoor(Door):
    """The box on the participant side of the login page.

    Always open. A code that admits people is not what opens it: somebody who
    registered last week comes back through this door on a day when every code
    is retired, and their token must still be asked for.
    """
    name = "token"
    template = "workshop_token_door.html"
    title = _l("Enter your code")
    intro = _l("First time? Enter the code your supervisor gave you. "
               "Coming back? Enter your personal code.")
    label = _l("Enter your code")

    def open(self):
        return True


# ---------------------------------------------------------------------------
# An account with no name cannot type one
# ---------------------------------------------------------------------------

def token_account():
    """Is this request served as an account made at the token door?

    One primary-key lookup, kept for the request: the navbar asks on every
    page, the guard below on two endpoints.
    """
    if "ws_token_account" not in g:
        user_id = session.get("id")
        g.ws_token_account = bool(
            user_id
            and TokenAccount.query.filter_by(user_id=user_id).first())
    return g.ws_token_account


def _no_settings():
    """Keep a token account away from core's settings.

    That page is a name box, an e-mail box and a password box. The first two
    are where a lycéen would type who they are, into an account whose whole
    point is to hold nothing of the kind; the third would give it a second
    way in that nobody was told about. The navbar hides the link
    (navbar.html), and this is what holds when the address is typed.

    Not gated on the plugin switch (toggle.py): the promise was made to the
    account, and switching the plugin off does not unmake it.

    Reading the profile is untouched. An admin can still rename an account
    from the admin panel: that is staff typing, not the participant.
    """
    if request.endpoint == SETTINGS_PAGE:
        if token_account():
            return redirect(after_entry())
    elif request.endpoint == SETTINGS_API:
        if request.method not in ("GET", "HEAD", "OPTIONS") and token_account():
            return jsonify({"success": False}), 403
    return None


def load_tokendoor(app):
    app.register_blueprint(workshop_tokendoor)
    register_door(TokenDoor())
    app.before_request(_no_settings)
    # navbar.html drops the Settings entry for these accounts.
    app.jinja_env.globals["token_account"] = token_account
