"""Where somebody lands on an instance, without touching CTFd core.

Four places decide it:

  1. `/` — CTFd serves the `index` Page there (`views.static_html`).
  2. after login/registration — CTFd falls back to `challenges.listing`
     (CTFd/auth.py). It honours an explicit `?next=` *before* that fallback,
     so rewriting the fallback never breaks a deep link.
  3. the talent door of `/login` (login.html, `login_is_staff()` in jump.py).
  4. core's bare `/admin`, which sends a visitor to `auth.login` with no `next`.

A signed-in user is sent from the first two to `/workshop`. The challenge
board stays exactly where it was and keeps working; it is simply no longer the
first thing a participant sees. Nothing is lost by skipping the index either:
its document is the Intro section of the workshop page.

An anonymous visitor is sent to Jump whenever Jump is the only way a
participant gets in (`jump_is_the_way_in()`, PLAN.md §53): from `/` and from
the talent door, straight to the one Jump this instance answers to
(`talent_door()`), or, when it answers to several, from `/` to the talent door,
which is then the choice between them. The copy that presents an activity and
the sign-in both live on Jump, so a page here that restated the first or
offered the second would be one more page between a talent and the activity.
An instance that lets people register keeps its front door: it is theirs.

What stays is every door a password opens: the staff form (`/login?staff=1`),
the supervisor and external joins, and `/admin`, which is sent to the staff
form rather than on to Jump. Admins, mentors and the emergency local accounts
sign in there.

Each hook reads the switch itself: `guard_routes` (toggle.py) only covers the
plugin's blueprints, and switched off, CTFd is CTFd again (§39).
"""
from flask import redirect, request, url_for

from CTFd.models import Challenges
from CTFd.utils.user import authed

from .jump import jump_is_the_way_in, login_is_staff, talent_door
from .toggle import workshop_enabled

# Endpoints whose default post-authentication redirect we retarget.
AUTH_ENDPOINTS = ("auth.login", "auth.register")


def load_landing(app):
    def workshop_url():
        return url_for("workshop_page.workshop")

    @app.before_request
    def root_to_workshop():
        if request.method != "GET" or request.path != "/" or not authed():
            return None
        # Switched off, `/` is CTFd's own front door again (toggle.py, §39).
        if not workshop_enabled():
            return None
        # On an instance with no content yet (fresh install, before the first
        # sync) the workshop page has nothing to show — leave the index alone.
        if Challenges.query.count() == 0:
            return None
        return redirect(workshop_url())

    @app.before_request
    def anonymous_to_jump():
        # Cheapest tests first: this runs on every request.
        if request.method != "GET" or authed():
            return None
        front = request.path == "/"
        if not front and request.endpoint != "auth.login":
            return None
        # `jump_enabled()`, inside, reads the switch.
        if not jump_is_the_way_in():
            return None
        if front:
            return redirect(talent_door() or url_for("auth.login"))
        # The staff form, a failed attempt or an admin `next` stay here.
        if login_is_staff():
            return None
        target = talent_door()
        return redirect(target) if target else None

    @app.before_request
    def admin_to_staff_door():
        if request.endpoint != "admin.view" or authed():
            return None
        if not workshop_enabled():
            return None
        return redirect(url_for("auth.login", staff=1, next=url_for("admin.view")))

    @app.after_request
    def post_auth_to_workshop(response):
        if request.endpoint not in AUTH_ENDPOINTS or not workshop_enabled():
            return response
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get("Location", "")
        board = url_for("challenges.listing")
        # Only the default landing is retargeted; an explicit ?next= produced a
        # different Location and is left untouched.
        if location.rstrip("/").endswith(board.rstrip("/")):
            response.headers["Location"] = workshop_url()
        return response
