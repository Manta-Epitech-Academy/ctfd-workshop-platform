#!/usr/bin/env python3
"""Is the token door the front door, and does anything send a visitor away?

    python3 scripts/front_door_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §42, §62). It holds the instance to this:

  * an anonymous visitor to `/` lands on the login page, and that page is the
    token door: in instructor-led mode and in self-serve mode, with core's
    registration form closed and with it open. The mode and core's setting
    change nothing about the way in;
  * every address an anonymous visitor can type or be bounced to ends on this
    instance. **Nothing redirects to Jump**, in the one corner where PLAN.md
    §53 used to: registration closed and one Jump key. There Jump is a button
    drawn under the token door, and the page holds no refresh either;
  * a self-serve instance is entered with a registration code like any other:
    a code makes an account, a wrong one is refused, and the account comes
    back with its token;
  * what a password opens is where it was: the staff form, a bare `/admin`
    sent to it, the supervisor's join page;
  * logging out lands on the token door, and a signed-in participant who
    opens `/` is sent to the workshop;
  * switched off, `/` is core's index again.

Shaped like scripts/token_door_check.py, and it borrows that script's way of
playing the browser: a token is 16 random bytes and the sign-in value is its
HKDF-SHA-256 (RFC 5869).

**It puts the instance back**: the mode, core's registration setting, the Jump
keys and slug and the supervisor code are saved and restored, the account and
the code it made are deleted and the plugin switch is left as it was found,
including on an exception or a Ctrl-C. It switches the plugin off for a few
seconds near the end and changes the mode while it runs. Do not run it during
a session.
"""
import atexit
import hashlib
import hmac
import json
import re
import secrets
import sys
import uuid
from urllib.parse import urlsplit

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
RUN = uuid.uuid4().hex[:6]
PAGE = "/admin/workshop/codes"
PLUGIN = "/admin/workshop/plugin"
DOOR = "/api/v1/workshop/door"
# A Jump nobody can reach: `.invalid` never resolves (RFC 2606), so a request
# that did follow a redirect there would fail loudly rather than leave.
JUMP = "https://jump.frontdoor-check.invalid"
SAVED_KEYS = ("workshop_mode", "registration_visibility", "workshop_jump_keys",
              "workshop_jump_instance", "workshop_supervisor_code")
SALT = b"ctfd-workshop-token-door-v1"
fails = []


def check(cond, label):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


def nonce(s, path="/"):
    r = s.get(BASE + path, timeout=20)
    m = (re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text)
         or re.search(r"['\"]csrfNonce['\"]\s*:\s*['\"]([0-9a-f]+)", r.text))
    return m.group(1) if m else None


