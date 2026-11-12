#!/usr/bin/env python3
"""Is feedback a pick from a list, counted, and tied to nobody?

    python3 scripts/feedback_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §67). It makes two steps of its own (a closing step and an ordinary
one), two registration codes, and one participant in each cohort, then holds
the instance to this:

  * a closing step is completed by any choice from the list, and by nothing
    else: not an empty answer, not a word, not the old thumb-and-comment;
  * a completed step offers the list once, through the plugin's endpoint,
    and refuses a second answer;
  * an answer is **counted**: the report's CSV has one line per step, cohort
    and subject version, a column per choice, and the cohorts do not mix;
  * **no row names anybody**: core's own export of every table is read, the
    counts have no account column, the marker has no choice column, and the
    closing step's solve holds `-` where the choices would be;
  * an admin's answer completes the step and is not counted;
  * core's own rating endpoint, which takes a written review, is refused;
  * the report shows the counts, and follows the room it is scoped to;
  * the page draws the list in French, with no text box.

Shaped like scripts/token_door_check.py: positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back, but for the counts**: the accounts, the codes
and the steps it made are deleted at exit, including on an exception or a
Ctrl-C. The count rows it wrote stay: nothing in the plugin deletes a count.
They are filed under steps that no longer exist, so no page reads them.
"""
import atexit
import csv
import io
import json
import re
import secrets
import sys
import uuid
import zipfile

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
RUN = uuid.uuid4().hex[:6]
PAGE = "/admin/workshop/codes"
DOOR = "/api/v1/workshop/door"
REPORT = "/admin/workshop/feedback"
KEYS = ["great", "good", "too_hard", "too_easy", "unclear_task",
        "unclear_explanation", "statement_error", "not_working"]
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
if admin.get(BASE + PAGE, timeout=20).status_code != 200:
    sys.exit(f"{PAGE} does not answer: wrong password, or an old plugin")
