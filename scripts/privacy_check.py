#!/usr/bin/env python3
"""Does the instance keep an address, or a wrong answer? It should keep neither.

    python3 scripts/privacy_check.py <base-url> <admin-pass> [log-folder]

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §65). It makes one participant and three steps of its own (a free
text, a single choice, a set of two choices), has the participant browse, fail
and solve, and then reads back what the instance holds:

  - no submission carries an address, right or wrong, and the account's
    address list in the admin panel is empty;
  - a wrong typed answer is stored as its distance to the right one (`~2`),
    never as the text, also when the attempt was refused for coming too fast;
  - a chosen option is stored as chosen, and a right answer as given;
  - a step validated by the instructor's code keeps a distance too. That
    needs an instructor-led instance, so the mode is switched for that part
    and put back.

With a log folder (CTFd's `LOG_FOLDER`, readable from where this runs), what
the run added to `submissions.log` and `logins.log` is read too: no address,
and none of the submitted text.

Shaped like scripts/supervisor_check.py: positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back**: the account, the steps and the mode, at exit,
including on an exception or a Ctrl-C.
"""
import atexit
import functools
import os
import re
import sys
import uuid

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
LOGS = sys.argv[3] if len(sys.argv) > 3 else None
RUN = uuid.uuid4().hex[:6]
ATTEMPT = "/api/v1/challenges/attempt"
# Something no log line or row should ever hold again.
SECRET = f"secret{RUN}"
ADDRESS = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b|\b[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){3,}\b", re.I)
fails = []


@functools.lru_cache(maxsize=None)
def distance(a, b):
    """Levenshtein, written a second time on purpose: the script must not
    borrow the code it checks."""
    if not a or not b:
        return len(a) + len(b)
    return min(distance(a[1:], b) + 1, distance(a, b[1:]) + 1,
               distance(a[1:], b[1:]) + (a[0] != b[0]))


def check(cond, label):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


def nonce(s, path="/"):
    r = s.get(BASE + path, timeout=20)
    m = (re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text)
         or re.search(r"['\"]csrfNonce['\"]\s*:\s*['\"]([0-9a-f]+)", r.text))
    return m.group(1) if m else None


def sign_in(name, password):
    s = requests.Session()
    s.post(BASE + "/login", timeout=20,
           data={"name": name, "password": password,
                 "nonce": nonce(s, "/login?staff=1"), "_submit": "Submit"})
    return s


admin = sign_in("admin", ADMIN_PASS)
TOKEN = nonce(admin, "/admin/config")
if TOKEN is None:
    sys.exit("not an admin session")
