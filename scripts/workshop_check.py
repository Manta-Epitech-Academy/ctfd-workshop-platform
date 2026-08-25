#!/usr/bin/env python3
"""Does a multi-subject workshop behave like one workshop (PLAN.md §19)?

    python3 scripts/workshop_check.py [base-url]

Run it against an instance a workshop has been synced into:

    docker compose --env-file compose/validate.env -p ctfd-validate up -d
    # setup wizard, then
    python3 tools/sync_workshop.py content/workshops/<name> --url … --admin-pass …
    python3 scripts/workshop_check.py http://localhost:8082

Nothing here names a subject: the expectations are read from the instance, so
the same checks cover `tic80-double` (PyPong then Santa Shooter) and
`discover-linux` (Shell RPG then Shell 1) without editing.

Separate from scripts/phase2_validate.py because that suite owns a
single-subject instance from the wizard onwards, and this needs a composed one.
"""
import json
import re
import sys
import uuid

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8082"
fails = []
# A fresh account per run: these checks look at what a participant who has done
# nothing sees, so re-running must not inherit the previous run's progress.
RUN = uuid.uuid4().hex[:6]


def check(cond, label):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        fails.append(label)


def nonce(s, p="/"):
    r = s.get(BASE + p)
    return (re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text)
            or re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", r.text)).group(1)


def api(s, method, path, **kw):
    return s.request(method, BASE + "/api/v1" + path,
                     headers={"CSRF-Token": s.nonce,
                              "Content-Type": "application/json"}, **kw)


def admin():
    s = requests.Session()
    n = nonce(s, "/login")
    s.post(BASE + "/login", data={"name": "admin", "password": "adminpass",
                                  "nonce": n, "_submit": "Submit"})
    s.nonce = nonce(s)
    return s


def participant(name):
    s = requests.Session()
    n = nonce(s, "/register")
    s.post(BASE + "/register", data={"name": f"{name}{RUN}",
                                     "email": f"{name}{RUN}@example.com",
                                     "password": f"{name}-pass", "nonce": n,
                                     "_submit": "Submit"})
    landing = s.get(BASE + "/workshop", allow_redirects=True)
    if "/login" in landing.url or "/register" in landing.url:
        sys.exit(f"could not register {name}{RUN} — is registration open?")
    s.nonce = nonce(s)
    return s


a = admin()
board = api(a, "GET", "/challenges?view=admin").json()["data"]
topics = {}
for c in board:
    for t in api(a, "GET", f"/challenges/{c['id']}/topics").json()["data"]:
        if t["value"].startswith("ws:"):
            _, subject, slug = t["value"].split(":", 2)
            topics[c["id"]] = (subject, slug)

cfg = lambda k: api(a, "GET", f"/configs/{k}").json()["data"]["value"]
docs = json.loads(cfg("workshop_documents"))
# Subject order as the *sync* recorded it: the manifest's order, starter first.
order = list(dict.fromkeys(d["subject"] for d in docs))
if len(order) < 2:
    sys.exit("this instance holds one subject — nothing to check (sync a workshop first)")
print(f"workshop of {len(order)} subjects: {' -> '.join(order)}\n")

print("== the board is one workshop, in order ==")
ordered = sorted(board, key=lambda c: c["position"])
runs = []
for c in ordered:
    subj = topics.get(c["id"], ("?",))[0]
    if not runs or runs[-1] != subj:
        runs.append(subj)
check(runs == order, f"each subject's steps are contiguous and in manifest order ({runs})")
positions = [c["position"] for c in ordered]
check(len(set(positions)) == len(positions), "no two steps share a board position")

print("== each subject waits for the one before it ==")
gate = participant("gate")
nodes = {n["id"]: n for n in api(gate, "GET", "/workshop/graph").json()["data"]["nodes"]}
intro_of = {s: cid for cid, (s, slug) in topics.items() if slug == "__intro__"}
finals = {}
for cid, (s, slug) in topics.items():
    if slug.startswith("__outro__"):
        finals.setdefault(s, []).append(cid)
for previous, subject in zip(order, order[1:]):
    intro = intro_of[subject]
    expected = max(finals[previous])          # the previous subject's last close
    # CTFd's challenge detail endpoint does not expose `requirements` at all —
    # even where gating demonstrably works — so this goes through the graph.
    check(nodes[intro]["prerequisites"] == [expected],
          f"{subject}'s intro requires {previous}'s closing step, and nothing else")
    check(not nodes[intro]["unlocked"],
          f"so a fresh participant cannot open {subject}")

print("== the instance-wide settings describe the whole workshop ==")
check(len({d["route"] for d in docs}) == len(docs), "part routes do not collide")
check(all(topics[c][0] in order for c in json.loads(cfg("workshop_optional")) if c in topics),
      "optional ids survive from the subjects that declared them")
check(all(topics[c][0] in order for c in json.loads(cfg("workshop_free")) if c in topics),
      "free ids too")
final = int(cfg("workshop_final_step"))
check(topics.get(final, ("?",))[0] == order[-1],
      "the workshop closes on the LAST subject's closing step")

runtime = json.loads(cfg("workshop_runtime") or "{}")
if runtime.get("subjects"):
    check(set(runtime["subjects"]) <= set(order),
          "runtime parameters are keyed by subject")
    for subject, params in runtime["subjects"].items():
        page = [d for d in docs if d["subject"] == subject][0]
        html = gate.get(BASE + "/" + page["route"]).text
        wanted = params.get("bundle_url", "")
        check(not wanted or wanted in html,
              f"{subject}'s pages hand the frame its own runtime parameters")

print("== a participant sees one workshop ==")
p = participant("eleve")
index = p.get(BASE + "/workshop")
check(index.status_code == 200, "the index renders")
for d in docs:
    check(f'href="/{d["route"]}"' in index.text, f"the index links to /{d['route']}")
first = p.get(BASE + "/" + docs[0]["route"])
check('data-state="current"' in first.text, "the starter opens on a current step")
last = p.get(BASE + "/" + [d for d in docs if d["subject"] == order[-1]][0]["route"])
states = set(re.findall(r'data-state="(\w+)"', last.text))
check(states == {"locked"}, f"every step of the last subject is locked ({states})")

print("\n" + ("ALL GREEN" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
