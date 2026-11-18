#!/usr/bin/env python3
"""Can one cohort see the other? It should not be able to, anywhere.

    python3 scripts/audience_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §43), and the one for what is left of the external door (§64). It
builds three accounts: one ordinary participant standing in for somebody who
came through Jump, and one in each of two cohorts of the retired external
door. Each gets a score worth hiding, and then every surface that lists or
serves an account is asked, as each of them in turn.

The external door is gone, so its two accounts are made the way an instance
that used it holds them: an account with a password, carrying its code in
the custom field `External code`. That is all the door ever wrote.

The interesting assertions are the negative ones. A feature that hides things
passes a careless test by hiding everything, so each surface is checked twice:
the other cohort is **absent**, and the reader's own cohort is **present**.

Then the retirement itself: the door's two addresses answer 404, its accounts
still sign in, and a cohort is shut out and let back in from the registration
codes' page without touching the other.

Shaped like scripts/supervisor_check.py: positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back**: every account and award it makes is removed at
exit, and the custom field too when this run is what made it, including on an
exception or a Ctrl-C.
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
PAGE = "/admin/workshop/codes"
FIELD_NAME = "External code"
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
                 "nonce": nonce(admin, "/login?staff=1"), "_submit": "Submit"})
TOKEN = nonce(admin, PAGE)
if TOKEN is None:
    sys.exit(f"{PAGE} does not answer — not an admin session, or an old image")
H = {"CSRF-Token": TOKEN, "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + "/api/v1" + path, headers=H, timeout=20, **kw)


def post(**data):
    return admin.post(BASE + PAGE, timeout=20, data={"nonce": TOKEN, **data})


fields = api("GET", "/configs/fields?type=user").json().get("data") or []
field = next((f for f in fields if f["name"] == FIELD_NAME), None)
made_field = field is None
if made_field:
    field = api("POST", "/configs/fields", json={
        "name": FIELD_NAME, "type": "user", "field_type": "text",
        "description": "probe", "required": False, "public": False,
        "editable": False}).json()["data"]
FIELD = field["id"]
made, awards = [], []


def restore():
    for a in awards:
        api("DELETE", f"/awards/{a}")
    for uid in made:
        api("DELETE", f"/users/{uid}")
    if made_field:
        api("DELETE", f"/configs/fields/{FIELD}")


atexit.register(restore)


def sign_in(name, password):
    s = requests.Session()
    s.post(BASE + "/login", timeout=20,
           data={"name": name, "password": password,
                 "nonce": nonce(s, "/login?staff=1"), "_submit": "Submit"})
    return s


def make(name, password, code=None):
    """An account by the admin API. With a code it carries the field the
    external door wrote; without, it is how a Jump account looks to
    everything here: no code."""
    body = {"name": name, "email": f"{name}@example.invalid",
            "password": password, "type": "user", "verified": True}
    if code:
        body["fields"] = [{"field_id": FIELD, "value": code}]
    r = api("POST", "/users", json=body)
    if r.status_code != 200:
        sys.exit(f"could not create {name}: {r.status_code} {r.text[:200]}")
    uid = r.json()["data"]["id"]
    made.append(uid)
    # A score, so there is something worth hiding on the scoreboard.
    r = api("POST", "/awards", json={"user_id": uid, "name": "probe",
                                     "value": 10, "category": "probe"})
    if r.status_code == 200:
        awards.append(r.json()["data"]["id"])
    return uid, name, sign_in(name, password)


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

print("\n== the door is gone, its accounts are not ==")
anon = requests.Session()
got = anon.get(BASE + "/external/join", timeout=20, allow_redirects=False)
check(got.status_code == 404, f"/external/join is a 404: {got.status_code}")
got = anon.post(BASE + "/external/join", timeout=20, allow_redirects=False,
                data={"nonce": nonce(anon, "/login"), "code": CODE_A,
                      "name": f"late-{RUN}", "email": f"late-{RUN}@example.invalid",
                      "password": f"late-{RUN}-pass"})
check(got.status_code == 404, f"and admits nobody: {got.status_code}")
late = api("GET", f"/users?view=admin&q=late-{RUN}&field=name").json().get("data") or []
check(late == [], "no account was made by asking")
got = admin.get(BASE + "/admin/workshop/external", timeout=20,
                allow_redirects=False)
check(got.status_code == 404, f"its admin page is a 404: {got.status_code}")


def is_in(s):
    r = s.get(BASE + "/api/v1/users/me", timeout=20, allow_redirects=False)
    return r.status_code == 200


def banned(uid):
    return api("GET", f"/users/{uid}").json()["data"].get("banned", False)


check(is_in(sign_in(A_NAME, f"aud-a-{RUN}-pass")),
      "an account it made signs in with its password")

print("\n== a cohort of it is shut out from the registration codes' page ==")
page = admin.get(BASE + PAGE, timeout=20).text
check("Accounts from the retired external door" in page
      and CODE_A in page and CODE_B in page,
      "the page lists both cohorts")
check(post(action="ban-external", code=CODE_A).status_code == 200
      and banned(A_ID) and not banned(B_ID) and not banned(JUMP_ID),
      f"banning {CODE_A} bans its account and no other")
check(not is_in(alpha) and is_in(beta),
      "its session is over, the other cohort's is not")
check(not is_in(sign_in(A_NAME, f"aud-a-{RUN}-pass")),
      "and it cannot sign in again")
check(post(action="unban-external", code=CODE_A).status_code == 200
      and not banned(A_ID), "the same page lets it back")
check(is_in(sign_in(A_NAME, f"aud-a-{RUN}-pass")), "and it signs in")
check(post(action="ban-external", code=f"nobody-{RUN}").status_code == 404,
      "a code nobody carries is a 404")
got = beta.post(BASE + PAGE, timeout=20, allow_redirects=False,
                data={"nonce": nonce(beta, "/"), "action": "ban-external",
                      "code": CODE_A})
check(got.status_code in (302, 403) and not banned(A_ID),
      f"a participant cannot do it: {got.status_code}")

print("\n== with no external account, nothing is filtered or shown ==")
for uid in (A_ID, B_ID):
    api("DELETE", f"/users/{uid}")
    made.remove(uid)
board = names_on_scoreboard(jump)
check(JUMP_NAME in board, f"a plain instance behaves as before: {board}")
check(RUN not in admin.get(BASE + PAGE, timeout=20).text,
      "and the page no longer names either cohort")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
