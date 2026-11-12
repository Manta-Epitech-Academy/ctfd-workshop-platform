"""The ways a participant gets in, as a list the login page reads.

`/login` has two sides (login.html): the staff form, which is core's, and the
participant side, which used to be Jump's button and nothing else. The page
asked `jump.py` what to draw, and landing.py asked it where to send an
anonymous visitor. A second way in would have meant a second special case in
each (PLAN.md §59).

So a way in registers a `Door` here, and the page draws whatever doors are
open. login.html reads only this module; it does not know what Jump is.

**A door is drawn, never followed.** A door used to be able to answer for the
whole page and send an anonymous visitor somewhere else (Jump's rule, §53).
That is gone (§62): the participant side of `/login` is the instance's front
door, the token door is always on it, and no door sends anybody off the
instance without a click.

A door is not a route. The route that actually signs somebody in stays in the
door's own module (`/jump/enter` in jump.py), and so does everything it
stores. What is registered is only what the login page needs: whether the
door is open, and the piece of page that leads to it.

The external door (external.py, §42) is deliberately not one: its address is
half its secret, and nothing participant-facing may point at it.

What every door needs once it has decided who somebody is lives here too:
`sign_in`, and `workshop_is_open` for the page to send them to afterwards.
"""
import hmac

from flask import request, session

from CTFd.utils import get_config
from CTFd.utils.security import signing
from CTFd.utils.security.auth import login_user

from .toggle import workshop_enabled

_DOORS = {}


class Door:
    """One way in. Subclass it and `register()` an instance."""

    # Unique, and stable: the registry is keyed on it.
    name = ""
    # The template included on the participant side of login.html.
    template = ""
    # The page's title and the line under it while this door is the first one
    # drawn, and what the staff form's link back to the participant side
    # says. Lazy strings: they are built at import time and read in a request.
    # An empty title leaves the page its own ("Login").
    title = ""
    intro = ""
    label = ""

    def open(self):
        """Is there anything to draw right now?"""
        return False


def register(door):
    """Add a door, or replace the one of the same name (see cohorts.py)."""
    _DOORS[door.name] = door


def open_doors():
    """The doors to draw, in registration order. None while the plugin is off:
    switched off, CTFd's login page is CTFd's again (toggle.py, §39)."""
    if not workshop_enabled():
        return []
    return [door for door in _DOORS.values() if door.open()]


def login_is_staff():
    """Which side of login.html to draw: the staff form, or the doors.

    The participant side is the default, as on Jump's own login, and
    `?staff=1` is the other one. One URL rather than a route of our own,
    because the form posts to core's `auth.login` and core re-renders
    `login.html` itself after a failed attempt: a second route would have to
    re-implement signing in.

    The staff form wins whenever the participant side would be the wrong
    answer:

      - no door is open (which also covers the plugin switched off) and a
        page with nothing on it is a dead end;
      - the request is a POST, which only the staff form sends, so the error
        after a wrong password lands next to the form that caused it;
      - `next` is an admin page: core's `admins_only` and our `staff_only`
        (staff.py) both bounce to `auth.login` with `next=request.full_path`,
        and no participant is going there.

    One admin URL does not carry a `next`: core's bare `/admin`
    (CTFd/admin/__init__.py `view`) redirects to `auth.login` with nothing
    after it, so landing.py sends an anonymous `/admin` to the staff side
    itself.
    """
    return (not open_doors()
            or request.method == "POST"
            or "staff" in request.args
            or (request.args.get("next") or "").startswith("/admin"))


def holds(user):
    """Is this browser already signed in as `user`?

    The two fields `get_current_user()` checks (CTFd/utils/user/__init__.py),
    so "yes" here is exactly "CTFd would serve this request as `user`". The
    hash is how a password change ends every other session, and a session it
    ended is not one to keep: that browser is signed in from scratch.
    """
    return (session.get("id") == user.id
            and hmac.compare_digest(session.get("hash") or "",
                                    signing.hmac(user.password)))


def sign_in(user):
    """Open a session as `user`, unless this browser already holds one.

    Returns whether a new session was opened. A door is walked through more
    than once (Jump's link is clicked again, a token is pasted again), usually
    with the workshop still open in another tab. Signing in again would change
    nothing about who this is, but `login_user` writes a new CSRF nonce, and
    every page already open still sends the old one: CTFd refuses each of its
    submissions with a 403 before grading (issue #64).

    Otherwise the local sign-in path (CTFd/auth.py `login`), not the OAuth
    one, which skips the regeneration and leaves the pre-login session id
    valid.
    """
    if holds(user):
        return False
    session.regenerate()
    login_user(user)
    return True


def workshop_is_open():
    """Can a participant actually open `/workshop` right now?

    Two instance states make it a raw error rather than a page:
    `challenge_visibility == "admins"`, and a CTF window that is closed or has
    not opened yet. Both are the operator's doing and both are temporary, so
    whoever just came in gets a sentence in their own language instead of a
    403 they cannot act on.
    """
    from CTFd.utils.dates import ctftime

    if str(get_config("challenge_visibility") or "private") == "admins":
        return False
    return bool(ctftime())


def load_doors(app):
    # login.html asks which side to open on, and which doors to draw.
    app.jinja_env.globals["login_is_staff"] = login_is_staff
    app.jinja_env.globals["open_doors"] = open_doors
