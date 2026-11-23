#!/usr/bin/env python3
"""Does a plain board keep the code door, and nothing else of the workshop?

    python3 scripts/board_codes_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §71): the third state of the switch, "CTF with codes". It holds the
instance to this:

  * the switch page has three states, and the form it had with two still
    works;
  * in the board state every workshop route is a 404 except the door's, and
    the admin bar keeps the registration codes beside the switch;
  * `/` sends an anonymous visitor to the door and `/admin` to the staff
    form; the login page draws the door, and neither Jump nor the supervisor
    join;
  * a code makes an account that lands on the challenge board, and comes back
    to it with its token;
  * two codes are two cohorts: an account sees its own and not the other;
  * while the board is closed, whoever comes in is told to come back, on a
    page that does not say "workshop";
  * switched off, the door is gone; switched back to the workshop, the same
    account lands on `/workshop`.

Shaped like scripts/token_door_check.py, which it borrows the token
arithmetic from: positional arguments, `check(cond, label)`, `ALL GREEN` or
`FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back**: the accounts and the codes it made are deleted,
and the switch and the challenge visibility are left as they were found,
including on an exception or a Ctrl-C. It changes the state of the instance
for a few seconds. Do not run it during a session.
"""
import atexit
import hashlib
import hmac
import re
import secrets
import sys
import uuid

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
RUN = uuid.uuid4().hex[:6]
PAGE = "/admin/workshop/codes"
PLUGIN = "/admin/workshop/plugin"
DOOR = "/api/v1/workshop/door"
fails = []

# assets/door.js, constant for constant (see scripts/token_door_check.py).
SALT = b"ctfd-workshop-token-door-v1"
INFO_SIGNIN = b"sign-in"

# One route per blueprint that must stay shut on a plain board.
SHUT = [
    "/workshop", "/toolbox", "/api/v1/workshop/graph", "/supervisor/join",
    "/api/v1/workshop/certificate", "/jump/enter",
    "/admin/workshop/settings", "/admin/workshop/answers", "/admin/workshop/stats",
    "/admin/workshop/submissions", "/admin/workshop/feedback",
    "/admin/workshop/sync", "/admin/workshop/jump",
]


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
    return hmac.new(prk, INFO_SIGNIN + b"\x01", hashlib.sha256).hexdigest()


admin = requests.Session()
admin.post(BASE + "/login", timeout=20,
           data={"name": "admin", "password": ADMIN_PASS,
                 "nonce": nonce(admin, "/login?staff=1"), "_submit": "Submit"})
if admin.get(BASE + PLUGIN, timeout=20).status_code != 200:
    sys.exit(f"{PLUGIN} does not answer: not an admin session, or an old image")


