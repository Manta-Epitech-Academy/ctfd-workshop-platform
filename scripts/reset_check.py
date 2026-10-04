#!/usr/bin/env python3
"""Does « Reset my progress » reset the admin, and only the admin?

    python3 scripts/reset_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §47). It gives the admin and a throwaway participant a solve and a
failed attempt each, presses the button's endpoint as the admin, and checks
the two things that matter: the admin is back to nothing, and the participant
has lost nothing. Then it checks the doors: a participant cannot call the
endpoint, and only an admin is shown the menu entry.

Shaped like scripts/supervisor_check.py — positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It resets the admin account's progress** (that is the feature) and deletes
the participant it creates. Needs an instance with at least one challenge.
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
RESET = "/api/v1/workshop/progress/reset"
fails = []


def check(cond, label):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


def nonce(session, path):
    text = session.get(BASE + path).text
    m = (re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", text)
         or re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', text))
    return m.group(1)


def login(name, password):
    session = requests.Session()
    session.post(BASE + "/login", data={"name": name, "password": password,
                                        "nonce": nonce(session, "/login?staff=1")})
    return session


admin = login("admin", ADMIN_PASS)
head = {"CSRF-Token": nonce(admin, "/admin/config"), "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + path, headers=head, **kw)


me = api("GET", "/api/v1/users/me").json()["data"]["id"]
challenges = api("GET", "/api/v1/challenges?view=admin").json()["data"]
if not challenges:
    sys.exit("this instance has no challenge to solve")
challenge = challenges[0]["id"]

name = "resetcheck" + uuid.uuid4().hex[:8]
password = uuid.uuid4().hex
other = api("POST", "/api/v1/users", json={
    "name": name, "email": name + "@example.com", "password": password}).json()["data"]["id"]
atexit.register(lambda: api("DELETE", f"/api/v1/users/{other}"))


def give(user, kind):
    r = api("POST", "/api/v1/submissions", json={
        "challenge_id": challenge, "user_id": user, "provided": "reset_check", "type": kind})
    return r.status_code == 200


def rows(user):
    solves = api("GET", f"/api/v1/users/{user}/solves").json()["data"]
    fails_ = api("GET", f"/api/v1/users/{user}/fails").json()
    return len(solves), fails_["meta"]["count"]


def workspace(session, keys=None):
    """Read, or write, the session user's saved runtime work."""
    if keys is None:
        return session.get(BASE + "/api/v1/workshop/workspace").json()["data"]
    runtime = workspace(session)["runtime"]
    return session.post(BASE + "/api/v1/workshop/workspace", headers={
        "CSRF-Token": nonce(session, "/workshop"), "Content-Type": "application/json"},
        json={"runtime": runtime, "keys": keys}).status_code == 200


participant = login(name, password)
HAS_RUNTIME = bool(workspace(admin)["runtime"])

print("-- before")
api("POST", RESET)        # from a known state, whatever the admin had done
check(all(give(u, k) for u in (me, other) for k in ("incorrect", "correct")),
      "the admin and a participant each get a failed attempt and a solve")
check(rows(me) == (1, 1), f"admin: 1 solve, 1 fail {rows(me)}")
check(rows(other) == (1, 1), f"participant: 1 solve, 1 fail {rows(other)}")

if HAS_RUNTIME:
    check(workspace(admin, {"reset-check-cart": "admin code"})
          and workspace(participant, {"reset-check-cart": "participant code"}),
          "and each has code saved for the runtime")

print("-- the doors")
r = participant.post(BASE + RESET, headers={
    "CSRF-Token": nonce(participant, "/workshop"), "Content-Type": "application/json"},
    data="{}", allow_redirects=False)
check(r.status_code in (302, 403), f"a participant is refused ({r.status_code})")
check(rows(other) == (1, 1), "and nothing of theirs moved")
check("data-ws-reset" not in participant.get(BASE + "/workshop").text,
      "a participant's account menu has no reset entry")
check("data-ws-reset" in admin.get(BASE + "/workshop").text,
      "an admin's account menu has it")
r = admin.post(BASE + RESET, headers={"Content-Type": "application/json"}, data="{}")
check(r.status_code == 403 and rows(me) == (1, 1), f"without the CSRF nonce nothing happens ({r.status_code})")

print("-- the reset")
r = api("POST", RESET)
data = r.json().get("data", {})
# `attempts` is every submission, right or wrong, so the solve is in it too.
check(r.status_code == 200 and data.get("solves") == 1 and data.get("attempts") == 2,
      f"it reports what it removed: {data}")
check(rows(me) == (0, 0), f"admin: nothing left {rows(me)}")
check(rows(other) == (1, 1), f"participant: untouched {rows(other)}")
if HAS_RUNTIME:
    check(data.get("runtime") == 1 and data.get("runtime_keys") == ["reset-check-cart"],
          "the admin's saved code is removed, and the answer names its keys for the browser")
    check(workspace(admin)["keys"] == {}, "admin: no saved code left")
    check(workspace(participant)["keys"] == {"reset-check-cart": "participant code"},
          "participant: saved code untouched")
r = api("POST", RESET)
check(r.status_code == 200 and not any(r.json()["data"].values()),
      "a second reset finds nothing to remove")

print("ALL GREEN" if not fails else "FAILURES: " + repr(fails))
sys.exit(1 if fails else 0)