TOKEN = nonce(admin, PAGE)
H = {"CSRF-Token": TOKEN, "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + "/api/v1" + path, headers=H, timeout=30, **kw)


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


CODES = {"A": f"FBA-{RUN}", "B": f"FBB-{RUN}"}
CAMPUS = {"A": f"Avis A {RUN}", "B": f"Avis B {RUN}"}
made_users, made_steps, made_topics = [], [], []
RATINGS = api("GET", "/configs/challenge_ratings").json().get("data", {}).get("value")


def set_ratings(value):
    api("PATCH", "/configs", json={"challenge_ratings": value})


def restore():
    set_ratings(RATINGS or "public")
    for uid in made_users:
        api("DELETE", f"/users/{uid}")
    for cid in made_steps:
        api("DELETE", f"/challenges/{cid}")
    for tid in made_topics:
        api("DELETE", f"/topics/{tid}")
    for code in CODES.values():
        ident = code_id(code)
        if ident:
            codes_page(action="delete", id=ident)


atexit.register(restore)
set_ratings("public")
for room, code in CODES.items():
    if codes_page(action="create", code=code, campus=CAMPUS[room]).status_code != 302:
        sys.exit(f"could not create {code}")
COHORT = {room: "cohort:%s" % code_id(code) for room, code in CODES.items()}


def step(name, kind, slug):
    r = api("POST", "/challenges", json={
        "name": f"{name} {RUN}", "category": f"avis-{RUN}", "description": "probe",
        "value": 0, "state": "visible", "type": "quiz",
        "quiz_type": kind, "quiz_answers": None, "quiz_spec": None})
    if r.status_code != 200:
        sys.exit(f"could not create the {kind} step: {r.status_code} {r.text[:200]}")
    cid = r.json()["data"]["id"]
    made_steps.append(cid)
    if slug:
        # Filed the way the sync files a step (PLAN.md §66).
        for value in (f"ws:{slug}", f"ws-content:avis-{RUN}"):
            t = api("POST", "/topics", json={"value": value, "type": "challenge",
                                             "challenge": cid}).json()["data"]
            made_topics.append(t["topic_id"])
    return cid


CLOSING = step("closing", "rating", f"avis-{RUN}:closing")
READ = step("read", "ack", f"avis-{RUN}:read")
BARE = step("bare", "ack", None)            # made by hand: no topic at all


class Participant:
    """A browser that came in through the token door with a code."""

    def __init__(self, code):
        self.s = requests.Session()
        self.h = {"CSRF-Token": nonce(self.s, "/login"),
                  "Content-Type": "application/json"}
        r = self.s.post(BASE + DOOR + "/register", headers=self.h, timeout=20,
                        json={"code": code, "signin": secrets.token_hex(32)})
        if r.status_code != 200:
            sys.exit(f"could not come in with {code}: {r.status_code}")
        self.h["CSRF-Token"] = nonce(self.s, "/")
        self.id = self.s.get(BASE + "/api/v1/users/me", timeout=20).json()["data"]["id"]
        made_users.append(self.id)


def attempt(who, cid, submission):
    s, h = (admin, H) if who is admin else (who.s, who.h)
    r = s.post(BASE + "/api/v1/challenges/attempt", headers=h, timeout=30,
               json={"challenge_id": cid, "submission": submission})
    return (r.json().get("data") or {}).get("status") if r.status_code == 200 else r.status_code


def send(who, cid, picked):
    s, h = (admin, H) if who is admin else (who.s, who.h)
    return s.post(BASE + f"/api/v1/workshop/feedback/{cid}", headers=h, timeout=30,
                  json={"choices": picked})


def body_of(who, cid):
    s = admin if who is admin else who.s
    r = s.get(BASE + f"/api/v1/workshop/step/{cid}", timeout=30)
    return (r.json().get("data") or {}).get("html", "") if r.status_code == 200 else ""


def lines(**scope):
    """The report's CSV, as dicts, for the steps this run made."""
    r = admin.get(BASE + REPORT + ".csv", timeout=30, params=scope or {"campus": ""})
    return [row for row in csv.DictReader(io.StringIO(r.text)) if RUN in row["step"]]


def line(rows, name, cohort):
    return next((row for row in rows
                 if row["step"].startswith(name) and row["cohort"] == cohort), None)


alice = Participant(CODES["A"])
bob = Participant(CODES["B"])

# ---------------------------------------------------------------------------
print("== a closing step is completed by a choice, and by nothing else ==")
html = body_of(alice, CLOSING)
check(all(f'data-choice="{key}"' in html for key in KEYS),
      "the closing step draws the eight choices")
check("Trop dur" in html and "Ça ne marche pas" in html
      and "J&#39;ai pas compris ce qu&#39;il fallait faire" in html,
      "in French")
check("<input" not in html and "<textarea" not in html and "ws-rating" not in html,
      "with no box to type in and no thumbs")
for sent, why in (("", "an empty answer"), ("lovely", "a word"),
                  ("good,lovely", "a choice next to a word"),
                  ("answered", "the counter's own key"),
                  (json.dumps({"value": 1, "review": "Jean Dupont"}),
                   "the old thumb and comment")):
    check(attempt(alice, CLOSING, sent) == "incorrect", f"{why} does not complete it")
check(lines() == [], "and nothing was counted for any of those")
check(attempt(alice, CLOSING, "good,too_hard") == "correct",
      "two choices complete it")
check(attempt(bob, CLOSING, "not_working") == "correct", "one choice does too")
html = body_of(alice, CLOSING)
check("Merci pour ton" in html and "data-choice" not in html,
      "once sent it says thank you and offers nothing more")

# ---------------------------------------------------------------------------
print("\n== the answer is counted, per cohort ==")
rows = lines()
a, b = line(rows, "closing", COHORT["A"]), line(rows, "closing", COHORT["B"])
check(a is not None and b is not None and len(rows) == 2,
      f"one line per cohort: {[r['cohort'] for r in rows]}")
if a and b:
    check(a["answered"] == "1" and a["good"] == "1" and a["too_hard"] == "1"
          and sum(int(a[k]) for k in KEYS) == 2, "the first cohort's two choices")
    check(b["answered"] == "1" and b["not_working"] == "1"
          and sum(int(b[k]) for k in KEYS) == 1, "the second cohort's one")
    check(a["content"] == f"avis-{RUN}" and a["step_slug"] == f"avis-{RUN}:closing",
          "filed under the content and the step the topics name")
    check(a["kind"] in ("part", "workshop"), f"as a closing step: {a['kind']}")

# ---------------------------------------------------------------------------
print("\n== a completed step offers the list once ==")
check(send(alice, READ, ["too_easy"]).status_code == 403,
      "not before the step is completed")
check(attempt(alice, READ, "read") == "correct" and attempt(bob, BARE, "read") == "correct",
      "the steps are completed")
html = body_of(alice, READ)
check("ws-opinion" in html and "Donner mon avis" in html
      and all(f'data-choice="{key}"' in html for key in KEYS)
      and "<input" not in html, "the step offers « Donner mon avis », folded")
for sent, why in (([], "nothing"), (["lovely"], "a word"), ("too_easy", "a bare string"),
                  (["too_easy", "Jean Dupont"], "a choice next to a name")):
    r = send(alice, READ, sent)
    check(r.status_code == 400, f"{why} is refused: {r.status_code}")
check(body_of(alice, READ).count("ws-opinion") > 0, "and the list is still offered")
check(send(alice, READ, ["too_easy", "unclear_task"]).status_code == 200,
      "two choices are taken")
check(send(alice, READ, ["great"]).status_code == 409, "a second answer is refused")
html = body_of(alice, READ)
check("ws-opinion" not in html and "Merci pour ton" in html,
      "and the step now says thank you")
check(send(alice, CLOSING, ["great"]).status_code == 400,
      "a closing step is not answered through this address")
check(send(bob, BARE, ["statement_error"]).status_code == 200,
      "a step made by hand takes an answer too")
rows = lines()
r = line(rows, "read", COHORT["A"])
check(r is not None and r["answered"] == "1" and r["too_easy"] == "1"
      and r["unclear_task"] == "1" and r["great"] == "0",
      "counted once: the refused second answer left nothing")
r = line(rows, "bare", COHORT["B"])
check(r is not None and r["statement_error"] == "1"
      and r["step_slug"] == f"#{BARE}", "the step made by hand is filed under its id")
anon = requests.Session()
got = anon.post(BASE + f"/api/v1/workshop/feedback/{READ}", timeout=20,
                json={"choices": ["great"]}, allow_redirects=False)
check(got.status_code in (302, 403), f"signed out, nothing is taken: {got.status_code}")

# ---------------------------------------------------------------------------
print("\n== an admin's answer completes the step and is not counted ==")
before = lines()
check(attempt(admin, CLOSING, "great") == "correct", "the admin completes the closing step")
check(attempt(admin, READ, "read") == "correct"
      and send(admin, READ, ["not_working"]).status_code == 200,
      "and answers on the other")
check(lines() == before, "the counts did not move")

# ---------------------------------------------------------------------------
print("\n== core's rating, which takes a written review, is refused ==")
r = alice.s.put(BASE + f"/api/v1/challenges/{READ}/ratings", headers=alice.h, timeout=20,
                json={"value": 1, "review": f"Jean Dupont {RUN}"})
check(r.status_code == 403, f"a participant cannot write one: {r.status_code}")
detail = alice.s.get(BASE + f"/api/v1/challenges/{READ}", timeout=20).json()["data"]
check(not (detail.get("rating") or {}).get("review"), "and none was stored")

# ---------------------------------------------------------------------------
print("\n== no row names anybody ==")
export = admin.get(BASE + "/admin/export", timeout=300)
archive = zipfile.ZipFile(io.BytesIO(export.content))
tables = {}
for n in archive.namelist():
    if n.startswith("db/"):
        text = archive.read(n).decode("utf-8", "replace")
        tables[n] = json.loads(text) if text.strip() else {}


def rows_of(name):
    return (tables.get(f"db/{name}.json") or {}).get("results") or []


counts = [r for r in rows_of("workshop_step_feedback") if RUN in r["content"]
          or r["step"] in (f"#{BARE}",)]
check(len(counts) > 0 and all(
    sorted(r) == ["choice", "cohort", "content", "count", "id", "step", "version"]
    for r in counts),
      "a count is a cohort, a content, a step, a version, a choice and a number")
check(all(r["choice"] in KEYS + ["answered"] for r in counts),
      "every choice in the table is a key of the list")
given = [r for r in rows_of("workshop_feedback_given")
         if r["challenge_id"] in (READ, BARE)]
check(len(given) == 3 and all(sorted(r) == ["challenge_id", "user_id"] for r in given),
      "the marker is an account and a step, and holds no choice")
solves = [r for r in rows_of("submissions") if r["challenge_id"] == CLOSING
          and r["type"] == "correct"]
check(len(solves) == 3 and all(r["provided"] == "-" for r in solves),
      "the closing step's solves hold `-`, not the choices")
everything = json.dumps(tables)
check("Jean Dupont" not in everything, "nothing the refused requests typed was kept")

# ---------------------------------------------------------------------------
print("\n== the report shows the counts, and follows the room ==")
page = admin.get(BASE + REPORT, timeout=30, params={"campus": ""}).text
check(page.count(f"closing {RUN}") >= 1 and f"read {RUN}" in page,
      "the whole instance: both steps are on the page")
block = page.split(f"read {RUN}", 1)[-1][:1200]
check('data-choice="too_easy"' in block and "Too easy" in block,
      "a step lists what was picked, in English")
check("<textarea" not in page and "Jean Dupont" not in page, "and no written word")
ref = {room: "@" + CAMPUS[room].casefold() for room in CAMPUS}
rows = lines(campus=ref["A"])
check({r["cohort"] for r in rows} == {COHORT["A"]},
      f"scoped to the first campus, the CSV holds its cohort only: "
      f"{sorted({r['cohort'] for r in rows})}")
page = admin.get(BASE + REPORT, timeout=30, params={"campus": ref["B"]}).text
check(f"bare {RUN}" in page and 'data-choice="statement_error"' in page
      and 'data-choice="too_easy"' not in page,
      "scoped to the second, the page shows its picks and not the first's")
admin.get(BASE + REPORT, timeout=30, params={"campus": ""})

# ---------------------------------------------------------------------------
print("\n== CTFd's switch for ratings turns the list off on a completed step ==")
set_ratings("disabled")
check(attempt(bob, READ, "read") == "correct", "a step is completed")
check("ws-opinion" not in body_of(bob, READ), "it does not offer the list")
check(send(bob, READ, ["great"]).status_code == 403, "and the address refuses")
set_ratings("public")
check("ws-opinion" in body_of(bob, READ), "switched back on, the list is offered")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
