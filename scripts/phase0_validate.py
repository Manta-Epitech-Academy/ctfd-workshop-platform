#!/usr/bin/env python3
"""Phase 0 validation (PLAN.md §6): prove CTFd's native progressive unlocking
works before writing any platform code.

Against a FRESH stock CTFd instance (docker compose up):
 1. run the initial setup wizard
 2. create 3 challenges chained by `requirements` prerequisites
 3. as an attendee, verify locked challenges are absent from the API,
    solve the chain, and watch each next challenge appear

Content is real: the 3 challenges are the first exercises of PyPong
(github.com/Manta-Epitech-Academy/pypong_new), not invented material.
Flags are placeholders — Phase 0 validates progression, not validation.

Usage: python3 scripts/phase0_validate.py [base_url]   (default http://localhost:8080)
Exits non-zero on the first failed assertion. Idempotence: none — run on a
fresh instance (docker compose down -v && rm -rf .data && docker compose up -d).
"""
import re
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"

ADMIN = {"name": "admin", "email": "admin@example.com", "password": "phase0-admin-pass"}
ATTENDEE = {"name": "alice", "email": "alice@example.com", "password": "phase0-alice-pass"}

# Real PyPong content, copied VERBATIM from pypong_new/pypong.md (including the
# section context each exercise builds on) so a participant reading the challenge
# in stock CTFd has the full instructions. The "Indice" toggles become native
# CTFd hints (cost 0). CTFd renders descriptions as markdown server-side.
VALIDATION_NOTE = ("\n\n---\n\n*When your code works, validate the exercise with the "
                   "code given by the instructor.*")

# pypong_new/intro.md verbatim, minus the two QUIZ blocks (no quiz mechanic in
# stock CTFd; they become quiz-type challenges in a later phase). Served as the
# CTFd front page so a participant reads the intro before the exercises.
INTRO_MD = """\
# Prise en main de TIC-80

TIC-80 est une fantasy retro console open source conçu pour créer, jouer et partager de petits jeux. Prenez quelques instants pour la découvrir en testant un jeu : [https://tic80.com/play](https://tic80.com/play)

Tous les outils sont intégrés pour vous permettre de créer facilement un jeu : éditeurs de code, de sprites, de cartes, de sons, ainsi qu'un terminal.

Pour réaliser cet atelier, vous pouvez utiliser directement TIC-80 dans votre navigateur : https://tic80.com/create.

## Initialiser TIC-80 pour pouvoir utiliser le langage Python

il vous suffit de taper la commande suivante

```bash
new python
```

## Reset "hello world"

Le plus simple pour bien comprendre le fonctionnement de TIC-80 consiste de partir d'un environnement vierge, nous allons donc supprimer tous les éléments de la démo.

- Utilisez la touche **`ESC`** de votre clavier pour vous rendre dans l'éditeur de code
- Vous pouvez sélectionner tout texte avec **`CTRL`+`A`** et ensuite supprimer le code existant

## Affichage du pad et de l'écran de jeu

Rendez vous dans l'éditeur, normalement vous y êtes déjà, et écrivez le code suivant :

```python
# script:  python

def TIC():
 cls()
 rect(0,0,120,120,10)
 rect(45, 110, 30, 3, 12)
```

Vous pouvez maintenant retourner sur le terminal avec la touche **`ESC`** de votre clavier et taper la commande **`run`** pour lancer votre jeu

### Quelques explications

Chaque environnement et chaque langage de programmation possède ses spécificités. Pour cet atelier, nous utilisons le langage Python et l'environnement TIC-80 :

- Il faut expliquer à TIC-80 que le code sera écrit en langage Python en écrivant à la première ligne de l'éditeur `# script:  python`
- La partie principale du programme se déclare de la façon suivante `def TIC():`
- Le code en dessous de la partie principale du programme doit respecter une indentation propre au python : il faut un espace au début de chaque ligne comme dans l'exemple
- Nous pouvons utiliser des instructions prédéfinies comme `cls()` pour effacer l'écran (CLear Screen) et `rect()` pour dessiner des rectangles.

---

*Une fois TIC-80 pris en main, rendez-vous dans l'onglet **Atelier** pour commencer les exercices.*
"""

