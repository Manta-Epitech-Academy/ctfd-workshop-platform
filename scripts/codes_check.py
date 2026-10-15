#!/usr/bin/env python3
"""Do the codes a subject chooses win, and does everything else stay put?

    python3 scripts/codes_check.py <base-url> <admin-pass> <subject-dir>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §56). Against an instance that already holds <subject-dir>, synced,
with at least two `checkpoint` exercises (Pacman is what it was written on):

  lint      `checkpoint_codes.yaml` is refused for an entry matching no
            exercise, an entry for a step that is not `checkpoint`, and an empty
            code, and two steps sharing a code is advice, not a refusal.
  sync      a copy of the subject with codes for two steps: those two get the
            chosen codes, the sync warns for each live code it replaces, and
            every other checkpoint step keeps exactly the code it had.
  again     a second sync changes nothing and warns about nothing.
  grade     the chosen code validates its step in another case; the old code
            and a wrong one do not.

Needs PyYAML and requests, and imports tools/ the way the CLI does.

**It puts the instance back**: the original codes are re-applied through the
importer's own `--codes` file, which outranks the codes already live, and the
prerequisites and solves it touches are restored, on exit or on an exception.
"""
import atexit
import contextlib
import io
import re
import shutil
import sys
import tempfile
from pathlib import Path

import requests
import yaml

if len(sys.argv) < 4:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
SUBJECT = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from sync_subject import sync  # noqa: E402
from ws_parser import lint_all, parse_subject  # noqa: E402

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


s = requests.Session()
s.post(BASE + "/login?staff=1", timeout=20, data={
    "name": "admin", "password": ADMIN_PASS,
    "nonce": nonce(s, "/login?staff=1"), "_submit": "Submit"})
CSRF = nonce(s)


def api(method, path, **kw):
    return s.request(method, BASE + "/api/v1" + path, timeout=30,
                     headers={"CSRF-Token": CSRF, "Content-Type": "application/json"}, **kw)


if api("GET", "/configs/ctf_name").status_code != 200:
    sys.exit("could not sign in as admin — wrong password?")

subject = parse_subject(SUBJECT)
slug = subject.slug
checkpoints = [e.slug for e in subject.exercises if e.validation == "checkpoint"]
if len(checkpoints) < 3:
    sys.exit(f"{slug} has {len(checkpoints)} checkpoint step(s); three are needed")


def ids_by_slug():
    out = {}
    for c in api("GET", "/challenges?view=admin").json()["data"]:
        for t in api("GET", f"/challenges/{c['id']}/topics").json()["data"]:
            if t["value"].startswith(f"ws:{slug}:"):
                out[t["value"].split(":", 2)[2]] = c["id"]
    return out


IDS = ids_by_slug()
missing = [x for x in checkpoints if x not in IDS]
if missing:
    sys.exit(f"the instance does not hold {missing}: sync {SUBJECT} into it first")


def live():
    raw = (api("GET", "/workshop/checkpoints").json() or {})
    raw = raw.get("data", raw)
    by_id = {int(k): str(v) for k, v in raw.items()}
    return {x: by_id.get(IDS[x]) for x in checkpoints}


ORIGINAL = live()
work = Path(tempfile.mkdtemp(prefix="codes_check-"))
mode = (api("GET", "/configs/workshop_mode").json().get("data") or {}).get("value")
touched = {}   # challenge id -> requirements to put back


def run_sync(subject_dir, codes):
    """Import as the CLI does, returning what it printed."""
    codes_file = work / f"codes-{len(list(work.iterdir()))}.yaml"
    if codes is not None:
        codes_file.write_text(yaml.safe_dump(codes))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sync(str(subject_dir), BASE, "admin", ADMIN_PASS, str(codes_file))
    return out.getvalue()


