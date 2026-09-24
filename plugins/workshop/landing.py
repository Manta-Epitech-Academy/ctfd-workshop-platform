"""Make the workshop page the landing page — without touching CTFd core.

Two places decide where a participant ends up:

  1. `/` — CTFd serves the `index` Page there (`views.static_html`).
  2. after login/registration — CTFd falls back to `challenges.listing`
     (CTFd/auth.py). It honours an explicit `?next=` *before* that fallback,
     so rewriting the fallback never breaks a deep link.

Both are redirected to `/workshop` for a signed-in user. The challenge board
stays exactly where it was and keeps working; it is simply no longer the first
thing a participant sees.

Anonymous visitors still get the index Page — it is the public front door, and
`/workshop` requires authentication anyway. Nothing is lost for signed-in users
either: the index document is the Intro section of the workshop page.
"""
from flask import redirect, request, url_for

from CTFd.models import Challenges
from CTFd.utils.user import authed

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