PAD_CONTEXT = """\
Pour arriver à faire bouger le pad avec le clavier, nous devons :

- Déclarez une variable `padx` qui va nous permettre modifier la valeur de la position de notre pad sur l'axe des abscisses
- Utilisez une condition `if` pour modifier la valeur de `padx` lorsque l'on appuie sur la touche **`←`** du clavier

Retournez dans l'éditeur pour modifier le code

```python
# script:  python
padx=45
padw=30
padh=3

def TIC():
 global padx

 if btn(2):
\t padx = padx - 2
 cls()
 rect(0,0,120,120,10)
 rect(padx, 110, padw, padh, 12)
```

Vous pouvez maintenant retourner sur le terminal avec la touche **`ESC`** de votre clavier et taper la commande **`run`** pour lancer votre jeu.

"""

CHALLENGES = [
    {
        "name": "Exercice 1 : Faites bouger le pad dans l'autre direction",
        "category": "Faire bouger le pad",
        "description": PAD_CONTEXT
        + "### Exercice 1\n\nEn vous inspirant du code précédent, vous devez faire "
          "en sorte que le pad puisse bouger à droite comme à gauche."
        + VALIDATION_NOTE,
        "hint": "La fonction **`btn`** prend en paramètre un nombre (3 : flèche droite "
                "du clavier). Une page dans l'aide contient le tableau de correspondance "
                "avec les touches du clavier : "
                "https://github.com/nesbox/TIC-80/wiki/key-map",
        "value": 25,
        "flag": "pad-deux-directions",
    },
    {
        "name": "Exercice 2 : Limitez les mouvement du pad",
        "category": "Faire bouger le pad",
        "description": "Vous voulez maintenant améliorer les mouvements du pad en "
                       "faisant en sorte qu'il reste dans le carré du jeu."
        + VALIDATION_NOTE,
        "hint": "Ajouter une condition supplémentaire dans votre `if` avec "
                "l'opérateur logique `and`",
        "value": 25,
        "flag": "pad-limite",
    },
    {
        "name": "Exercice 1 : Dessinez la balle",
        "category": "Créer la balle rebondissante",
        "description": "Vous allez utiliser la fonction `circ` pour créer la balle au "
                       "centre de l'écran, pensez à utiliser des variables `ballx` et "
                       "`bally` pour pouvoir déplacer votre balle dans l'écran de jeu."
        + VALIDATION_NOTE,
        "hint": "Vous pouvez aller consulter l'aide de TIC80 https://tic80.com/learn "
                "et retrouver tous les paramètres de la fonction `circ`",
        "value": 25,
        "flag": "balle-dessinee",
    },
]

checks_run = 0


def check(cond, label):
    global checks_run
    checks_run += 1
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}")
    if not cond:
        sys.exit(1)


def get_nonce(session, path="/"):
    r = session.get(BASE + path)
    m = re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", r.text)
    if not m:
        m = re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text)
    if not m:
        raise RuntimeError(f"no CSRF nonce found on {path} (status {r.status_code})")
    return m.group(1)


def api(session, method, path, **kwargs):
    headers = {"CSRF-Token": session.nonce, "Content-Type": "application/json"}
    r = session.request(method, BASE + "/api/v1" + path, headers=headers, **kwargs)
    return r


def run_setup(session):
    print("== Setup wizard ==")
    r = session.get(BASE + "/setup")
    if r.url.rstrip("/") == BASE:  # already redirected away: instance not fresh
        sys.exit("Instance already set up. Phase 0 needs a fresh one: "
                 "docker compose down && sudo rm -rf .data && docker compose up -d")
    nonce = get_nonce(session, "/setup")
    r = session.post(BASE + "/setup", data={
        "ctf_name": "Phase 0 - PyPong",
        "ctf_description": "Validation de la progression native CTFd",
        "user_mode": "users",
        "name": ADMIN["name"],
        "email": ADMIN["email"],
        "password": ADMIN["password"],
        "challenge_visibility": "private",
        "account_visibility": "public",
        "score_visibility": "public",
        "registration_visibility": "public",
        "verify_emails": "false",
        "ctf_theme": "core-beta",
        "theme_color": "",
        "start": "",
        "end": "",
        "_submit": "Finish",
        "nonce": nonce,
    })
    check(r.status_code == 200, "setup completed")
    session.nonce = get_nonce(session)