def restore():
    for cid, req in touched.items():
        api("PATCH", f"/challenges/{cid}", json={"requirements": req or {}})
    me = api("GET", "/users/me").json()["data"]["id"]
    for sub in api("GET", f"/submissions?user_id={me}").json().get("data", []):
        if sub["challenge_id"] in touched:
            api("DELETE", f"/submissions/{sub['id']}")
    api("PATCH", "/configs", json={"workshop_mode": mode or ""})
    # The --codes file outranks the live codes, so this puts every one back.
    run_sync(SUBJECT, ORIGINAL)
    print("restored:", "codes match" if live() == ORIGINAL else "CODES DIFFER", flush=True)
    shutil.rmtree(work, ignore_errors=True)


atexit.register(restore)


def subject_copy(codes_yaml):
    dest = work / f"subject-{len(list(work.iterdir()))}"
    shutil.copytree(SUBJECT, dest, ignore=shutil.ignore_patterns(".git"))
    if codes_yaml is not None:
        (dest / "checkpoint_codes.yaml").write_text(codes_yaml)
    return dest


# ------------------------------------------------------------------- lint --
print("== lint ==")
other = next((e.slug for e in subject.exercises if e.validation != "checkpoint"), None)
cases = [
    ("an entry matching no exercise", f"codes:\n  no-such-step: abc\n", "matches no exercise"),
    ("an empty code", f"codes:\n  {checkpoints[0]}: ''\n", "needs a non-empty code"),
    ("a list instead of a code", f"codes:\n  {checkpoints[0]}: [a, b]\n", "needs a non-empty code"),
]
if other:
    cases.append(("an entry for a step that is not a checkpoint",
                  f"codes:\n  {other}: abc\n", "not by a checkpoint code"))
for label, text, expect in cases:
    problems, _ = lint_all(subject_copy(text))
    check(any(expect in p for p in problems), f"refused: {label}")
problems, advice = lint_all(subject_copy(
    f"codes:\n  {checkpoints[0]}: same\n  {checkpoints[1]}: SAME\n"))
check(not problems and any("share one code" in a for a in advice),
      "two steps sharing a code is advice, not a refusal")
problems, _ = lint_all(subject_copy(None))
check(not problems, "no codes file at all is fine")

# ------------------------------------------------------------------- sync --
print("== a partial checkpoint_codes.yaml ==")
A, B = checkpoints[0], checkpoints[1]
CHOSEN = {A: "Tortue-Carre", B: "2026"}
chosen_dir = subject_copy(f"codes:\n  {A}: Tortue-Carre\n  {B}: 2026\n")
printed = run_sync(chosen_dir, None)
now = live()
check(now[A] == "Tortue-Carre", f"{A} takes the chosen code")
check(now[B] == "2026", f"{B} takes the chosen code, unquoted digits read as text")
check(all(now[x] == ORIGINAL[x] for x in checkpoints if x not in CHOSEN),
      f"the other {len(checkpoints) - 2} checkpoint step(s) keep their instance code")
check(all(f"code for {x!r} now comes from checkpoint_codes.yaml" in printed for x in CHOSEN),
      "the sync warns for each live code it replaces")
check("2 checkpoint step(s) use the code chosen" in printed, "and says how many steps it chose")

print("== the same sync again ==")
printed = run_sync(chosen_dir, None)
check(live() == now, "a re-sync changes no code")
check("now comes from checkpoint_codes.yaml" not in printed, "and warns about nothing")

# ------------------------------------------------------------------ grade --
print("== grading ==")
api("PATCH", "/configs", json={"workshop_mode": "instructor_led"})
cid = IDS[A]
touched[cid] = api("GET", f"/challenges/{cid}").json()["data"].get("requirements")
api("PATCH", f"/challenges/{cid}", json={"requirements": {}})


def attempt(code):
    r = api("POST", "/challenges/attempt", json={"challenge_id": cid, "submission": code})
    return (r.json().get("data") or {}).get("status") if r.status_code == 200 else r.status_code


check(attempt("wrong-code") == "incorrect", "a wrong code is refused")
if ORIGINAL[A] and ORIGINAL[A].lower() != "tortue-carre":
    check(attempt(ORIGINAL[A]) == "incorrect", "the code the instance had no longer validates")
check(attempt("  TORTUE-CARRE ") == "correct", "the chosen code validates, in another case")

print()
print("ALL GREEN" if not fails else f"FAILURES: {fails}")
sys.exit(1 if fails else 0)
