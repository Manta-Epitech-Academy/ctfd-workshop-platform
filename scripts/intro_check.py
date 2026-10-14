#!/usr/bin/env python3
"""Do hidden introductions disappear everywhere, count nowhere, and gate nothing?

    python3 scripts/intro_check.py <base-url> <admin-pass> <instructor-codes.yaml>

The plugin has no test suite, so this script **is** this feature's test suite
(plugins/workshop/intro.py). Against an instance holding one subject with a
`checkpoint` first step (Pacman is what it was written on), with the codes
file the sync wrote:

  hidden    the default, config unset: the index shows no introduction, the
            counters leave it out, the graph has no node for it, part 1 opens
            at once, its glossary is on the toolbox page from the start, and
            an answer to the first step is accepted by CTFd core — which
            enforces prerequisites itself — because the acknowledgement was
            written for the participant. The Jump outbox never hears of it.
  shown     unticked on /admin/workshop/settings: everything is back — the
            section on the index, one more step in the counters, the gate on
            part 1, core refusing the first answer with a 403.
  hidden    ticked again: a participant who met the gate while it was shown
            is where they were, and a fresh one is not gated.

Then the sign-in button: gone from the header while Jump is the only way in
(PLAN.md §53), back when the instance lets people register.

Shaped like scripts/toggle_check.py — positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.
**It puts the instance back**: every config it touches is restored and
every account it creates is deleted, on exit or on an exception.
"""
import atexit
import json
import re
import sys
import uuid

import requests
import yaml

if len(sys.argv) < 4:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
CODES = yaml.safe_load(open(sys.argv[3]))
SETTINGS = "/admin/workshop/settings"
RUN = uuid.uuid4().hex[:6]
fails = []


def check(cond, label):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


def nonce(session, path="/"):
    r = session.get(BASE + path, timeout=20)
    m = (re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", r.text)
         or re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text))
    if not m:
        sys.exit(f"no CSRF nonce on {path} (HTTP {r.status_code})")
    return m.group(1)


class Client:
    def __init__(self, name, password):
        self.s = requests.Session()
        self.s.post(BASE + "/login?staff=1", timeout=20, data={
            "name": name, "password": password,
            "nonce": nonce(self.s, "/login?staff=1"), "_submit": "Submit"})
        self.nonce = nonce(self.s)

    def get(self, path, **kw):
        return self.s.get(BASE + path, timeout=20, **kw)

    def api(self, method, path, **kw):
        return self.s.request(method, BASE + "/api/v1" + path, timeout=30,
                              headers={"CSRF-Token": self.nonce,
                                       "Content-Type": "application/json"}, **kw)

    # -- participant-side readings ------------------------------------
    def index(self):
        return self.get("/workshop", allow_redirects=True).text

    def total(self):
        m = re.search(r'class="ws-overall"\s+data-solved="(\d+)"\s+data-total="(\d+)"',
                      self.index())
        return int(m.group(2)) if m else None

    def graph(self):
        return self.api("GET", "/workshop/graph").json()["data"]

    def first_state(self, doc):
        html = self.get(f"/workshop/{doc}").text
        m = re.search(r'class="ws-step ws-(\w+)" id="step-(\d+)"', html)
        return (m.group(1), int(m.group(2))) if m else (None, None)

    def attempt(self, challenge_id, submission):
        r = self.api("POST", "/challenges/attempt",
                     json={"challenge_id": challenge_id, "submission": submission})
        return r.status_code, (r.json().get("data") or {}).get("status") if r.status_code == 200 else None


