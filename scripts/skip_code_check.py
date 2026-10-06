#!/usr/bin/env python3
"""Can an admin skip a validation code, and nobody else?

    python3 scripts/skip_code_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §49). It needs an instructor-led instance with at least one step
validated by the instructor's code, and it checks the two halves: the button
is shown to an admin only, and the server honours the flag for an admin only.
The second half is the one that matters, because a button that is merely
hidden is not a permission.

Shaped like scripts/supervisor_check.py — positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It resets the admin account's progress** at the start and at the end, and
deletes the participant it creates.
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
ATTEMPT = "/api/v1/challenges/attempt"
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


def attempt(session, challenge, submission, **extra):
    r = session.post(BASE + ATTEMPT, headers={
        "CSRF-Token": nonce(session, "/workshop"), "Content-Type": "application/json"},
        json={"challenge_id": challenge, "submission": submission, **extra})
    return (r.json().get("data") or {}).get("status")


admin = login("admin", ADMIN_PASS)
head = {"CSRF-Token": nonce(admin, "/admin/config"), "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + path, headers=head, **kw)


mode = (api("GET", "/api/v1/configs/workshop_mode").json().get("data") or {}).get("value")
if mode == "self_serve":
    sys.exit("this instance is self-serve: there is no code to skip")
me = api("GET", "/api/v1/users/me").json()["data"]["id"]

# The first step validated by a code, and what has to be solved to reach it.
target, before = None, []
for c in api("GET", "/api/v1/challenges?view=admin").json()["data"]:
    full = api("GET", f"/api/v1/challenges/{c['id']}?view=admin").json()["data"]
    if full.get("type") == "quiz" and full.get("quiz_type") == "checkpoint":
        target = c["id"]
        before = (api("GET", f"/api/v1/challenges/{c['id']}/requirements").json()
                  .get("data") or {}).get("prerequisites") or []
        break
if target is None:
    sys.exit("this instance has no step validated by a code")

name = "skipcheck" + uuid.uuid4().hex[:8]
password = uuid.uuid4().hex
other = api("POST", "/api/v1/users", json={
    "name": name, "email": name + "@example.com", "password": password}).json()["data"]["id"]
atexit.register(lambda: api("DELETE", f"/api/v1/users/{other}"))
atexit.register(lambda: api("POST", "/api/v1/workshop/progress/reset"))
api("POST", "/api/v1/workshop/progress/reset")
for user in (me, other):
    for challenge in before:
        api("POST", "/api/v1/submissions", json={"challenge_id": challenge, "user_id": user,
                                                 "provided": "skip_check", "type": "correct"})
participant = login(name, password)


def solved(user):
    return target in [s["challenge_id"] for s in api("GET", f"/api/v1/users/{user}/solves").json()["data"]]


print("-- the button")
page = admin.get(BASE + "/workshop").text
check("ws-skip-code" in page, "an admin's code step carries « Skip validation code »")
check(page.count('placeholder="Validation code"') + page.count("Code de validation") > 0
      or 'data-answer-kind="code"' in page, "beside the field a participant gets")
theirs = participant.get(BASE + "/workshop").text
check('data-answer-kind="code"' in theirs and "ws-skip-code" not in theirs,
      "a participant gets the same step, without the button")

print("-- the server")
check(attempt(participant, target, "x", skip_code=True) == "incorrect" and not solved(other),
      "a participant who sends skip_code is refused, and has no solve")
check(attempt(admin, target, "not-the-code") == "incorrect" and not solved(me),
      "an admin without the flag still needs the code")
check(attempt(admin, target, "x", skip_code="true") == "incorrect" and not solved(me),
      "the flag is a JSON true, not any truthy string")
check(attempt(admin, target, "(admin: validation code skipped)", skip_code=True) == "correct" and solved(me),
      "an admin who sends skip_code is solved")
rows = api("GET", f"/api/v1/submissions?challenge_id={target}&user_id={me}&type=correct").json()["data"]
check(any("skipped" in (r.get("provided") or "") for r in rows),
      "and the submission records that the code was skipped")
check(not solved(other), "the participant is still where they were")

print("ALL GREEN" if not fails else "FAILURES: " + repr(fails))
sys.exit(1 if fails else 0)
