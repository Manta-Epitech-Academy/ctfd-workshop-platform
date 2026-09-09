#!/usr/bin/env python3
"""Phase 2 validation: the content pipeline imports a convention-2.0 subject
repo into a fresh CTFd instance, idempotently.

 0. static checks, before anything is wiped: the vendored parser still matches
    the one published in workshop-content-tools, which is what makes "what CI
    accepts, the platform imports" true
 1. setup wizard on a FRESH instance
 2. tools/sync_subject.py imports content/pypong (converted from the real
    pypong_new): index page, 9 chained exercises, 4 quizzes, hints, codes
 3. attendee checks: intro page, progression (locked = '???'), full
    instructions with context, hint, quiz gating + grading, no answer leaks
 4. navigation UX: the graph endpoint exposes the DAG (branch out of
    exercise 1, anonymized locked names) and the Parcours page is created
 5. the single-page workshop view (/workshop) renders the whole subject with
    per-step state, and never serves a locked statement
 6. re-sync is idempotent: nothing re-created, solves preserved
 7. a content edit re-syncs in place (description updated, solve kept)

Usage: python3 scripts/phase2_validate.py [base_url]   (default http://localhost:8080)
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import requests
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from check_content_sync import check_all as content_sync_check  # noqa: E402
from check_parser_sync import compare as parser_sync_compare  # noqa: E402
from sync_subject import CTFdAdmin, sync  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
ADMIN = {"name": "admin", "email": "admin@example.com", "password": "phase2-admin-pass"}
ATTENDEE = {"name": "alice", "email": "alice@example.com", "password": "phase2-alice-pass"}

checks_run = 0


def check(cond, label):
    global checks_run
    checks_run += 1
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        sys.exit(1)


def get_nonce(session, path="/"):
    r = session.get(BASE + path)
    m = (re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", r.text)
         or re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text))
    if not m:
        raise RuntimeError(f"no CSRF nonce on {path} (status {r.status_code})")
    return m.group(1)


def api(session, method, path, **kwargs):
    headers = {"CSRF-Token": session.nonce, "Content-Type": "application/json"}
    return session.request(method, BASE + "/api/v1" + path, headers=headers, **kwargs)


def run_setup():
    print("== Setup wizard ==")
    s = requests.Session()
    r = s.get(BASE + "/setup")
    if r.url.rstrip("/") == BASE:
        sys.exit("Instance already set up — reset first (see PLAN.md Phase 0 note).")
    nonce = get_nonce(s, "/setup")
    r = s.post(BASE + "/setup", data={
        "ctf_name": "Phase 2 - PyPong", "ctf_description": "Pipeline de contenu",
        "user_mode": "users", "name": ADMIN["name"], "email": ADMIN["email"],
        "password": ADMIN["password"], "challenge_visibility": "private",
        "account_visibility": "public", "score_visibility": "public",
        "registration_visibility": "public", "verify_emails": "false",
        "ctf_theme": "core-beta", "theme_color": "", "start": "", "end": "",
        "_submit": "Finish", "nonce": nonce,
    })
    check(r.status_code == 200, "setup completed")


def register_attendee():
    s = requests.Session()
    nonce = get_nonce(s, "/register")
    s.post(BASE + "/register", data={
        "name": ATTENDEE["name"], "email": ATTENDEE["email"],
        "password": ATTENDEE["password"], "nonce": nonce, "_submit": "Submit"})
    s.nonce = get_nonce(s)
    return s


def register_attendee2():
    """A second participant, for the checks that must prove per-user isolation."""
    s = requests.Session()
    nonce = get_nonce(s, "/register")
    s.post(BASE + "/register", data={
        "name": "bob", "email": "bob@example.com",
        "password": "phase2-bob-pass", "nonce": nonce, "_submit": "Submit"})
    s.nonce = get_nonce(s)
    return s


def attempt(s, cid, submission):
    r = api(s, "POST", "/challenges/attempt",
            json={"challenge_id": cid, "submission": submission})
    if r.status_code == 403:
        return "forbidden"
    return r.json()["data"]["status"]


def visible(s):
    return {c["id"]: c["name"] for c in api(s, "GET", "/challenges").json()["data"]}


def check_static():
    """Checks that need no instance. Run first: they are cheap, and the setup
    wizard below wipes whatever is on the target."""
    print("== Static checks ==")
    status, detail = parser_sync_compare()
    if status == "skip":
        # Not a pass and not a failure — say so rather than letting a green run
        # imply the parsers were compared.
        print(f"  [skip] parser sync: {detail}")
    else:
        check(status == "ok", f"vendored parser matches the published one\n{detail}"
              if status == "fail" else "vendored parser matches the published one")

    # Same idea one level up: the sync imports content/<subject>, the subject's
    # own repo is what its CI lints, and nothing else compares them. A subject
    # with no upstream (pypong is a conversion, not a mirror) reports skip.
    for subject, status, detail in content_sync_check():
        if status == "skip":
            print(f"  [skip] content sync: {detail}")
        else:
            check(status == "ok", detail if status == "fail"
                  else f"content/{subject} matches its subject repo")


def check_validation_modes():
    """The two validation modes, without an instance: this is parser and linter
    behaviour, and it is cheaper and clearer to prove on a fixture than on a
    subject that uses only one of them (PLAN.md §20)."""
    print("== Validation modes (checkpoint / flag) ==")
    import ws_parser
    root = Path(tempfile.mkdtemp()) / "fx"
    (root).mkdir(parents=True)
    (root / "subject.yaml").write_text(
        'schema_version: "2.0"\n'
        'project: {name: FX, slug: fx, summary: s, entrypoint: intro.md}\n'
        'platform: {validation_default: flag, points_default: 10}\n'
        'documents: [intro.md, main.md]\n')
    (root / "intro.md").write_text("# FX\n\nIntro.\n")
    (root / "main.md").write_text(
        "# Exercices\n\n## Un\n<!-- ws: {type: exercise, id: a} -->\n\nFaire.\n\n"
        "## Deux\n<!-- ws: {type: exercise, id: b, validation: checkpoint} -->\n\nFaire.\n\n"
        "## Fin\n\nMerci.\n")
    problems = ws_parser.lint(root)
    check(any("no entry in flags.yaml" in p for p in problems),
          "a flag exercise with no authored answer is refused")
    (root / "flags.yaml").write_text('flags:\n  a: "demo{x}"\n')
    check(ws_parser.lint(root) == [], "and accepted once the answer exists")
    subject = ws_parser.parse_subject(root)
    modes = {e.slug: e.validation for e in subject.exercises}
    check(modes == {"a": "flag", "b": "checkpoint"},
          "the subject default applies, and a marker overrides it")
    (root / "flags.yaml").write_text('flags:\n  a: "demo{x}"\n  ghost: "demo{y}"\n')
    check(any("matches no exercise" in p for p in ws_parser.lint(root)),
          "an answer for an exercise that does not exist is refused")
    # `token` needs the runtime's own exercise id to derive from.
    (root / "main.md").write_text(
        (root / "main.md").read_text().replace("validation: checkpoint", "validation: token"))
    check(any("no `token_id`" in p for p in ws_parser.lint(root)),
          "a token exercise with nothing to derive from is refused")
    (root / "main.md").write_text(
        (root / "main.md").read_text().replace("validation: token",
                                               "validation: token, token_id: 7"))
    # Drop the deliberately-bogus entry from the check above, or this one fails
    # for a reason that has nothing to do with tokens.
    (root / "flags.yaml").write_text('flags:\n  a: "demo{x}"\n')
    check(ws_parser.lint(root) == [], "and accepted once it names one")
    (root / "main.md").write_text(
        (root / "main.md").read_text().replace("validation: token, token_id: 7",
                                               "validation: review"))
    check(any("not implemented" in p for p in ws_parser.lint(root)),
          "a validation mode that is specified but unimplemented says so")
    shutil.rmtree(root.parent, ignore_errors=True)


def main():
    check_static()
    check_validation_modes()
    run_setup()

    print("== Sync content/pypong ==")
    # The instructor sheet lives at a stable path, never a temp dir: a throwaway
    # sheet used to hand every run a brand-new set of codes.
    codes_file = REPO / "instructor_codes.pypong.yaml"
    result = sync(str(REPO / "content/pypong"), BASE,
                  ADMIN["name"], ADMIN["password"], str(codes_file))
    ex_ids, quiz_ids = result["ex_ids"], result["quiz_ids"]
    codes = yaml.safe_load(codes_file.read_text())
    check(len(ex_ids) == 9, f"9 exercises imported: {list(ex_ids)[:3]}...")
    check(len(quiz_ids) == 4, "4 quizzes imported")

    print("== Board ordering (reading order, not alphabetical) ==")
    admin = CTFdAdmin(BASE, ADMIN["name"], ADMIN["password"])
    board = admin.api("GET", "/challenges?view=admin")
    positions = [c.get("position", 0) for c in board]
    check(all(p > 0 for p in positions), "every challenge has a non-zero position")
    check(positions == sorted(positions), "API returns challenges in position order")
    # Reading order: intro, the two intro quizzes, exercise 1 and its quiz…
    ordered_ids = [c["id"] for c in sorted(board, key=lambda c: c["position"])]
    intro_id, outro_id = ordered_ids[0], ordered_ids[-1]
    expected_head = [intro_id, quiz_ids["init-cmd"][0], quiz_ids["cls-role"][0],
                     ex_ids["pad-direction"], quiz_ids["btn-doc"][0]]
    check(ordered_ids[:5] == expected_head,
          "reading order: intro, intro quizzes, exercise 1, its quiz")
    check(ordered_ids[-2] == ex_ids["vies-gameover"], "last exercise sorts second to last")

    print("== Intro and outro are steps, not side pages ==")
    intro = admin.api("GET", f"/challenges/{intro_id}")
    outro = admin.api("GET", f"/challenges/{outro_id}")
    check(intro["name"] == "Prise en main de TIC-80" and intro["type"] == "quiz",
          "the entrypoint document is a step")
    check(intro["value"] == 0 and outro["value"] == 0,
          "intro and outro are worth no points")
    check(intro["quiz_type"] == "ack" and outro["quiz_type"] == "rating",
          "intro asks for an acknowledgement, the outro for a workshop rating")
    check("fantasy retro console" in intro["description"],
          "the intro step carries the whole entrypoint document")
    check(outro["name"] == "Pour aller plus loin",
          "the trailing prose is the closing step")
    check("pong-video-game.gif" in outro["description"],
          "the going-further gif (would-be-dropped) is in the outro step")
    check("Crédits" in outro["description"], "credits section preserved")
    check(not any(p["route"].startswith("pypong/") for p in admin.api("GET", "/pages")),
          "no duplicate trailing page beside the outro step")

    print("== Image styling ==")
    r = requests.get(BASE + "/login")
    check("plugins/workshop/assets/workshop.css" in r.text,
          "workshop stylesheet injected on every page (caps hint images)")

    print("== Attendee: page, progression, instructions ==")
    r = requests.get(BASE + "/")
    check("Prise en main de TIC-80" in r.text and "new python" in r.text,
          "index page carries the intro document")
    check("ws:" not in r.text.replace("news:", ""), "no ws markers leak on the page")

    s = register_attendee()
    seen = visible(s)
    first = ex_ids["pad-direction"]
    # The intro now gates every root, so a fresh attendee sees only it.
    check(seen.get(intro_id) == "Prise en main de TIC-80", "the intro is the way in")
    check(seen.get(first) == "???", "the first exercise waits behind the intro")
    check(seen.get(quiz_ids["init-cmd"][0]) == "???", "intro quizzes wait behind the intro too")
    check(attempt(s, intro_id, "read") == "correct",
          "acknowledging the intro is enough to complete it (nothing to grade)")
    seen = visible(s)
    check(seen.get(first, "").startswith("Exercice 1"), "first exercise unlocked by the intro")
    check(seen.get(ex_ids["pad-limites"]) == "???", "second exercise anonymized ('???')")
    check(seen.get(quiz_ids["init-cmd"][0], "").startswith("Quiz"),
          "intro quiz unlocked by the intro")
    check(seen.get(quiz_ids["btn-doc"][0]) == "???", "hosted quiz locked behind its exercise")
    check(seen.get(outro_id) == "???", "the outro waits until the workshop is finished")
    check(attempt(s, intro_id, "read") == "already_solved",
          "the intro cannot be acknowledged twice")

    print("== Ratings (CTFd's own table, two paths in) ==")
    def rate(cid, value, review=""):
        return s.put(BASE + f"/api/v1/challenges/{cid}/ratings",
                     headers={"CSRF-Token": s.nonce, "Content-Type": "application/json"},
                     json={"value": value, "review": review})
    check(rate(outro_id, 1).status_code == 403,
          "CTFd refuses a rating for an unsolved challenge (so the outro cannot use it)")
    check(rate(intro_id, 1, "clear").status_code == 200,
          "a solved step can be rated")
    detail = api(s, "GET", f"/challenges/{intro_id}").json()["data"]
    check(detail.get("rating", {}).get("value") == 1, "the rating is stored and read back")
    body = s.get(BASE + f"/api/v1/workshop/step/{intro_id}").json()["data"]["html"]
    check("ws-rating" in body and "Rate this step" in body,
          "a solved step offers the quiet inline rating")
    check('class="form-control form-control-sm ws-rating-review"' in body
          and "hidden" in body.split("ws-rating-review")[1][:120],
          "its reason box ships hidden — revealed by the rating itself")

    detail = api(s, "GET", f"/challenges/{first}").json()["data"]
    check("padx=45" in detail["description"] and "btn(2)" in detail["description"],
          "exercise 1 carries its context (section intro + code sample)")
    # The note under a step depends on the instance's mode, so it is rendered
    # at request time and must NOT be in the stored description (PLAN.md §25.6).
    check("instructor" not in detail["description"].lower(),
          "no mode-dependent note is baked into the stored statement")
    check(len(detail.get("hints", [])) == 1, "exercise 1 hint attached")
    # The authoring markers must never reach a participant. Two fences may: the
    # context one and the resume one, both written by the sync rather than by
    # the author. They are what lets the page lift a part's opening prose and
    # the step's short version out of the statement, while the description
    # stays self-sufficient on CTFd's own board (PLAN.md §25.7).
    comments = [c.strip() for c in re.findall(r"<!--(.*?)-->", detail["description"], re.S)]
    check(all(c in ("ws:context", "/ws:context", "ws:resume", "/ws:resume")
              for c in comments),
          f"no authoring markers leak in description (found {comments})")

    print("== Progression & quiz grading ==")
    check(attempt(s, ex_ids["pad-limites"], "x") == "forbidden", "locked exercise rejects attempts")
    check(attempt(s, first, "wrong-code") == "incorrect", "wrong validation code rejected")
    check(attempt(s, first, codes["pad-direction"].upper()) == "correct",
          "instructor code solves exercise 1 (case-insensitive)")
    seen = visible(s)
    check(seen.get(ex_ids["pad-limites"], "").startswith("Exercice 2"),
          "exercise 2 unlocked and de-anonymized")
    check(seen.get(quiz_ids["btn-doc"][0], "").startswith("Quiz"), "hosted quiz unlocked")

    print("== Navigation: challenge graph endpoint (feeds next-button + Parcours) ==")
    # Solving exercise 1 forks the path: exercise 2 AND its hosted quiz both
    # unlock. This is the branch state the modal must surface, checked here at
    # the data level (SVG/DOM rendering is verified in the browser separately).
    graph = api(s, "GET", "/workshop/graph").json()["data"]
    gmap = {n["id"]: n for n in graph["nodes"]}
    check(len(graph["nodes"]) == len(ex_ids) + len(quiz_ids) + 2,
          "graph exposes every challenge as a node (exercises, quizzes, intro, outro)")
    check(first in graph["solved"], "graph marks the solved exercise")
    succ = graph["next"].get(str(first), [])   # jsonify stringifies int keys
    check(set(succ) >= {ex_ids["pad-limites"], quiz_ids["btn-doc"][0]},
          "graph 'next' map records the branch out of exercise 1 (>=2 successors)")
    branch = [i for i in succ if gmap[i]["unlocked"] and not gmap[i]["solved"]]
    check(len(branch) >= 2, "both branches unlocked & unsolved -> modal shows a choice")
    locked = next(n for n in graph["nodes"] if not n["unlocked"])
    check(locked["name"] == "???", "locked challenge name anonymized in the graph")
    r = requests.get(BASE + "/login")
    check("plugins/workshop/assets/graph.js" in r.text, "graph.js injected on every page")

    print("== Navigation: Parcours page ==")
    parcours = next((p for p in admin.api("GET", "/pages") if p["route"] == "parcours"), None)
    check(parcours is not None, "Parcours page created by sync")
    pfull = admin.api("GET", f"/pages/{parcours['id']}")
    check('id="ws-parcours"' in pfull["content"], "Parcours page hosts the graph mount point")
    check(pfull["auth_required"], "Parcours page requires authentication")
    # Nodes are links to their challenge: the board opens whatever the URL hash
    # names (themes/core/assets/js/challenges.js reads it on init).
    asset = requests.get(BASE + "/plugins/workshop/assets/graph.js").text
    check("ws-node-link" in asset and "/challenges#" in asset,
          "Parcours nodes render as links to their challenge")
    css = requests.get(BASE + "/plugins/workshop/assets/workshop.css").text
    check(".ws-node-locked { cursor: not-allowed" in css,
          "locked nodes are not presented as clickable")

    qid = quiz_ids["btn-doc"][0]
    body = api(s, "GET", f"/challenges/{qid}").text
    check("quiz_answers" not in body, "quiz answers never serialized")
    check(attempt(s, qid, "A,C") == "incorrect", "wrong quiz answer rejected")
    check(attempt(s, qid, "B, a") == "correct", "quiz graded (order/case tolerant)")

    print("== The closing rating cannot be skipped ==")
    # Reaching 100% without leaving feedback must be impossible: the rating is
    # the submission, so an empty or malformed one does not solve the step.
    check(attempt(s, outro_id, "") == "forbidden", "the outro is still locked mid-workshop")

    print("== Single-page workshop view (/workshop) ==")
    # The participant view: whole subject as one document, steps as accordions.
    # pypong is one part, so /workshop redirects to it instead of serving a
    # second copy of the same steps; everything below is asserted on the part
    # page, which is what a participant actually reads.
    entry = s.get(BASE + "/workshop", allow_redirects=False)
    check(entry.status_code == 302
          and entry.headers.get("Location", "").endswith("/workshop/pypong"),
          "a single-part subject sends /workshop straight to that part")
    page = s.get(BASE + "/workshop")
    check(page.status_code == 200, "workshop page served to an attendee")
    check('id="ws-workshop"' in page.text, "workshop page renders its root")
    # The sync creates one Page per part for its *route*, never for a navbar
    # entry: every part Page is `hidden` (tools/sync_subject.py), which keeps
    # the route and drops only the link, so a subject with several parts cannot
    # wrap the header onto extra lines. The shell owns the item order outright
    # and puts its own "Workshop" entry first, which is the way in now
    # (plugins/workshop/templates/navbar.html).
    part_page = next((p for p in admin.api("GET", "/pages")
                      if p["route"] == "workshop/pypong"), None)
    check(part_page is not None and part_page["hidden"] is True,
          "the part Page keeps its route but stays out of the navbar")
    check('class="epi-nav-link' in page.text and 'href="/workshop"' in page.text,
          "the shell's own Workshop entry is what leads there instead")
    check("Prise en main de TIC-80" in page.text,
          "intro document folded into the page (no separate index visit)")
    check(page.text.count('class="ws-step ws-') >= len(ex_ids) + len(quiz_ids),
          "every challenge rendered as a step")
    check(f'id="step-{first}"' in page.text and 'data-state="done"' in page.text,
          "solved step marked done")
    nxt = ex_ids["pad-limites"]
    check(f'id="step-{nxt}"' in page.text, "the unlocked next step is on the page")
    check("ws-chip ws-current" in page.text and "ws-chip ws-locked" in page.text,
          "steppers carry per-step state (done/current/locked)")
    # The workshop rating is the last step and it counts: the bar cannot reach
    # 100% without it, which is what actually gets the feedback in.
    check('data-counts="0"' not in page.text,
          "every step counts towards the progress bar, the rating included")
    # Locked steps advertise their title but never their statement.
    late = ex_ids["vies-gameover"]
    check("ws-locked-note" in page.text, "locked steps explain what unlocks them")
    r = s.get(BASE + f"/api/v1/workshop/step/{late}")
    check(r.status_code == 403, "step endpoint refuses a locked statement")
    r = s.get(BASE + f"/api/v1/workshop/step/{nxt}")
    check(r.status_code == 200 and "ws-form" in r.json()["data"]["html"],
          "step endpoint serves an unlocked step (fills it in place on unlock)")
    check("<!--" not in page.text.split('id="ws-workshop"')[1],
          "no ws markers leak into the workshop page")

    print("== Runtime pane (PLAN.md §14) ==")
    # The subject declares tic80; the dist is built by tools/build_runtime.sh
    # and is gitignored, so the pane is only asserted when it is installed.
    cfg = json.loads(admin.api("GET", "/configs/workshop_runtime")["value"])
    check(cfg["id"] == "tic80" and cfg["version"],
          "subject runtime declaration synced to CTFd config")
    dist = REPO / "plugins/workshop/runtimes" / cfg["id"] / cfg["version"]
    if dist.is_dir():
        r = requests.get(f"{BASE}/runtime/{cfg['id']}/{cfg['version']}/")
        check(r.status_code == 200 and "<html" in r.text.lower(),
              "runtime dist served same-origin at /runtime/<id>/<version>/")
        wasm = requests.get(f"{BASE}/runtime/{cfg['id']}/{cfg['version']}/tic80/tic80.wasm")
        check(wasm.headers.get("Content-Type") == "application/wasm",
              "wasm served as application/wasm (instantiateStreaming rejects anything else)")
        check('id="ws-runtime"' in page.text and "ws-runtime-toggle" in page.text,
              "workshop page renders the pane and its toggle")
        check("ws-runtime-frame" not in page.text,
              "no iframe in the markup — the runtime mounts lazily, on open")
        check(page.text.count("ws-runtime-toggle") == 1 and "ws-runtime-handle" in page.text,
              "the only toggle is the fixed handle, reachable from anywhere")
    else:
        print(f"  [skip] {dist} not built (tools/build_runtime.sh {cfg['id']})")
    r = requests.get(BASE + f"/runtime/{cfg['id']}/../../etc/passwd")
    check(r.status_code == 404, "runtime path traversal refused")
    r = requests.get(BASE + "/plugins/workshop/assets/runtime/adapters/tic80.js")
    check(r.status_code == 200 and "wrp" in r.text, "runtime adapter served to the frame")

    print("== The feedback report (PLAN.md §17) ==")
    # allow_redirects=False, or the 302 to /login follows through to a 200 and
    # the check quietly proves nothing.
    r = s.get(BASE + "/admin/workshop/feedback", allow_redirects=False)
    check(r.status_code in (302, 403), "a participant cannot read the feedback report")
    page = admin.s.get(BASE + "/admin/workshop/feedback").text
    check("Workshop feedback" in page, "the report renders for an admin")
    # The two populations must stay apart: a closing-step verdict is not a
    # comment on one exercise's writing.
    check("The workshop" in page and "Steps, worst first" in page,
          "verdicts and step quality are reported separately")
    check(outro["name"] not in page.split("Steps, worst first")[1],
          "the closing step is not listed among the rated exercises")
    csv_body = admin.s.get(BASE + "/admin/workshop/feedback.csv")
    check(csv_body.headers["Content-Type"].startswith("text/csv")
          and csv_body.text.startswith("kind,step,part,"),
          "the same rows download as CSV")

    print("== The answer sheet (PLAN.md §23) ==")
    for path in ("/admin/workshop/answers", "/admin/workshop/answers.csv"):
        r = s.get(BASE + path, allow_redirects=False)
        check(r.status_code in (302, 403), f"a participant cannot read {path}")
    sheet = admin.s.get(BASE + "/admin/workshop/answers").text
    check("Answer sheet" in sheet, "the sheet renders for an admin")
    # The point of the page: the live code this instance will accept, which
    # otherwise exists only in the maintainer's instructor_codes file.
    check(codes["pad-direction"] in sheet and "Instructor code" in sheet,
          "a checkpoint code is shown, labelled as the instructor's to read out")
    # The sync records the mode now, so nothing on a freshly synced instance
    # should be labelled from the shape of the stored flag.
    check("synced before the validation mode was recorded" not in sheet,
          "no row is inferred: the sync recorded every validation mode")
    recorded = json.loads(admin.api("GET", "/configs/workshop_validation")["value"])
    check(recorded[str(ex_ids["pad-direction"])] == "checkpoint",
          "the sync wrote the validation map the sheet reads")
    # Quiz answers are a plugin JSON column that no CTFd page and no API
    # response exposes. This is the only place they can be read.
    quiz_first = quiz_ids["init-cmd"][0]
    check(admin.api("GET", f"/challenges/{quiz_first}").get("quiz_answers") is None,
          "the API still refuses to hand out a quiz answer")
    check("Quiz — one answer" in sheet, "the sheet reads the quiz answer instead")
    # Who is where: the participant is on it, and the admin is not.
    people = sheet.split("Who is where")[1].split("</section>")[0]
    # By id, not by name: every row links to /admin/users/<id>, so the literal
    # word "admin" is in the markup whoever is listed.
    listed = set(re.findall(r"/admin/users/(\d+)", people))
    check(re.search(r">\s*%s\s*<" % ATTENDEE["name"], people),
          "the participant appears in the progress table")
    check(str(admin.api("GET", "/users/me")["id"]) not in listed,
          "the admin does not: an instructor account is not an attendee")
    csv_body = admin.s.get(BASE + "/admin/workshop/answers.csv")
    check(csv_body.headers["Content-Type"].startswith("text/csv")
          and csv_body.text.startswith("part,step,kind,answer,")
          and codes["pad-direction"] in csv_body.text,
          "the same answers download as CSV")

    print("== Instructor-led and self-serve (PLAN.md §25) ==")
    # A checkpoint step is a plugin-owned challenge now, because whether its
    # code is required depends on the instance and only plugin code runs at
    # submit time.
    cp = admin.api("GET", f"/challenges/{ex_ids['pad-limites']}")
    check(cp["type"] == "quiz" and cp["quiz_type"] == "checkpoint",
          "a checkpoint step is a checkpoint challenge, not a standard one")
    check(cp.get("quiz_answers") is None,
          "and its code is not in the API response, for an admin either")
    check(not admin.api("GET", f"/challenges/{cp['id']}/flags"),
          "no static flag is left beside it: one answer, one place")

    r = s.get(BASE + "/admin/workshop/settings", allow_redirects=False)
    check(r.status_code in (302, 403), "a participant cannot reach the mode setting")
    mode_page = admin.s.get(BASE + "/admin/workshop/settings").text
    check("Workshop settings" in mode_page and 'value="self_serve"' in mode_page,
          "the admin can see both modes")
    # Nothing set this key: the wizard has no field for it and this instance
    # was not provisioned by tools/provision.py. Default instructor_led is what
    # an unconfigured instance has always in fact been (PLAN.md §25.3).
    check(re.search(r'id="ws-mode-instructor"[^>]*checked', mode_page) is not None,
          "an instance with nothing set reads as instructor-led")

    def set_mode(mode):
        nonce = get_nonce(admin.s, "/admin/workshop/settings")
        admin.s.post(BASE + "/admin/workshop/settings",
                     data={"workshop_mode": mode, "nonce": nonce})

    def step_html(cid):
        return s.get(BASE + f"/api/v1/workshop/step/{cid}").json()["data"]["html"]

    target = ex_ids["pad-limites"]     # unlocked, unsolved by this attendee
    body = step_html(target)
    check('data-answer-kind="code"' in body and "ask the instructor" in body,
          "instructor-led: the step asks for the code the instructor reads out")
    check(codes["pad-limites"] not in body,
          "and the code itself never reaches the page")
    check(attempt(s, target, "done") == "incorrect",
          "instructor-led: pressing a button is not a valid answer")

    set_mode("self_serve")
    body = step_html(target)
    check('data-answer-kind="done"' in body and "Mark as done" in body,
          "self-serve: the same step offers a button instead")
    check("ask the instructor" not in body and 'class="form-control ws-answer"' not in body,
          "self-serve: no code is asked for, and none is mentioned")
    check(codes["pad-limites"] not in body,
          "self-serve: the code is stored but still never revealed")
    check(attempt(s, target, "done") == "correct",
          "self-serve: the button solves the step")
    sheet = admin.s.get(BASE + "/admin/workshop/answers").text
    check("This instance is self-serve" in sheet and codes["pad-limites"] in sheet,
          "the answer sheet says so, and still lists the codes for the way back")

    set_mode("instructor_led")
    check(attempt(s, ex_ids["pad-direction"], codes["pad-direction"]) == "already_solved",
          "flipping back leaves every solve where it was")
    check("This instance is self-serve"
          not in admin.s.get(BASE + "/admin/workshop/answers").text,
          "and the sheet reads as a session sheet again")

    print("== Migrating a pre-§25 checkpoint step in place ==")
    # What every already-deployed instance looks like: a standard challenge with
    # a static flag. The migration must move it to the checkpoint type without
    # touching the id, and therefore without touching the solve on it.
    legacy = admin.api("POST", "/challenges", json={
        "name": "Legacy checkpoint", "category": "Migration", "description": "old shape",
        "value": 10, "type": "standard", "state": "visible"})
    admin.api("POST", "/flags", json={"challenge_id": legacy["id"], "type": "static",
                                      "content": "0ff1ce", "data": "case_insensitive"})
    check(attempt(s, legacy["id"], "0ff1ce") == "correct",
          "the old shape solves the old way")
    solved_before = admin.api("GET", f"/challenges/{legacy['id']}/solves")
    result = admin.api("POST", "/workshop/checkpoints/migrate",
                       json={"challenge_ids": [legacy["id"]]})
    check(result["migrated"] == [legacy["id"]], "the migration reports it converted it")
    after = admin.api("GET", f"/challenges/{legacy['id']}")
    check(after["type"] == "quiz" and after["quiz_type"] == "checkpoint",
          "the challenge is now a checkpoint step")
    check(after["id"] == legacy["id"], "with the same id, which is the whole point")
    solved_after = admin.api("GET", f"/challenges/{legacy['id']}/solves")
    check(len(solved_before) == 1 and len(solved_after) == 1,
          "the solve on it survived the type change")
    check(not admin.api("GET", f"/challenges/{legacy['id']}/flags"),
          "and the flag it used to be answered by is gone")
    codes_now = admin.api("GET", "/workshop/checkpoints")
    check(codes_now[str(legacy["id"])] == "0ff1ce",
          "the code was carried over, so a sheet handed out this morning still works")
    # `admins_only` sends a browser to the login page and refuses an API call
    # outright, so both shapes are checked — following the redirect would land
    # on a 200 login page and prove nothing.
    check(s.get(BASE + "/api/v1/workshop/checkpoints",
                allow_redirects=False).status_code == 302
          and s.get(BASE + "/api/v1/workshop/checkpoints",
                    headers={"Content-Type": "application/json"},
                    allow_redirects=False).status_code == 403,
          "a participant cannot read the codes back")
    check(admin.api("POST", "/workshop/checkpoints/migrate",
                    json={"challenge_ids": [legacy["id"]]})["already"] == [legacy["id"]],
          "running the migration twice is a no-op")
    admin.api("DELETE", f"/challenges/{legacy['id']}")

    print("== The sync page (PLAN.md §26) ==")
    # The page itself, its access control and the shape of what it stores. The
    # fetch is deliberately not exercised here: this suite must pass without a
    # network, and what talks to GitHub is covered by its own walkthrough
    # (a subject and a workshop, both imported from their real repositories).
    for path in ("/admin/workshop/sync", "/api/v1/workshop/sync"):
        r = s.get(BASE + path, allow_redirects=False)
        check(r.status_code in (302, 403), f"a participant cannot reach {path}")
    page_html = admin.s.get(BASE + "/admin/workshop/sync").text
    check("Sync content" in page_html, "the sync page renders for an admin")
    check("passphrase is used in your browser" in page_html,
          "and says where an encrypted answers file is opened")
    admin.api("PATCH", "/configs/workshop_source",
              json={"value": json.dumps({"repo": "kevin-cazal/pypong_subject",
                                         "ref": "main"})})
    page_html = admin.s.get(BASE + "/admin/workshop/sync").text
    check("kevin-cazal/pypong_subject" in page_html,
          "the page names the repository this instance follows")
    r = s.post(BASE + "/api/v1/workshop/sync", json={},
               headers={"CSRF-Token": s.nonce, "Content-Type": "application/json"})
    check(r.status_code in (302, 403), "a participant cannot start a sync")
    # An instance with no source says so instead of starting a job that cannot
    # do anything.
    admin.api("PATCH", "/configs/workshop_source", json={"value": ""})
    r = admin.s.post(BASE + "/api/v1/workshop/sync", json={},
                     headers={"CSRF-Token": admin.nonce,
                              "Content-Type": "application/json"})
    check(r.status_code == 400 and "source repository" in r.text,
          "with no source configured, starting a sync is refused")
    check(admin.s.get(BASE + "/api/v1/workshop/sync").json()["data"]["state"] == "idle",
          "and no job is left behind")

    print("== Work in progress, kept server-side (PLAN.md §16) ==")
    WS = "/workshop/workspace"
    check(requests.get(BASE + "/api/v1" + WS, allow_redirects=False).status_code
          in (302, 403), "anonymous cannot read a workspace")
    check(api(s, "GET", WS).json()["data"]["keys"] == {},
          "a participant with no saved work reads an empty workspace")
    cart = json.dumps({"version": 1, "text": "-- travail", "ext": "lua"})
    r = api(s, "POST", WS, json={"runtime": cfg["id"],
                                 "keys": {"tic80-web-editor-cart": cart}})
    check(r.status_code == 200 and r.json()["data"]["updated"],
          "a save returns its timestamp")
    check(api(s, "GET", WS).json()["data"]["keys"]["tic80-web-editor-cart"] == cart,
          "what was saved comes back verbatim")
    # The one that matters: this is per participant, or it is a way to read
    # somebody else's work.
    other = register_attendee2()
    check(api(other, "GET", WS).json()["data"]["keys"] == {},
          "another participant sees none of it — rows are per user")
    r = api(other, "POST", WS, json={"runtime": cfg["id"], "user_id": 1,
                                     "keys": {"tic80-web-editor-cart": "theirs"}})
    check(api(s, "GET", WS).json()["data"]["keys"]["tic80-web-editor-cart"] == cart,
          "a body naming another user does not write into that user's row")
    check(api(s, "POST", WS, json={"runtime": "nope", "keys": {}}).status_code == 400,
          "a runtime this instance does not declare is refused")
    check(api(s, "POST", WS, json={"runtime": cfg["id"],
                                   "keys": {"k": {"nested": 1}}}).status_code == 400,
          "non-string values are refused")
    check(api(s, "POST", WS, json={"runtime": cfg["id"],
                                   "keys": {"big": "x" * (600 * 1024)}}).status_code == 413,
          "an oversized workspace is refused with 413, not stored")
    check(api(s, "GET", WS).json()["data"]["keys"]["tic80-web-editor-cart"] == cart,
          "and a refused save leaves the previous one intact")
    check("ws-workspace-owner" in requests.get(
              BASE + "/plugins/workshop/assets/runtime.js").text,
          "the host script stamps the browser bucket with an owner "
          "(the shared classroom PC rule)")

    print("== Workshop page is the landing page (board kept) ==")
    r = s.get(BASE + "/", allow_redirects=False)
    check(r.status_code == 302 and r.headers["Location"].endswith("/workshop"),
          "a signed-in participant landing on / goes to the workshop")
    check(requests.get(BASE + "/", allow_redirects=False).status_code == 200,
          "anonymous visitors still get the public index page")
    fresh = requests.Session()
    n = get_nonce(fresh, "/login")
    r = fresh.post(BASE + "/login", allow_redirects=False, data={
        "name": ATTENDEE["name"], "password": ATTENDEE["password"],
        "nonce": n, "_submit": "Submit"})
    check(r.headers.get("Location", "").endswith("/workshop"),
          "login lands on the workshop page instead of the board")
    # An explicit ?next= is honoured by CTFd before its default landing, so
    # retargeting the default must not break deep links.
    n = get_nonce(fresh, "/login?next=%2Fscoreboard")
    r = fresh.post(BASE + "/login?next=%2Fscoreboard", allow_redirects=False, data={
        "name": ATTENDEE["name"], "password": ATTENDEE["password"],
        "nonce": n, "_submit": "Submit"})
    check(r.headers.get("Location", "").endswith("/scoreboard"),
          "an explicit ?next= still wins over the workshop landing")
    check(s.get(BASE + "/challenges").status_code == 200,
          "the challenge board is still reachable at /challenges")

    print("== Finishing the workshop: 100% requires the feedback ==")
    # Everything except the closing step, in reading order.
    submissions = {intro_id: "read"}
    submissions.update({cid: codes[slug] for slug, cid in ex_ids.items()})
    submissions.update({quiz_ids[k][0]: v for k, v in {
        "init-cmd": "B", "cls-role": "C", "btn-doc": "A,B",
        "rebond": "A-b,B-c,C-a"}.items()})
    for cid in ordered_ids:
        if cid in submissions:
            attempt(s, cid, submissions[cid])

    def counter():
        m = re.search(r'class="ws-overall-num">([^<]+)<', s.get(BASE + "/workshop").text)
        return m.group(1).strip()

    check(counter() == "14/15", "everything done but the feedback: the bar stops at 14/15")
    check(attempt(s, outro_id, "") == "incorrect",
          "an empty rating does not solve the closing step")
    check(attempt(s, outro_id, "lovely") == "incorrect",
          "a submission that carries no rating does not solve it either")
    # A bare value is valid JSON that is not an object. It used to reach
    # `.get` on an int and 500 the worker, which no browser ever did but any
    # script does.
    check(attempt(s, outro_id, "0") == "incorrect",
          "a bare out-of-range value is refused, not a server error")
    check(counter() == "14/15", "still 14/15 — 100% is unreachable without feedback")
    check(attempt(s, outro_id, json.dumps({"value": 1, "review": "clear and well paced"}))
          == "correct", "the rating itself solves the step")
    check(counter() == "15/15", "and only then does the workshop read 100%")
    detail = api(s, "GET", f"/challenges/{outro_id}").json()["data"]
    check(detail["rating"]["value"] == 1
          and detail["rating"]["review"] == "clear and well paced",
          "the feedback landed in CTFd's own ratings table, comment included")
    # Someone who rates first and thinks of the reason afterwards must be able
    # to add it: the comment box stays, pre-filled, once the step is done.
    body = s.get(BASE + f"/api/v1/workshop/step/{outro_id}").json()["data"]["html"]
    check('value="clear and well paced"' in body and "ws-rating-save" in body,
          "the given rating comes back editable, comment and all")

    print("== A part's opening prose belongs to the part, not to its first step ==")
    page = s.get(BASE + "/workshop/pypong").text
    check("ws-part-lead" in page, "the opening prose renders above the steps")
    lead = re.search(r'class="ws-part-lead-body[^"]*">(.*?)</details>',
                     page, re.S).group(1)
    lead_text = re.sub(r"<[^>]+>", "", lead).strip()[:60]
    first_step = page.split('class="ws-statement', 1)[1][:1500]
    check(lead_text and lead_text not in first_step,
          "and no longer inside the first step, which now starts with its own task")
    desc = admin.api("GET", f"/challenges/{ex_ids['pad-direction']}")["description"]
    check("<!-- ws:context -->" in desc and lead_text[:30] in desc,
          "the challenge itself keeps that prose, fenced — the board stays self-sufficient")
    # Foldable for the long ones (a code block pushes the first step off the
    # screen), but never folded on arrival: a click to read is the bug above.
    check(re.search(r'<details class="ws-part-lead"[^>]*\bopen\b', page),
          "the introduction folds, and starts open so nothing is hidden on arrival")
    check('<summary class="ws-part-lead-summary"' in page,
          "with a control to fold it away once it has been read")

    print("== A locked part still shows its introduction ==")
    # The lead is teaching prose, not a statement. It used to be carried inside
    # the first step's body, and a body is withheld while a step is locked — so
    # a whole section of the workshop was invisible until the participant got
    # there, while the exercise after it said "reuse the code above".
    fresh = requests.Session()
    fresh_nonce = get_nonce(fresh, "/login")
    fresh.post(BASE + "/login", data={"name": "bob", "password": "phase2-bob-pass",
                                      "nonce": fresh_nonce})
    locked_page = fresh.get(BASE + "/workshop/pypong").text
    locked_text = re.sub(r"<[^>]+>", " ", locked_page)
    check("ws-part-lead" in locked_page,
          "a participant who has solved nothing still gets the part introduction")
    check(lead_text[:40].strip() in re.sub(r"\s+", " ", locked_text),
          "including the prose the exercise after it says to reuse")
    # ...and the statement it introduces is still withheld.
    pad = locked_page.split(f'id="step-{ex_ids["pad-direction"]}"', 1)
    check(len(pad) == 2, "the locked step is on the page")
    step_html = pad[1].split('class="ws-step', 1)[0]
    check("ws-locked" in locked_page and "ws-statement" not in step_html,
          "while the locked step itself still hands out no statement")

    print("== A part named after the page does not print the title twice ==")
    cid = ex_ids["pad-direction"]
    was = admin.api("GET", f"/challenges/{cid}")["category"]
    title = re.search(r'class="ws-title[^"]*">([^<]+)<', page).group(1).strip()
    admin.api("PATCH", f"/challenges/{cid}", json={"category": title})
    renamed = s.get(BASE + "/workshop/pypong").text
    heading = re.search(r'class="ws-part-title[^"]*">(.*?)</h2>', renamed, re.S).group(1)
    check(title not in re.sub(r"<[^>]+>", "", heading),
          "the part heading drops a name identical to the page title")
    check("ws-part-count" in heading, "but keeps the counter, which carries information")
    admin.api("PATCH", f"/challenges/{cid}", json={"category": was})

    print("== Bonus steps, and which closing step ends the workshop ==")
    # PyPong is a single part with no bonuses, so both branches are exercised by
    # flipping the configs the sync writes and putting them back. That is the
    # same mechanism a multi-part subject uses, without needing one here.
    final_cfg = admin.api("GET", "/configs/workshop_final_step")["value"]
    outro_id = int(final_cfg)
    check(outro_id > 0, "the sync names the closing step that ends the subject")
    body = s.get(BASE + f"/api/v1/workshop/step/{outro_id}").json()["data"]["html"]
    check("How was this workshop?" in body,
          "the last closing step asks about the workshop")
    admin.api("PATCH", "/configs/workshop_final_step", json={"value": "0"})
    body = s.get(BASE + f"/api/v1/workshop/step/{outro_id}").json()["data"]["html"]
    check("How was this part?" in body and "How was this workshop?" not in body,
          "a closing step that is not the last asks about the part instead")
    admin.api("PATCH", "/configs/workshop_final_step", json={"value": str(outro_id)})

    first_ex = {"id": ex_ids["pad-direction"]}
    page_before = s.get(BASE + "/workshop/pypong").text
    check('data-optional="1"' not in page_before,
          "nothing is marked bonus in a subject that declares none")
    total_before = re.search(r'data-total="(\d+)"', page_before).group(1)
    admin.api("PATCH", "/configs/workshop_optional",
              json={"value": json.dumps([first_ex["id"]])})
    page_after = s.get(BASE + "/workshop/pypong").text
    check('data-optional="1"' in page_after and "ws-step-bonus" in page_after,
          "an optional step is labelled Bonus in the step list, not just in the counter")
    check(int(re.search(r'data-total="(\d+)"', page_after).group(1)) == int(total_before) - 1,
          "and it drops out of the progress total")
    admin.api("PATCH", "/configs/workshop_optional", json={"value": "[]"})

    print("== Idempotent re-sync ==")
    before = {c["id"] for c in api(s, "GET", "/challenges").json()["data"]}
    result2 = sync(str(REPO / "content/pypong"), BASE,
                   ADMIN["name"], ADMIN["password"], str(codes_file))
    ex_ids2, quiz_ids2 = result2["ex_ids"], result2["quiz_ids"]
    check(ex_ids2 == ex_ids and {k: v[0] for k, v in quiz_ids2.items()}
          == {k: v[0] for k, v in quiz_ids.items()},
          "re-sync maps to the same challenge ids (no duplicates)")
    check(attempt(s, first, codes["pad-direction"]) == "already_solved",
          "solves preserved across re-sync")

    print("== Content edit updates in place ==")
    tmp = Path(tempfile.mkdtemp()) / "pypong"
    shutil.copytree(REPO / "content/pypong", tmp)
    md = (tmp / "pypong.md").read_text()
    marker = "En vous inspirant du code précédent"
    md = md.replace(marker, "EDITED-SENTENCE " + marker, 1)
    (tmp / "pypong.md").write_text(md)
    sync(str(tmp), BASE, ADMIN["name"], ADMIN["password"], str(codes_file))
    detail = api(s, "GET", f"/challenges/{first}").json()["data"]
    check("EDITED-SENTENCE" in detail["description"], "edited description synced in place")

    # Restore the pristine content so a validation run never leaves the test
    # edit in the live instance.
    sync(str(REPO / "content/pypong"), BASE, ADMIN["name"], ADMIN["password"], str(codes_file))
    detail = api(s, "GET", f"/challenges/{first}").json()["data"]
    check("EDITED-SENTENCE" not in detail["description"],
          "pristine content restored after the edit test")

    print(f"\nPhase 2 validated: {checks_run} checks passed. Subject repo -> CTFd "
          "instance is automated, idempotent, and edit-safe.")


if __name__ == "__main__":
    main()