def create_chain(admin):
    print("== Create challenge chain (admin) ==")
    ids = []
    for ch in CHALLENGES:
        r = api(admin, "POST", "/challenges", json={
            "name": ch["name"], "category": ch["category"],
            "description": ch["description"], "value": ch["value"],
            "state": "visible", "type": "standard",
        })
        check(r.status_code == 200, f"created: {ch['name'][:50]}")
        cid = r.json()["data"]["id"]
        ids.append(cid)
        r = api(admin, "POST", "/flags", json={
            "challenge_id": cid, "type": "static", "content": ch["flag"]})
        check(r.status_code == 200, f"  flag set for #{cid}")
        # The md "Indice" toggle becomes a native CTFd hint, free to reveal.
        r = api(admin, "POST", "/hints", json={
            "challenge_id": cid, "content": ch["hint"], "cost": 0})
        check(r.status_code == 200, f"  hint set for #{cid}")

    # Chain: 1 -> 2 (hidden while locked) -> 3 (anonymized preview while locked)
    r = api(admin, "PATCH", f"/challenges/{ids[1]}",
            json={"requirements": {"prerequisites": [ids[0]]}})
    check(r.status_code == 200, f"#{ids[1]} requires #{ids[0]} (hidden when locked)")
    r = api(admin, "PATCH", f"/challenges/{ids[2]}",
            json={"requirements": {"prerequisites": [ids[1]], "anonymize": True}})
    check(r.status_code == 200, f"#{ids[2]} requires #{ids[1]} (anonymized preview)")
    return ids


def create_intro_page(admin):
    print("== Workshop intro as front page ==")
    # CTFd's setup wizard already creates the default index page — replace its
    # content rather than POSTing a duplicate route (unique key on `route`).
    r = api(admin, "GET", "/pages")
    index_id = next(p["id"] for p in r.json()["data"] if p["route"] == "index")
    r = api(admin, "PATCH", f"/pages/{index_id}", json={
        "title": "Prise en main de TIC-80",
        "route": "index",
        "content": INTRO_MD,
        "format": "markdown",
        "draft": False,
        "hidden": False,
        "auth_required": False,
    })
    check(r.status_code == 200, "intro.md published as index page")
    r = requests.get(BASE + "/")  # anonymous, fresh session
    check("Prise en main de TIC-80" in r.text and "new python" in r.text,
          "anonymous front page shows the workshop introduction")


def register_attendee():
    s = requests.Session()
    nonce = get_nonce(s, "/register")
    r = s.post(BASE + "/register", data={
        "name": ATTENDEE["name"], "email": ATTENDEE["email"],
        "password": ATTENDEE["password"], "nonce": nonce, "_submit": "Submit"})
    s.nonce = get_nonce(s)
    return s


def visible_challenges(s):
    r = api(s, "GET", "/challenges")
    return {c["id"]: c["name"] for c in r.json()["data"]}


def solve(s, cid, flag):
    """Returns the attempt status, or 'forbidden' — CTFd hard-403s attempts
    on challenges whose prerequisites are unmet (api/v1/challenges.py:728)."""
    r = api(s, "POST", "/challenges/attempt",
            json={"challenge_id": cid, "submission": flag})
    if r.status_code == 403:
        return "forbidden"
    return r.json()["data"]["status"]


def validate_progression(ids):
    print("== Attendee progression (the actual Phase 0 premise) ==")
    s = register_attendee()
    c1, c2, c3 = ids

    seen = visible_challenges(s)
    check(c1 in seen, "challenge 1 visible at start")

    r = api(s, "GET", f"/challenges/{c1}")
    detail = r.json()["data"]
    check("padx=45" in detail["description"] and "btn(2)" in detail["description"],
          "challenge 1 serves the FULL instructions (context + code sample)")
    check(len(detail.get("hints", [])) == 1, "challenge 1 exposes its hint")
    check(c2 not in seen, "challenge 2 LOCKED: absent from the list")
    check(c3 in seen and seen[c3] != CHALLENGES[2]["name"],
          f"challenge 3 LOCKED: anonymized as {seen.get(c3)!r}")

    check(solve(s, c2, CHALLENGES[1]["flag"]) == "forbidden",
          "solving locked challenge 2 is rejected (403)")

    check(solve(s, c1, CHALLENGES[0]["flag"]) == "correct", "solved challenge 1")
    seen = visible_challenges(s)
    check(c2 in seen and seen[c2] == CHALLENGES[1]["name"],
          "challenge 2 UNLOCKED and visible")

    check(solve(s, c2, CHALLENGES[1]["flag"]) == "correct", "solved challenge 2")
    seen = visible_challenges(s)
    check(seen.get(c3) == CHALLENGES[2]["name"], "challenge 3 de-anonymized")

    check(solve(s, c3, CHALLENGES[2]["flag"]) == "correct", "solved challenge 3")


def main():
    admin = requests.Session()
    run_setup(admin)
    create_intro_page(admin)
    ids = create_chain(admin)
    validate_progression(ids)
    print(f"\nPhase 0 premise validated: {checks_run} checks passed. "
          "Native `requirements` covers progressive unlocking with zero custom code.")


if __name__ == "__main__":
    main()