def signin_value(raw):
    """HKDF-SHA-256 (RFC 5869), one block: extract, then expand to 32 bytes."""
    prk = hmac.new(SALT, raw, hashlib.sha256).digest()
    return hmac.new(prk, b"sign-in\x01", hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# The admin
# ---------------------------------------------------------------------------

admin = requests.Session()
admin.post(BASE + "/login", timeout=20,
           data={"name": "admin", "password": ADMIN_PASS,
                 "nonce": nonce(admin, "/login?staff=1"), "_submit": "Submit"})
if admin.get(BASE + PAGE, timeout=20).status_code != 200:
    sys.exit(f"{PAGE} does not answer: wrong password, or the plugin is "
             "older than the registration codes")
TOKEN = nonce(admin, PAGE)
H = {"CSRF-Token": TOKEN, "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + "/api/v1" + path, headers=H, timeout=20, **kw)


def config(key):
    r = api("GET", f"/configs/{key}")
    return r.json()["data"]["value"] if r.status_code == 200 else None


def set_configs(values):
    r = api("PATCH", "/configs", json=values)
    if r.status_code != 200:
        sys.exit(f"could not write {sorted(values)}: HTTP {r.status_code}")


def codes_page(**data):
    return admin.post(BASE + PAGE, timeout=20, allow_redirects=False,
                      data={"nonce": TOKEN, **data})


def code_id(code):
    html = admin.get(BASE + PAGE, timeout=20).text
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    for chunk in body.split("<tr>")[1:]:
        if f"<code>{code}</code>" in chunk:
            return re.search(r'name="id" value="(\d+)"', chunk).group(1)
    return None


def plugin_on():
    return "badge-success" in admin.get(BASE + PLUGIN, timeout=20).text


def switch(to):
    admin.post(BASE + PLUGIN, timeout=20,
               data={"nonce": nonce(admin, PLUGIN), "enabled": to})


if not plugin_on():
    sys.exit("the workshop plugin is switched off on this instance")
saved = {key: config(key) for key in SAVED_KEYS}
CODE = f"FRONT-{RUN}"
made_users = []


def restore():
    if not plugin_on():
        switch("on")
    # A key that was not there is emptied, not left with this script's value:
    # an empty one reads as unset everywhere these five are read.
    set_configs({key: "" if value is None else value
                 for key, value in saved.items()})
    for uid in made_users:
        api("DELETE", f"/users/{uid}")
    ident = code_id(CODE)
    if ident:
        codes_page(action="delete", id=ident)
    print("\nrestored: " + ", ".join(
        f"{key}={'(unset)' if value in (None, '') else '(kept)'}"
        for key, value in saved.items()))


atexit.register(restore)
if codes_page(action="create", code=CODE, campus=f"Front {RUN}").status_code != 302:
    sys.exit(f"could not create {CODE}")
# No Jump and no supervisor code to begin with, whatever the instance had.
set_configs({"workshop_jump_keys": "", "workshop_jump_instance": "",
             "workshop_supervisor_code": ""})


def lands(path, s=None):
    """Where one request to `path` is answered from: (status, Location)."""
    r = (s or requests).get(BASE + path, timeout=20, allow_redirects=False)
    return r.status_code, r.headers.get("Location", "")


def ends(path):
    """Where an anonymous visitor to `path` ends up, every redirect followed."""
    r = requests.Session().get(BASE + path, timeout=20)
    return r, urlsplit(r.url)


def is_door(html):
    side = html.split('class="epi-login-main"', 1)[-1].split("epi-login-foot", 1)[0]
    return ("data-ws-door-form" in side and 'id="ws-door-code"' in side
            and 'name="password"' not in side and 'name="email"' not in side)


class Browser:
    """A browser nobody has signed in on: a session and its CSRF nonce."""

    def __init__(self):
        self.s = requests.Session()
        self.h = {"CSRF-Token": nonce(self.s, "/login"),
                  "Content-Type": "application/json"}

    def post(self, path, **kw):
        r = self.s.post(BASE + DOOR + path, headers=self.h, timeout=20, **kw)
        if r.status_code == 200:
            # Signing in changes the session's nonce.
            self.h["CSRF-Token"] = nonce(self.s, "/")
        return r

    def who(self):
        r = self.s.get(BASE + "/api/v1/users/me", timeout=20, allow_redirects=False)
        return r.json()["data"] if r.status_code == 200 else None


# Every address an anonymous visitor can type or be bounced to.
ASKED = ("/", "/login", "/login?next=%2Fworkshop", "/workshop", "/challenges",
         "/scoreboard", "/users", "/notifications", "/settings", "/logout",
         "/register", "/door/waiting", "/admin", "/admin/workshop/codes")

# ---------------------------------------------------------------------------
print("== / leads to the token door, whatever the mode and core's setting ==")
for mode in ("instructor_led", "self_serve"):
    for visibility in ("private", "public"):
        set_configs({"workshop_mode": mode, "registration_visibility": visibility})
        where = f"{mode}, core registration {visibility}"
        status, location = lands("/")
        check(status == 302 and urlsplit(location).path == "/login"
              and not urlsplit(location).query,
              f"{where}: / is a redirect to /login ({status} {location})")
        page, url = ends("/")
        check(page.status_code == 200 and url.path == "/login" and is_door(page.text),
              f"{where}: and that page is the token door, with no name, no "
              f"e-mail and no password on it")
        check('href="/login?staff=1"' in page.text,
              f"{where}: the staff form is one link away")
        room = Browser()
        check(room.post("/code", json={"code": CODE}).status_code == 200
              and room.post("/code", json={"code": f"NOPE-{RUN}"}).status_code == 403,
              f"{where}: the door takes the registration code and refuses another")

# ---------------------------------------------------------------------------
print("\n== a self-serve instance is entered with a code like any other ==")
set_configs({"workshop_mode": "self_serve", "registration_visibility": "private"})
RAW = secrets.token_bytes(16)
SIGNIN = signin_value(RAW)
first = Browser()
check(first.post("/register", json={"code": f"NOPE-{RUN}", "signin": SIGNIN}).status_code == 403
      and first.who() is None,
      "no account without a registration code, self-serve or not")
made = first.post("/register", json={"code": CODE, "signin": SIGNIN})
ME = first.who()
if made.status_code != 200 or ME is None:
    sys.exit(f"registration failed in self-serve mode: HTTP {made.status_code}")
made_users.append(ME["id"])
check(ME["id"] > 0, f"the code made an account ({ME['name']})")
status, location = lands("/", first.s)
check(status == 302 and urlsplit(location).path == "/workshop",
      f"signed in, / goes to the workshop ({status} {location})")
out = first.s.get(BASE + "/logout", timeout=20)
check(urlsplit(out.url).path == "/login" and is_door(out.text) and first.who() is None,
      f"logging out lands on the token door ({urlsplit(out.url).path})")
back = Browser()
check(back.post("/signin", json={"signin": SIGNIN}).status_code == 200
      and (back.who() or {}).get("id") == ME["id"],
      "and the token opens the same account again, from another browser")

# ---------------------------------------------------------------------------
print("\n== with a Jump key, Jump is a button and never a redirect ==")
# The corner §53 redirected in: registration closed, one key.
set_configs({
    "registration_visibility": "private",
    "workshop_jump_instance": f"front-{RUN}",
    "workshop_jump_keys": json.dumps({f"front-{RUN}": {
        "origin": JUMP, "secret": secrets.token_hex(32), "label": "front"}})})
login = requests.get(BASE + "/login", timeout=20, allow_redirects=False)
button = login.text.find(f'href="{JUMP}"')
check(login.status_code == 200 and button > 0,
      "the key is live: the login page draws its Jump button")
check(is_door(login.text) and 0 < login.text.find("data-ws-door-form") < button,
      "under the token door, which comes first")
check('http-equiv="refresh"' not in login.text.lower()
      and "location.replace" not in login.text and "location.href =" not in login.text,
      "and the page holds no refresh that would follow it by itself")
for mode in ("instructor_led", "self_serve"):
    set_configs({"workshop_mode": mode})
    sent, left = [], []
    for path in ASKED:
        location = lands(path)[1]
        if location.startswith("http") and not location.startswith(BASE):
            sent.append((path, location))
        page, url = ends(path)
        if f"{url.scheme}://{url.netloc}" != BASE:
            left.append((path, page.url))
    check(not sent, f"{mode}: none of {len(ASKED)} anonymous addresses answers "
                    f"with a redirect off the instance {sent}")
    check(not left, f"{mode}: and followed to the end, every one of them ends "
                    f"on the instance {left}")
    for path in ("/", "/workshop", "/logout", "/door/waiting"):
        page, url = ends(path)
        check(url.path == "/login" and is_door(page.text),
              f"{mode}: {path} ends on the token door")
    # Core's own form, the one that asks for a name, an e-mail address and a
    # password: closed by core itself, which answers a 404 and no redirect.
    check(lands("/register") == (404, ""),
          f"{mode}: core's /register is closed, and is not a way round the door")
set_configs({"workshop_jump_keys": "", "workshop_jump_instance": ""})

# ---------------------------------------------------------------------------
print("\n== what a password opens is where it was ==")
staff = requests.get(BASE + "/login?staff=1", timeout=20, allow_redirects=False)
check(staff.status_code == 200 and 'name="password"' in staff.text
      and "data-ws-door-form" not in staff.text,
      "/login?staff=1 is the password form, with no door on it")
check('href="/login"' in staff.text, "and it links back to the participants' door")
status, location = lands("/admin")
check(status == 302 and location.endswith("/login?staff=1"),
      f"a bare /admin goes to the staff form ({status} {location})")
page, url = ends("/admin/workshop/codes")
check(url.path == "/login" and 'name="password"' in page.text,
      "and so does an admin page bounced to the login")
check(admin.get(BASE + PAGE, timeout=20).status_code == 200,
      "the admin who signed in on the staff form is still in")
wrong = requests.Session()
r = wrong.post(BASE + "/login", timeout=20, data={
    "name": "nobody" + RUN, "password": "wrong",
    "nonce": nonce(wrong, "/login?staff=1"), "_submit": "Submit"})
check('name="password"' in r.text and "data-ws-door-form" not in r.text,
      "a wrong password is answered on the staff form, not on the door")
check(lands("/supervisor/join")[0] == 404, "with no supervisor code, no join page")
set_configs({"workshop_supervisor_code": f"sup-{RUN}"})
join = requests.get(BASE + "/supervisor/join", timeout=20, allow_redirects=False)
check(join.status_code == 200 and 'type="password"' in join.text
      and "login?staff=1" in join.text,
      "with one, the supervisor's join page is drawn and names the staff form")
set_configs({"workshop_supervisor_code": ""})

# ---------------------------------------------------------------------------
print("\n== switched off, / is core's again ==")
switch("off")
status, location = lands("/")
check(status == 200, f"/ is a page of its own ({status} {location})")
page = requests.get(BASE + "/login", timeout=20)
check(page.status_code == 200 and "data-ws-door" not in page.text
      and 'name="password"' in page.text,
      "and /login is core's form")
switch("on")
check(lands("/")[0] == 302 and is_door(ends("/")[0].text),
      "switched back on, / leads to the token door")

print()
if fails:
    print(f"FAILURES: {fails}")
    sys.exit(1)
print("ALL GREEN")