class Admin(Client):
    def __init__(self):
        super().__init__("admin", ADMIN_PASS)
        if self.api("GET", "/configs/ctf_name").status_code != 200:
            sys.exit("could not sign in as admin — wrong password?")

    def config(self, key):
        r = self.api("GET", f"/configs/{key}")
        return (r.json().get("data") or {}).get("value") if r.status_code == 200 else None

    def set_configs(self, values):
        r = self.api("PATCH", "/configs", json=values)
        if r.status_code != 200:
            sys.exit(f"could not write configs: HTTP {r.status_code} {r.text[:200]}")

    def settings(self, *, intro_hidden):
        data = {"nonce": nonce(self.s, SETTINGS),
                "workshop_mode": self.config("workshop_mode") or "instructor_led"}
        if intro_hidden:
            data["intro_hidden"] = "on"
        return self.s.post(BASE + SETTINGS, timeout=20, data=data)

    def create_user(self, tag):
        password = uuid.uuid4().hex
        name = f"Intro {tag} {RUN}"
        r = self.api("POST", "/users", json={
            "name": name, "email": f"intro-{tag}-{RUN}@example.invalid",
            "password": password, "verified": True})
        if r.status_code != 200:
            sys.exit(f"could not create a participant: {r.status_code} {r.text[:200]}")
        return r.json()["data"]["id"], name, password

    def topics(self, challenge_id):
        return [t["value"] for t in self.api("GET", f"/challenges/{challenge_id}/topics").json()["data"]]

    def solves_of(self, user_id):
        return {s["challenge_id"] for s in self.api("GET", f"/users/{user_id}/solves").json()["data"]}

    def outbox(self):
        r = self.api("GET", "/workshop/jump/events")
        return r.json()["data"] if r.status_code == 200 else []


admin = Admin()
KEYS = ("workshop_intro_hidden", "workshop_mode", "workshop_jump_keys",
        "workshop_jump_instance", "registration_visibility")
saved = {k: admin.config(k) for k in KEYS}
created = []


def restore():
    admin.set_configs({k: ("" if v is None else v) for k, v in saved.items()})
    for uid in created:
        admin.api("DELETE", f"/users/{uid}")


atexit.register(restore)

# -- the board ------------------------------------------------------------
challenges = admin.api("GET", "/challenges?view=admin").json()["data"]
by_id = {c["id"]: c for c in challenges}
intro_ids = [c["id"] for c in challenges
             if any(t.endswith(":__intro__") for t in admin.topics(c["id"]))]
check(len(intro_ids) == 1, f"one introduction step on this instance: {intro_ids}")
INTRO = intro_ids[0]
slug_of = {c["id"]: next((t.split(":", 2)[2] for t in admin.topics(c["id"])
                          if t.startswith("ws:")), None) for c in challenges}
docs = json.loads(admin.config("workshop_documents") or "[]")
PART1 = docs[0]["slug"]
first_id = next(cid for cid in docs[0]["challenge_ids"] if cid != INTRO)
# A code when the first step is a checkpoint; otherwise a wrong answer, which
# is enough: core grades it (200, "incorrect") only once the gate is passed,
# and refuses it (403) before.
first_answer = CODES.get(slug_of[first_id]) or "not-the-answer"
GRADED = ("correct", "incorrect", "partial")
print(f"first step of part 1: {slug_of[first_id]} ({first_answer!r})")


def fresh(tag):
    uid, name, password = admin.create_user(tag)
    created.append(uid)
    return uid, Client(name, password)


# ---------------------------------------------------------------- hidden --
print("== hidden by default (config empty, as unset) ==")
# Empty is how a cleared config is stored (CTFd's API refuses to delete one),
# and it must read as unset, which is hidden. The exit puts back what was here.
admin.set_configs({"workshop_intro_hidden": ""})
check('id="ws-intro-hidden"' in admin.get(SETTINGS).text
      and re.search(r'id="ws-intro-hidden"[^>]*checked', admin.get(SETTINGS).text),
      "the settings page shows the box ticked")
p1_id, p1 = fresh("hidden")
html = p1.index()
check("ws-index-intro" not in html, "the index shows no introduction section")
T_HIDDEN = p1.total()
check(T_HIDDEN is not None, f"the index counts {T_HIDDEN} steps")
g = p1.graph()
nodes = {n["id"]: n for n in g["nodes"]}
check(INTRO not in nodes, "the graph has no node for the introduction")
check(nodes[first_id]["prerequisites"] == [], f"the first step waits on nothing: {nodes[first_id]['prerequisites']}")
check(INTRO not in g["solved"], "the graph does not list the introduction as solved")
state, sid = p1.first_state(PART1)
check(state == "current" and sid == first_id, f"part 1 opens on its first step ({state}, {sid})")
tb = p1.get("/toolbox").text
check("map.isWall" in tb, "the introduction's glossary is on the toolbox page, open")
check(f'id="glossary-{INTRO}"' in tb and "Unlocks with this step" not in tb.split(f'id="glossary-{INTRO}"', 1)[1][:400],
      "and it is not marked as locked")
