#!/usr/bin/env python3
"""Phase 1 validation: the workshop plugin loads into pristine CTFd and its
`quiz` challenge type works end to end.

Against a FRESH instance (same reset as phase 0):
 1. setup wizard
 2. the `quiz` type is registered (= appears in the admin challenge-type dropdown)
 3. create the four quiz kinds via the API — content is the real quizzes of
    pypong_new (intro.md, pypong.md) plus the freeform example from
    workshop-metadata-tools docs/QUIZ.md; answers per plugins/workshop/README.md
 4. as an attendee: answers never leak through the API, wrong submissions are
    rejected, right ones (in any accepted format) solve and score

Usage: python3 scripts/phase1_validate.py [base_url]   (default http://localhost:8080)
"""
import re
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"

ADMIN = {"name": "admin", "email": "admin@example.com", "password": "phase1-admin-pass"}
ATTENDEE = {"name": "alice", "email": "alice@example.com", "password": "phase1-alice-pass"}

QUIZZES = [
    {
        "name": "Quiz : Commande d'initialisation",
        "category": "Prise en main de TIC-80",
        "description": "Quelle commande initialise un projet Python dans TIC-80 ?",
        "value": 10,
        "quiz_type": "single",
        "quiz_spec": {"items": [{"letter": "A", "text": "init python"}, {"letter": "B", "text": "new python"}, {"letter": "C", "text": "start python"}]},
        "quiz_answers": {"answer": "B"},
        "wrong": "A",
        "right": "b",  # case-insensitive
    },
    {
        "name": "Quiz : Lecture de documentation",
        "category": "Faire bouger le pad",
        "description": "Avec TIC-80, si je souhaite avoir l'état des touches haut et "
                       "bas du joueur 1, je dois utiliser dans mon code "
                       "(2 réponses correctes)",
        "value": 10,
        "quiz_type": "multiple",
        "quiz_spec": {"items": [{"letter": "A", "text": "btn(0)"}, {"letter": "B", "text": "btn(1)"}, {"letter": "C", "text": "btn(\"up\")"}, {"letter": "D", "text": "btn(\"down\")"}]},
        "quiz_answers": {"answers": ["A", "B"]},
        "wrong": "A,C",
        "right": "b, a",  # order-free, spaces tolerated
    },
    {
        "name": "Quiz : Le rebond",
        "category": "Créer la balle rebondissante",
        "description": "Faites correspondre la vitesse initiale de la balle sur un axe "
                       "avec la nouvelle vitesse de la balle sur ce même axe afin "
                       "qu'elle reparte dans l'autre sens ?",
        "value": 10,
        "quiz_type": "match",
        "quiz_spec": {"left": [{"letter": "A", "text": "0"}, {"letter": "B", "text": "2"}, {"letter": "C", "text": "-5"}], "right": [{"letter": "a", "text": "5"}, {"letter": "b", "text": "0"}, {"letter": "c", "text": "-2"}]},
        "quiz_answers": {"pairs": {"A": "b", "B": "c", "C": "a"}},
        "wrong": "A-c,B-b,C-a",
        "right": "B:c, A:b, C:a",  # order-free, ":" separator accepted
    },
    {
        # Freeform example from workshop-metadata-tools docs/QUIZ.md
        "name": "Quiz : Essay",
        "category": "Prise en main de TIC-80",
        "description": "Name the main designer of the C language.",
        "value": 10,
        "quiz_type": "freeform",
        "quiz_answers": {"patterns": [r"\britchie\b"]},
        "wrong": "Linus Torvalds",
        "right": "Dennis Ritchie",  # case-insensitive regex
    },
]

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


def run_setup(session):
    print("== Setup wizard ==")
    r = session.get(BASE + "/setup")
    if r.url.rstrip("/") == BASE:
        sys.exit("Instance already set up — reset first (see PLAN.md Phase 0 note).")
    nonce = get_nonce(session, "/setup")
    r = session.post(BASE + "/setup", data={
        "ctf_name": "Phase 1 - Quiz", "ctf_description": "Validation du type quiz",
        "user_mode": "users", "name": ADMIN["name"], "email": ADMIN["email"],
        "password": ADMIN["password"], "challenge_visibility": "private",
        "account_visibility": "public", "score_visibility": "public",
        "registration_visibility": "public", "verify_emails": "false",
        "ctf_theme": "core-beta", "theme_color": "", "start": "", "end": "",
        "_submit": "Finish", "nonce": nonce,
    })
    check(r.status_code == 200, "setup completed")
    session.nonce = get_nonce(session)


def check_type_registered(admin):
    print("== Plugin loaded ==")
    r = api(admin, "GET", "/challenges/types")
    types = r.json()["data"]
    check("quiz" in types, f"'quiz' registered in challenge types {sorted(types)}")


def create_quizzes(admin):
    print("== Create the four quiz kinds (admin API) ==")
    ids = []
    for q in QUIZZES:
        r = api(admin, "POST", "/challenges", json={
            "name": q["name"], "category": q["category"],
            "description": q["description"], "value": q["value"],
            "state": "visible", "type": "quiz",
            "quiz_type": q["quiz_type"], "quiz_answers": q["quiz_answers"],
            "quiz_spec": q.get("quiz_spec"),
        })
        check(r.status_code == 200, f"created {q['quiz_type']:8s} — {q['name']}")
        ids.append(r.json()["data"]["id"])
    return ids


def register_attendee():
    s = requests.Session()
    nonce = get_nonce(s, "/register")
    s.post(BASE + "/register", data={
        "name": ATTENDEE["name"], "email": ATTENDEE["email"],
        "password": ATTENDEE["password"], "nonce": nonce, "_submit": "Submit"})
    s.nonce = get_nonce(s)
    return s


def attempt(s, cid, submission):
    r = api(s, "POST", "/challenges/attempt",
            json={"challenge_id": cid, "submission": submission})
    return r.json()["data"]["status"]


def validate_grading(ids):
    print("== Attendee: no leaks, grading correct ==")
    s = register_attendee()
    for cid, q in zip(ids, QUIZZES):
        r = api(s, "GET", f"/challenges/{cid}")
        body = r.text
        check("quiz_answers" not in body
              and all(str(v) not in body for v in ["ritchie"]),
              f"{q['quiz_type']:8s} — answers absent from challenge payload")
        check(attempt(s, cid, q["wrong"]) == "incorrect",
              f"{q['quiz_type']:8s} — wrong submission rejected: {q['wrong']!r}")
        check(attempt(s, cid, q["right"]) == "correct",
              f"{q['quiz_type']:8s} — right submission accepted: {q['right']!r}")

    r = api(s, "GET", f"/users/me")
    score = r.json()["data"]["score"]
    check(score == sum(q["value"] for q in QUIZZES), f"score credited: {score} pts")


def main():
    admin = requests.Session()
    run_setup(admin)
    check_type_registered(admin)
    ids = create_quizzes(admin)
    validate_grading(ids)
    print(f"\nPhase 1 validated: {checks_run} checks passed. Plugin loads into "
          "pristine CTFd; the quiz type grades all four kinds server-side.")


if __name__ == "__main__":
    main()