H = {"CSRF-Token": TOKEN, "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + "/api/v1" + path, headers=H, timeout=20, **kw)


def log_size(name):
    path = os.path.join(LOGS, name) if LOGS else None
    return os.path.getsize(path) if path and os.path.exists(path) else 0


def log_since(name, start):
    with open(os.path.join(LOGS, name), errors="replace") as f:
        f.seek(start)
        return f.read()


before = {name: log_size(name) for name in ("submissions.log", "logins.log")}
MODE = (api("GET", "/configs/workshop_mode").json().get("data") or {}).get("value")
made_steps, made_users = [], []


def set_mode(value):
    if value is None:
        api("DELETE", "/configs/workshop_mode")
    else:
        api("PATCH", "/configs", json={"workshop_mode": value})


def restore():
    for cid in made_steps:
        api("DELETE", f"/challenges/{cid}")
    for uid in made_users:
        api("DELETE", f"/users/{uid}")
    set_mode(MODE)


atexit.register(restore)


def step(name, kind, answers, spec=None):
    r = api("POST", "/challenges", json={
        "name": f"{name} {RUN}", "category": f"probe-{RUN}", "description": "probe",
        "value": 0, "state": "visible", "type": "quiz",
        "quiz_type": kind, "quiz_answers": answers, "quiz_spec": spec})
    if r.status_code != 200:
        sys.exit(f"could not create the {kind} step: {r.status_code} {r.text[:200]}")
    made_steps.append(r.json()["data"]["id"])
    return made_steps[-1]


TEXT = step("text", "freeform", {"patterns": ["^(ritchie|thompson)$"]})
CHOICE = step("choice", "single", {"answer": "B"},
              {"prompt": "probe", "options": {"A": "a", "B": "b"}})
SET = step("set", "quizset", {"questions": [
    {"kind": "single", "answer": "B"}, {"kind": "multiple", "answers": ["A", "C"]}]})
CODE = step("code", "checkpoint", {"code": "4b279a"})

NAME, PASSWORD = f"priv-{RUN}", f"priv-{RUN}-pass"
r = api("POST", "/users", json={"name": NAME, "email": f"{NAME}@example.invalid",
                                "password": PASSWORD, "type": "user", "verified": True})
USER = r.json()["data"]["id"]
made_users.append(USER)
# An address of its own, the way a proxy hands one over. Whether CTFd trusts
# the header depends on its REVERSE_PROXY setting; either way some address
# reaches it, and none should be kept.
user = sign_in(NAME, PASSWORD)
user.headers["X-Forwarded-For"] = "203.0.113.77"


def attempt(cid, submission):
    r = user.post(BASE + ATTEMPT, timeout=20,
                  headers={"CSRF-Token": nonce(user, "/workshop"),
                           "Content-Type": "application/json"},
                  json={"challenge_id": cid, "submission": submission})
    return (r.json().get("data") or {}).get("status")


def rows(cid=None):
    """This account's submissions, oldest first, as the admin API gives them."""
    found, page = [], 1
    while True:
        query = f"/submissions?user_id={USER}&page={page}"
        if cid:
            query += f"&challenge_id={cid}"
        body = api("GET", query).json()
        found += body.get("data") or []
        if not (body.get("meta") or {}).get("pagination", {}).get("next"):
            break
        page += 1
    return sorted(found, key=lambda row: row["id"])


print("== browsing ==")
for path in ("/workshop", "/challenges", "/scoreboard", "/workshop"):
    user.get(BASE + path, timeout=20)
user.get(BASE + f"/api/v1/challenges/{TEXT}", timeout=20)
page = admin.get(BASE + f"/admin/users/{USER}", timeout=20)
check(page.status_code == 200, f"the account's admin page renders: {page.status_code}")
check("203.0.113.77" not in page.text and not re.search(
    r"field=ip&(?:amp;)?q=\d", page.text),
    "and lists no address for it")

print("\n== a wrong typed answer ==")
check(attempt(TEXT, f"Ritchi {SECRET}") == "incorrect", "a wrong text is refused")
check(attempt(TEXT, "  Ritchi ") == "incorrect", "and a near miss")
check(attempt(TEXT, "THOMPSONN") == "incorrect", "and a near miss of the other answer")
kept = [row["provided"] for row in rows(TEXT)]
far = min(distance(f"ritchi {SECRET}", answer) for answer in ("ritchie", "thompson"))
check(kept == [f"~{far}", "~1", "~1"],
      f"each is kept as its distance to the nearest answer: {kept}")

print("\n== a choice ==")
check(attempt(CHOICE, "A") == "incorrect", "a wrong option is refused")
check([row["provided"] for row in rows(CHOICE)] == ["A"],
      "and kept as chosen")
check(attempt(SET, "A|A,C") == "incorrect", "a set with one wrong question is refused")
check([row["provided"] for row in rows(SET)] == ["A|A,C"],
      "and kept as chosen, question by question")
check(attempt(CHOICE, f"A {SECRET}") == "incorrect", "a text sent where a choice goes is refused")
check(rows(CHOICE)[-1]["provided"] == "~?", "and not kept at all")
page = user.get(BASE + "/workshop", timeout=20)
check(page.status_code == 200, f"the workshop page still renders: {page.status_code}")

print("\n== a right answer ==")
check(attempt(TEXT, "Ritchie") == "correct", "the right text is accepted")
last = rows(TEXT)[-1]
check(last["type"] == "correct" and last["provided"] == "Ritchie",
      f"and kept as given: {last['provided']!r}")
check(attempt(CHOICE, "B") == "correct", "the right option too")

print("\n== the instructor's code ==")
set_mode("instructor_led")
check(attempt(CODE, f"4b279b {SECRET}") == "incorrect", "a wrong code is refused")
check(attempt(CODE, "4B279B") == "incorrect", "and a near miss")
kept = [row["provided"] for row in rows(CODE)]
far = distance(f"4b279b {SECRET}", "4b279a")
check(kept == [f"~{far}", "~1"], f"both kept as a distance: {kept}")
check(attempt(CODE, "4B279A") == "correct", "the code is accepted, in any case")
set_mode(MODE)

print("\n== refused for coming too fast ==")
# Core refuses past ten wrong answers a minute, and stores the attempt.
statuses = [attempt(SET, f"{SECRET}|{i}") for i in range(12)]
check("ratelimited" in statuses, f"the limit is reached: {statuses[-3:]}")
limited = [row for row in rows() if row["type"] == "ratelimited"]
check(limited and all(SECRET not in (row["provided"] or "") for row in limited),
      f"what it stores has no text: {[row['provided'] for row in limited][:3]}")

print("\n== nothing kept that should not be ==")
everything = rows()
check(len(everything) >= 12 and all(not row.get("ip") for row in everything),
      f"none of the {len(everything)} submissions carries an address")
check(all(SECRET not in (row["provided"] or "") for row in everything),
      "none carries the typed text")
page = admin.get(BASE + f"/admin/users/{USER}", timeout=20)
check("203.0.113.77" not in page.text and not re.search(
    r"field=ip&(?:amp;)?q=\d", page.text),
    "the account's address list is still empty")
page = admin.get(BASE + f"/admin/workshop/submissions?q={NAME}", timeout=20)
check(page.status_code == 200 and "ws-sb-distance-note" in page.text
      and "~1" in page.text and SECRET not in page.text,
      "the staff's page shows the distances and says what they are")

if LOGS:
    print("\n== the log files ==")
    sub = log_since("submissions.log", before["submissions.log"])
    mine = [line for line in sub.splitlines() if NAME in line]
    check(len(mine) >= 12, f"the attempts were logged: {len(mine)} lines")
    check(SECRET not in sub and "Ritchi" not in sub and "4b279" not in sub.lower(),
          "with none of the submitted text, right or wrong")
    check(all(" submitted - on " in line for line in mine),
          f"each line reads like: {mine[0] if mine else None}")
    logins = log_since("logins.log", before["logins.log"])
    check(NAME in logins, "the sign-in was logged")
    check(not ADDRESS.search(sub) and not ADDRESS.search(logins),
          "and no line of either file holds an address")
else:
    print("\n(no log folder given: the log files were not read)")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