def H():
    return {"CSRF-Token": nonce(admin, PLUGIN), "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + "/api/v1" + path, headers=H(), timeout=20, **kw)


def state():
    html = admin.get(BASE + PLUGIN, timeout=20).text
    m = re.search(r'name="state"\s+id="ws-state-\w+"\s+value="(\w+)"\s+checked', html)
    return m.group(1) if m else None


def switch(to):
    admin.post(BASE + PLUGIN, timeout=20,
               data={"nonce": nonce(admin, PLUGIN), "state": to})


def codes_page(**data):
    return admin.post(BASE + PAGE, timeout=20, allow_redirects=False,
                      data={"nonce": nonce(admin, PAGE), **data})


def code_id(code):
    html = admin.get(BASE + PAGE, timeout=20).text
    if "<tbody>" not in html:
        return None
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    for chunk in body.split("<tr>")[1:]:
        if f"<code>{code}</code>" in chunk:
            return re.search(r'name="id" value="(\d+)"', chunk).group(1)
    return None


def visibility(value=None):
    if value is None:
        r = api("GET", "/configs/challenge_visibility")
        return r.json()["data"]["value"] if r.status_code == 200 else "private"
    return api("PATCH", "/configs", json={"challenge_visibility": value})


WAS = state()
if WAS is None:
    sys.exit("the switch page has no three-state form: an image older than §71")
WAS_VISIBLE = visibility()
SUPERVISOR_KEY = "workshop_supervisor_code"
r = api("GET", f"/configs/{SUPERVISOR_KEY}")
WAS_SUPERVISOR = r.json()["data"]["value"] if r.status_code == 200 else None
CODE_A, CODE_B = f"BOARD-A-{RUN}", f"BOARD-B-{RUN}"
made_users = []


def restore():
    visibility(WAS_VISIBLE)
    if WAS_SUPERVISOR is None:
        api("DELETE", f"/configs/{SUPERVISOR_KEY}")
    else:
        api("PATCH", "/configs", json={SUPERVISOR_KEY: WAS_SUPERVISOR})
    # The codes' page answers in two states of the three.
    if state() == "off":
        switch("board")
    for uid in made_users:
        api("DELETE", f"/users/{uid}")
    for code in (CODE_A, CODE_B):
        ident = code_id(code)
        if ident:
            codes_page(action="delete", id=ident)
    switch(WAS)


atexit.register(restore)


class Browser:
    """A browser nobody has signed in on: a session and its CSRF nonce."""

    def __init__(self):
        self.s = requests.Session()
        self.h = {"CSRF-Token": nonce(self.s, "/login"),
                  "Content-Type": "application/json"}
        self.signin_value = signin_value(secrets.token_bytes(16))

    def post(self, path, **kw):
        r = self.s.post(BASE + DOOR + path, headers=self.h, timeout=20, **kw)
        if r.status_code == 200:
            # Signing in changes the session's nonce.
            self.h["CSRF-Token"] = nonce(self.s, "/")
        return r

    def register(self, code):
        r = self.post("/register", json={"code": code, "signin": self.signin_value})
        if r.status_code == 200:
            made_users.append(self.who()["id"])
        return r

    def signin(self):
        return self.post("/signin", json={"signin": self.signin_value})

    def get(self, path):
        return self.s.get(BASE + path, timeout=20, allow_redirects=False)

    def who(self):
        r = self.get("/api/v1/users/me")
        return r.json()["data"] if r.status_code == 200 else None

    def out(self):
        self.s.get(BASE + "/logout", timeout=20)
        self.h["CSRF-Token"] = nonce(self.s, "/login")


def statuses(session, paths):
    return {p: session.get(BASE + p, timeout=20, allow_redirects=False).status_code
            for p in paths}


def admin_menu():
    html = admin.get(BASE + "/admin/statistics", timeout=20).text
    return sorted(set(re.findall(r'href="(/admin/workshop/[^"]+)"', html)))


def next_of(response):
    return (response.json().get("data") or {}).get("next") if response.ok else None


# ---------------------------------------------------------------------------
print("== the switch has three states ==")
admin.post(BASE + PLUGIN, timeout=20,
           data={"nonce": nonce(admin, PLUGIN), "enabled": "on"})
check(state() == "workshop", "the old form's `enabled=on` is the workshop state")
admin.post(BASE + PLUGIN, timeout=20,
           data={"nonce": nonce(admin, PLUGIN), "enabled": "off"})
check(state() == "off", "and its `enabled=off` is off, not the board")
switch("board")
check(state() == "board", "the page reports the board with codes")
check("badge-success" not in admin.get(BASE + PLUGIN, timeout=20).text,
      "which is not reported as the workshop being enabled")
visibility("private")

# ---------------------------------------------------------------------------
print("\n== what answers on a plain board ==")
c = statuses(admin, SHUT)
check(all(v == 404 for v in c.values()), f"every workshop route is a 404: {c}")
check(admin.get(BASE + PAGE, timeout=20).status_code == 200,
      "the registration codes' page answers")
check(admin_menu() == [PAGE, PLUGIN],
      f"the admin bar keeps the codes and the switch: {admin_menu()}")
board = admin.get(BASE + "/challenges", timeout=20).text
nav = re.findall(r'class="epi-nav-link[^"]*"\s+href="([^"]+)"',
                 board.split('id="epi-nav"', 1)[-1].split("</nav>", 1)[0])
check("/workshop" not in nav and "/toolbox" not in nav and "/challenges" in nav,
      f"the navbar leads to the board, not to a workshop: {nav}")

# ---------------------------------------------------------------------------
print("\n== the front door ==")
visitor = requests.Session()
root = visitor.get(BASE + "/", timeout=20, allow_redirects=False)
check(root.status_code in (301, 302)
      and root.headers.get("Location", "").rstrip("/").endswith("/login"),
      f"/ sends an anonymous visitor to the door ({root.status_code})")
adm = visitor.get(BASE + "/admin", timeout=20, allow_redirects=False)
check("staff=1" in adm.headers.get("Location", ""),
      "/admin sends them to the staff form")
front = visitor.get(BASE + "/login", timeout=20).text
check("data-ws-door" in front and 'id="ws-door-code"' in front,
      "the login page draws the code door")
check("/jump/" not in front, "and no Jump button")
# With a supervisor code set, the workshop's staff form links to the join.
api("PATCH", "/configs", json={SUPERVISOR_KEY: f"SUP-{RUN}"})
staff = visitor.get(BASE + "/login?staff=1", timeout=20).text
check('type="password"' in staff, "the staff form is where it was")
check("/supervisor/join" not in staff,
      "with no link to the supervisor join, even with a supervisor code set")

# ---------------------------------------------------------------------------
print("\n== a code makes an account that lands on the board ==")
for code in (CODE_A, CODE_B):
    if codes_page(action="create", code=code, campus=f"Board {RUN}").status_code != 302:
        sys.exit(f"could not create {code}")
a, a2, b = Browser(), Browser(), Browser()
r = a.register(CODE_A)
check(r.status_code == 200 and next_of(r) == "/challenges",
      f"registering answers with the board as the next page ({r.status_code}, "
      f"{next_of(r)})")
check(a.get("/challenges").status_code == 200, "and the board opens")
check(a.get("/").status_code == 200, "/ is core's index for somebody signed in")
me = a.who()
r = a.get("/settings")
check(r.status_code in (301, 302)
      and r.headers.get("Location", "").endswith("/challenges"),
      "an account with no name is kept off the settings page, and sent to the board")
a.out()
check(a.who() is None, "signed out")
r = a.signin()
check(r.status_code == 200 and next_of(r) == "/challenges"
      and (a.who() or {}).get("id") == me["id"],
      "the token opens the same account, on the board")

# ---------------------------------------------------------------------------
print("\n== two codes, two cohorts ==")
a2.register(CODE_A)
b.register(CODE_B)
ida, ida2, idb = me["id"], a2.who()["id"], b.who()["id"]
check(a.get(f"/api/v1/users/{ida2}").status_code == 200,
      "an account sees another one of its own code")
check(a.get(f"/api/v1/users/{idb}").status_code == 404,
      "and not one of the other code")
listed = {u["id"] for u in a.get("/api/v1/users").json().get("data", [])}
check(ida2 in listed and idb not in listed,
      "the participant list holds its own cohort only")
check(b.get(f"/users/{ida}").status_code == 404,
      "the wall stands in the other direction too")

# ---------------------------------------------------------------------------
print("\n== a board that is not open yet ==")
visibility("admins")
r = b.signin()
check(next_of(r) == "/door/waiting", f"coming in leads to the waiting page ({next_of(r)})")
page = b.get("/door/waiting")
check(page.status_code == 200
      and ("pas encore ouvert" in page.text or "not open yet" in page.text),
      "which says the event is not open yet")
check("atelier" not in page.text.split("epi-hero-title", 1)[-1].split("</h1>", 1)[0].lower()
      and "workshop" not in page.text.split("epi-hero-title", 1)[-1].split("</h1>", 1)[0].lower(),
      "without calling it a workshop")
visibility("private")
check(b.get("/door/waiting").headers.get("Location", "").endswith("/challenges"),
      "once it opens, the waiting page leads to the board")

# ---------------------------------------------------------------------------
print("\n== off, and back to the workshop ==")
switch("off")
a.out()
r = a.signin()
check(r.status_code == 404, f"switched off, the door's requests are a 404 ({r.status_code})")
check("data-ws-door" not in visitor.get(BASE + "/login", timeout=20).text,
      "and the login page is the staff form alone")
check(admin_menu() == [PLUGIN], f"the admin bar keeps only the switch: {admin_menu()}")
switch("workshop")
check("/supervisor/join" in visitor.get(BASE + "/login?staff=1", timeout=20).text,
      "in the workshop state the staff form links to the supervisor join again")
r = a.signin()
check(r.status_code == 200 and next_of(r) in ("/workshop", "/door/waiting")
      and (a.who() or {}).get("id") == me["id"],
      f"in the workshop state the same token leads to the workshop ({next_of(r)})")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