code, status = p1.attempt(first_id, first_answer)
check(code == 200 and status in GRADED,
      f"CTFd core grades the first answer: the gate was passed for them ({code} {status})")
check(INTRO in admin.solves_of(p1_id), "an acknowledgement was written on the participant's behalf")
check(not [e for e in admin.outbox() if e.get("challenge_id") == INTRO],
      "and the Jump outbox never heard of it")
check(p1.total() == T_HIDDEN, f"the index still counts {T_HIDDEN} steps after that attempt")

# ----------------------------------------------------------------- shown --
print("== introductions shown (unticked) ==")
r = admin.settings(intro_hidden=False)
check(r.status_code == 200 and admin.config("workshop_intro_hidden") in (False, "false"),
      f"unticking writes the config ({admin.config('workshop_intro_hidden')!r})")
p2_id, p2 = fresh("shown")
html = p2.index()
check("ws-index-intro" in html, "the index shows the introduction section")
check(p2.total() == T_HIDDEN + 1, f"the counters gain one step ({p2.total()})")
nodes = {n["id"]: n for n in p2.graph()["nodes"]}
check(INTRO in nodes and nodes[first_id]["prerequisites"] == [INTRO],
      "the graph has the introduction, gating the first step")
state, sid = p2.first_state(PART1)
check(state == "locked", f"part 1's first step is locked ({state})")
code, status = p2.attempt(first_id, first_answer)
check(code == 403, f"CTFd core refuses the first answer before the acknowledgement ({code})")
check(f'id="step-{INTRO}"' in p1.index() and f'class="ws-step ws-done" id="step-{INTRO}"' in p1.index(),
      "the participant acknowledged on their behalf reads as done")

# ---------------------------------------------------------------- hidden --
print("== hidden again (ticked) ==")
admin.settings(intro_hidden=True)
check("ws-index-intro" not in p2.index() and p2.total() == T_HIDDEN,
      f"the fresh participant's index is back to {T_HIDDEN} steps, no introduction")
state, sid = p2.first_state(PART1)
check(state == "current", f"and part 1 opens for them ({state})")
code, status = p2.attempt(first_id, first_answer)
check(code == 200 and status in GRADED, f"and their first answer is graded ({code} {status})")
check(p1.total() == T_HIDDEN, f"the earlier participant still counts {T_HIDDEN} steps")
check(admin.get("/admin/workshop/answers").status_code == 200
      and admin.get("/admin/workshop/stats").status_code == 200,
      "the Answers and Stats pages render")

# ------------------------------------------------------ the sign-in button --
print("== the header's sign-in button goes when Jump is the only way in (§53) ==")
admin.set_configs({"workshop_jump_keys": json.dumps(
    {"ka": {"origin": "https://jump.introcheck.invalid", "secret": "s" * 32, "label": "A"}}),
    "workshop_jump_instance": "introcheck", "registration_visibility": "private"})
anon = requests.Session()
r = anon.get(BASE + "/", allow_redirects=False, timeout=20)
check(r.status_code == 302 and "introcheck.invalid" in r.headers.get("Location", ""),
      "/ sends an anonymous visitor to Jump")
page = anon.get(BASE + "/scoreboard", timeout=20).text
check("epi-cta-on-band" not in page.split("</header>", 1)[0],
      "the header carries no sign-in button")
check('name="password"' in anon.get(BASE + "/login?staff=1", timeout=20).text,
      "the staff form is still at /login?staff=1")
admin.set_configs({"registration_visibility": "public"})
page = anon.get(BASE + "/scoreboard", timeout=20).text
check("epi-cta-on-band" in page.split("</header>", 1)[0],
      "with registration open, the sign-in button is back")

print()
print("ALL GREEN" if not fails else f"FAILURES: {fails}")
sys.exit(1 if fails else 0)
