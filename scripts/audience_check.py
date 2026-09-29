#!/usr/bin/env python3
"""Can one cohort see the other? It should not be able to, anywhere.

    python3 scripts/audience_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §43). It builds three accounts — one ordinary participant standing in
for somebody who came through Jump, and one in each of two external cohorts —
gives each a solve so they have a score worth hiding, and then asks every
surface that lists or serves an account, as each of them in turn.

The interesting assertions are the negative ones. A feature that hides things
passes a careless test by hiding everything, so each surface is checked twice:
the other cohort is **absent**, and the reader's own cohort is **present**.

Shaped like scripts/supervisor_check.py — positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back**: every account, code and solve it makes is
removed at exit, including on an exception or a Ctrl-C.
"""
import atexit
import re
import sys
import uuid

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
RUN = uuid.uuid4().hex[:6]
PAGE = "/admin/workshop/external"
CODE_A = f"AUD-A-{RUN}"
CODE_B = f"AUD-B-{RUN}"
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


admin = requests.Session()
admin.post(BASE + "/login", timeout=20,
           data={"name": "admin", "password": ADMIN_PASS,
                 "nonce": nonce(admin, "/login"), "_submit": "Submit"})
TOKEN = nonce(admin, PAGE)
if TOKEN is None:
    sys.exit(f"{PAGE} does not answer — not an admin session, or an old image")
H = {"CSRF-Token": TOKEN, "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + "/api/v1" + path, headers=H, timeout=20, **kw)


def post(**data):
    return admin.post(BASE + PAGE, timeout=20, data={"nonce": TOKEN, **data})


start = admin.get(BASE + PAGE, timeout=20).text
was_on = bool(re.search(r'id="ws-ext-on"[^>]*\bchecked', start))
made, codes_added, awards = [], [], []


def restore():
    for a in awards:
        api("DELETE", f"/awards/{a}")
    for uid in made:
        api("DELETE", f"/users/{uid}")
    for code in codes_added:
        post(action="remove", code=code)
    post(action="switch", **({"enabled": "on"} if was_on else {}))


atexit.register(restore)

post(action="switch", enabled="on")
for code in (CODE_A, CODE_B):
    post(action="add", code=code)
    codes_added.append(code)


def make(name, password, code=None):
    """An account, through the door when a code is given, by the admin API when
    not — which is how a Jump account looks to everything here: no code."""
    if code:
        s = requests.Session()
        n = nonce(s, "/external/join")
        if n is None:
            sys.exit("the external door is not answering; is the throttle full?")
        s.post(BASE + "/external/join", timeout=20,
               data={"nonce": n, "code": code, "name": name,
                     "email": f"{name}@example.invalid", "password": password})
    else:
        api("POST", "/users", json={"name": name, "email": f"{name}@example.invalid",
                                    "password": password, "type": "user",
                                    "verified": True})
    row = next((u for u in (api("GET", f"/users?view=admin&q={name}&field=name")
                            .json().get("data") or []) if u["name"] == name), None)
    if row is None:
        # Almost always the throttle: `external_check.py` fills it on purpose,
        # so running the two back to back inside fifteen minutes leaves this
        # address locked out of the door. Say that instead of "could not
        # create", which sent one run looking for a bug that was not there.
        shut = requests.get(BASE + "/external/join", timeout=20).status_code == 404
        sys.exit(f"could not create {name}"
                 + (" — the external door is answering 404 for this address. "
                    "The throttle is probably full from an earlier run; wait "
                    "fifteen minutes or run from another address." if shut else ""))
    made.append(row["id"])
    # A score, so there is something worth hiding on the scoreboard.
    r = api("POST", "/awards", json={"user_id": row["id"], "name": "probe",
                                     "value": 10, "category": "probe"})
    if r.status_code == 200:
        awards.append(r.json()["data"]["id"])
    s = requests.Session()
    s.post(BASE + "/login", timeout=20,
           data={"name": name, "password": password,
                 "nonce": nonce(s, "/login"), "_submit": "Submit"})
    return row["id"], name, s


JUMP_ID, JUMP_NAME, jump = make(f"jump-{RUN}", f"jump-{RUN}-pass")
A_ID, A_NAME, alpha = make(f"aud-a-{RUN}", f"aud-a-{RUN}-pass", CODE_A)
B_ID, B_NAME, beta = make(f"aud-b-{RUN}", f"aud-b-{RUN}-pass", CODE_B)


def names_on_scoreboard(s):
    r = s.get(BASE + "/api/v1/scoreboard", timeout=20)
    return [row["name"] for row in (r.json().get("data") or [])]


def names_in_top(s):
    r = s.get(BASE + "/api/v1/scoreboard/top/10", timeout=20)
    return [row["name"] for row in (r.json().get("data") or {}).values()]


def names_in_users(s):
    r = s.get(BASE + "/api/v1/users", timeout=20)
    return [row["name"] for row in (r.json().get("data") or [])]


def users_page(s):
    return s.get(BASE + "/users", timeout=20).text


for who, s, mine, theirs in (
        ("a Jump participant", jump, JUMP_NAME, [A_NAME, B_NAME]),
        (f"cohort {CODE_A}", alpha, A_NAME, [JUMP_NAME, B_NAME]),
        (f"cohort {CODE_B}", beta, B_NAME, [JUMP_NAME, A_NAME])):
    print(f"\n== seen by {who} ==")
    board = names_on_scoreboard(s)
    check(mine in board, f"own cohort is on the scoreboard: {board}")
    check(not [n for n in theirs if n in board], f"the others are not: {theirs}")

    top = names_in_top(s)
    check(mine in top and not [n for n in theirs if n in top],
          f"same for /scoreboard/top: {top}")

    listed = names_in_users(s)
    check(mine in listed and not [n for n in theirs if n in listed],
          f"same for the users API: {listed}")

    page = users_page(s)
    check(mine in page and not [n for n in theirs if n in page],
          "same for the /users page, which CTFd renders server-side")

print("\n== and one cohort cannot open another's account ==")
for label, s, mine, other in (("jump -> external", jump, JUMP_ID, A_ID),
                              ("external -> jump", alpha, A_ID, JUMP_ID),
                              ("external -> other cohort", alpha, A_ID, B_ID)):
    own = s.get(BASE + f"/api/v1/users/{mine}", timeout=20).status_code
    foreign = s.get(BASE + f"/api/v1/users/{other}", timeout=20).status_code
    check(own == 200 and foreign == 404,
          f"{label}: own {own}, other {foreign}")
    html = s.get(BASE + f"/users/{other}", timeout=20, allow_redirects=False).status_code
    solves = s.get(BASE + f"/api/v1/users/{other}/solves", timeout=20).status_code
    check(html == 404 and solves == 404,
          f"{label}: profile page {html}, solves {solves}")

print("\n== staff still see everyone ==")
board = names_on_scoreboard(admin)
check(all(n in board for n in (JUMP_NAME, A_NAME, B_NAME)),
      f"the admin scoreboard has all three: {board}")
check(all(admin.get(BASE + f"/api/v1/users/{i}", timeout=20).status_code == 200
          for i in (JUMP_ID, A_ID, B_ID)), "and every account opens")

print("\n== with no external account, nothing is filtered ==")
for code in codes_added:
    post(action="remove", code=code)
for uid in (A_ID, B_ID):
    api("DELETE", f"/users/{uid}")
    made.remove(uid)
board = names_on_scoreboard(jump)
check(JUMP_NAME in board, f"a plain instance behaves as before: {board}")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
