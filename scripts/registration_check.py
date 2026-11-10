#!/usr/bin/env python3
"""Does a registration code make a cohort, and nothing else?

    python3 scripts/registration_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §42, §59). What it holds the door to:

  * a code is all a participant gives: the account has a generated name, an
    address under `.invalid`, and no password core's own form would accept;
  * several codes admit people at once, each account stays in the cohort of
    the code it came in on, and one cohort does not see another: on the
    scoreboard, in the users list, and in the room picker of the staff pages;
  * a code is closed by hand, by its window or by its cap, and a closed code
    keeps its accounts and admits nobody new;
  * banning one cohort retires its code and does not touch another;
  * **every refusal is the same answer**, byte for byte, whatever the reason;
  * a cap holds when a room presses the button together;
  * the throttle counts wrong codes: one typo read by a whole room does not
    close the door, a list of 300 does, and the door opens again by itself.

Shaped like scripts/external_check.py: positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back**: every account it makes is deleted, then every
code it made, including on an exception or a Ctrl-C. It closes the door on
purpose for up to a minute near the end and waits for it to open again, so a
run takes one to two minutes. Do not run it during a session.
"""
import atexit
import re
import secrets
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
RUN = uuid.uuid4().hex[:6]
PAGE = "/admin/workshop/codes"
DOOR = "/api/v1/workshop/door"
NAME = re.compile(r"[A-ZÉ][a-zéèêï]+-[A-ZÉ][a-zéèê]+-\d{2,4}")
PROPOSED = re.compile(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}")
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
first = admin.get(BASE + PAGE, timeout=20)
if first.status_code != 200:
    sys.exit(f"{PAGE} answers {first.status_code}: wrong password, or the "
             "plugin is older than the registration codes")
TOKEN = nonce(admin, PAGE)
H = {"CSRF-Token": TOKEN, "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + "/api/v1" + path, headers=H, timeout=20, **kw)


def post(**data):
    return admin.post(BASE + PAGE, timeout=20, allow_redirects=False,
                      data={"nonce": TOKEN, **data})


def rows():
    """code as shown -> {id, state, accounts, html}, read off the admin page."""
    html = admin.get(BASE + PAGE, timeout=20).text
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    found = {}
    for chunk in body.split("<tr>")[1:]:
        code = re.search(r"<code>([^<]+)</code>", chunk)
        ident = re.search(r'name="id" value="(\d+)"', chunk)
        state = re.search(r'<span class="badge badge-(?:success|info|secondary)">([^<]+)</span>', chunk)
        count = re.search(r"<summary>\s*(\d+)", chunk)
        if code and ident:
            found[code.group(1)] = {"id": int(ident.group(1)),
                                    "state": state.group(1) if state else None,
                                    "accounts": int(count.group(1)) if count else 0,
                                    "html": chunk}
    return found


made_users, made_codes, awards = [], [], []


def restore():
    admin.get(BASE + "/admin/workshop/stats?campus=", timeout=20)
    for a in awards:
        api("DELETE", f"/awards/{a}")
    for uid in made_users:
        api("DELETE", f"/users/{uid}")
    known = rows()
    for code in made_codes:
        if code in known:
            post(action="delete", id=known[code]["id"])


atexit.register(restore)


def create(code, **facts):
    r = post(action="create", code=code, **facts)
    if r.status_code == 302:
        made_codes.append(code)
    return r.status_code


class Visitor:
    """A browser nobody has signed in on: a session and its CSRF nonce."""

    def __init__(self):
        self.s = requests.Session()
        self.h = {"CSRF-Token": nonce(self.s, "/login"),
                  "Content-Type": "application/json"}
        self.signin = secrets.token_hex(32)

    def ask(self, code):
        return self.s.post(BASE + DOOR + "/code", headers=self.h, timeout=20,
                           json={"code": code})

    def register(self, code, signin=None):
        r = self.s.post(BASE + DOOR + "/register", headers=self.h, timeout=20,
                        json={"code": code, "signin": signin or self.signin})
        if r.status_code == 200:
            # Signing in changes the session's nonce.
            self.h["CSRF-Token"] = nonce(self.s, "/")
        return r

    def me(self):
        # Not followed: signed out, core answers with a redirect to the login
        # page, and that page is a 200.
        return self.s.get(BASE + "/api/v1/users/me", timeout=20,
                          allow_redirects=False)


def admitted(code):
    """Register a new visitor on a code. Returns `(visitor, user id, name)`."""
    v = Visitor()
    r = v.register(code)
    if r.status_code != 200:
        sys.exit(f"{code} did not admit a new account: {r.status_code} {r.text[:200]}")
    me = v.me().json()["data"]
    made_users.append(me["id"])
    return v, me["id"], me["name"]


def utc(delta_hours):
    when = datetime.now(timezone.utc) + timedelta(hours=delta_hours)
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


CODE_A = f"Lille-{RUN}"
CODE_B = f"PARIS-{RUN}"
CODE_C = f"LILLE2-{RUN}"
CAMPUS_A = f"Lille {RUN}"
CAMPUS_B = f"Paris {RUN}"

# ---------------------------------------------------------------------------
print("== the admin page makes codes ==")
proposed = re.search(r'id="ws-code"[^>]*value="([^"]*)"', first.text)
check(bool(proposed and PROPOSED.fullmatch(proposed.group(1))),
      f"the form proposes a random code: {proposed.group(1) if proposed else None}")
check(create(CODE_A, campus=CAMPUS_A, event="Coding Club",
             event_date="2026-11-10", content="pacman") == 302,
      f"{CODE_A} is created with a campus, an event, a date and a content")
check(create(CODE_B, campus=CAMPUS_B, event="Stage de seconde") == 302,
      f"{CODE_B} is created with no date")
check(create(CODE_C, campus=f"  LILLE {RUN.upper()} ") == 302,
      f"{CODE_C} is created with the campus alone, written another way")
table = rows()
check(all(table.get(c, {}).get("state") == "admits people"
          for c in (CODE_A, CODE_B, CODE_C)), "all three admit people")
check("Coding Club" in table[CODE_A]["html"] and "2026-11-10" in table[CODE_A]["html"]
      and "content: pacman" in table[CODE_A]["html"],
      "and the page shows what the code carries")

for label, form in (
        ("the same code in another case, with a space", {"code": f"lille {RUN}"}),
        ("the same code with digits for its letters", {"code": f"L1LLE-{RUN}".replace("LLE", "11E")}),
        ("a code of three characters", {"code": "abc"}),
        ("a code of thirty", {"code": "A" * 30}),
        ("a cap of zero", {"code": f"CAP0-{RUN}", "cap": "0"}),
        ("a date that is not one", {"code": f"DATE-{RUN}", "event_date": "demain"}),
        ("a window that closes before it opens",
         {"code": f"WIN-{RUN}", "valid_from": utc(2), "valid_until": utc(1)})):
    check(create(**form) == 400, f"refused: {label}")
check(len(rows()) == len(table), "and none of those made a code")

# ---------------------------------------------------------------------------
print("\n== the door answers a code, and only one way when it is a no ==")
v = Visitor()
bare = v.s.post(BASE + DOOR + "/code", json={"code": CODE_A}, timeout=20)
check(bare.status_code == 403 and "success" not in bare.text,
      f"without the session's nonce core refuses the request: {bare.status_code}")
yes = v.ask(CODE_A)
check(yes.status_code == 200 and yes.json() == {"success": True},
      f"{CODE_A} is a code: {yes.status_code}")
check(v.ask(f"  l1lle {RUN.upper()}  ").status_code == 200,
      "typed in another case, with spaces and a 1 for the I: still the code")
check(v.me().status_code != 200, "asking made no account and signed nobody in")
no = v.ask(f"NOPE-{RUN}")
REFUSAL = (no.status_code, no.content)
check(no.status_code == 403 and no.json().get("success") is False
      and "encadrant" in no.json().get("message", ""),
      f"an unknown code is refused, in French: {no.json().get('message')}")


def refused(r):
    return (r.status_code, r.content) == REFUSAL


check(refused(v.ask("")) and refused(v.ask("abc")) and refused(v.ask("A" * 40)),
      "empty, too short, too long: the same answer")
check(refused(v.s.post(BASE + DOOR + "/code", headers=v.h, data="[1, 2]", timeout=20)),
      "a body that is not what the door sends: the same answer")
check(refused(v.register(CODE_A, signin="abc")) and refused(v.register(CODE_A, signin="G" * 64))
      and refused(v.register(CODE_A, signin=secrets.token_hex(32).upper())),
      "a sign-in value of the wrong shape: the same answer")
check(v.me().status_code != 200, "and still nobody is signed in")

# ---------------------------------------------------------------------------
print("\n== a code is all a participant gives ==")
users_before = len(api("GET", "/users?view=admin&per_page=1").json().get("data") or [])
alpha, A_ID, A_NAME = admitted(CODE_A)
alpha2, A2_ID, A2_NAME = admitted(f"lille{RUN}")
beta, B_ID, B_NAME = admitted(CODE_B)
gamma, C_ID, C_NAME = admitted(CODE_C)
check(all(NAME.fullmatch(n) for n in (A_NAME, A2_NAME, B_NAME, C_NAME)),
      f"the names are generated: {A_NAME}, {A2_NAME}, {B_NAME}, {C_NAME}")
seen = api("GET", f"/users/{A_ID}").json()["data"]
check(seen["email"].endswith("@token.invalid") and re.fullmatch(r"[0-9a-f]{32}@token\.invalid", seen["email"]),
      f"the address is random and under .invalid: {seen['email']}")
check(not seen.get("affiliation") and not seen.get("website") and not seen.get("country")
      and not seen.get("fields"), "nothing else on the account is filled")
for guess in ("", "None", A_NAME):
    s = requests.Session()
    s.post(BASE + "/login", timeout=20,
           data={"name": A_NAME, "password": guess,
                 "nonce": nonce(s, "/login?staff=1"), "_submit": "Submit"})
    check(s.get(BASE + "/api/v1/users/me", timeout=20,
                allow_redirects=False).status_code != 200,
          f"core's own form does not open it with the password {guess!r}")

again = Visitor()
r = again.register(CODE_A, signin=alpha.signin)
check(r.status_code == 200 and r.json()["data"]["name"] == A_NAME,
      "the same sign-in value sent twice is the same account")
r = again.register(f"NOPE-{RUN}", signin=alpha.signin)
check(r.status_code == 200 and r.json()["data"]["name"] == A_NAME,
      "whatever the code says the second time")
table = rows()
check((table[CODE_A]["accounts"], table[CODE_B]["accounts"], table[CODE_C]["accounts"]) == (2, 1, 1),
      "the page counts two accounts, one and one")
check(A_NAME in table[CODE_A]["html"] and A2_NAME in table[CODE_A]["html"]
      and B_NAME not in table[CODE_A]["html"] and B_NAME in table[CODE_B]["html"],
      "each account under the code it came in on")

# ---------------------------------------------------------------------------
print("\n== a cohort is a code ==")
for uid in (A_ID, A2_ID, B_ID, C_ID):
    r = api("POST", "/awards", json={"user_id": uid, "name": "probe",
                                     "value": 10, "category": "probe"})
    if r.status_code == 200:
        awards.append(r.json()["data"]["id"])


def board(s):
    return [row["name"] for row in
            (s.get(BASE + "/api/v1/scoreboard", timeout=20).json().get("data") or [])]


def listed(s):
    return [row["name"] for row in
            (s.get(BASE + "/api/v1/users", timeout=20).json().get("data") or [])]


for who, s, mine, theirs in (
        (CODE_A, alpha.s, [A_NAME, A2_NAME], [B_NAME, C_NAME]),
        (CODE_B, beta.s, [B_NAME], [A_NAME, A2_NAME, C_NAME]),
        (CODE_C, gamma.s, [C_NAME], [A_NAME, A2_NAME, B_NAME])):
    names = board(s)
    check(all(n in names for n in mine) and not [n for n in theirs if n in names],
          f"the scoreboard of {who} has its own cohort only")
    names = listed(s)
    check(all(n in names for n in mine) and not [n for n in theirs if n in names],
          f"the users list of {who} too")
check(alpha.s.get(BASE + f"/api/v1/users/{A2_ID}", timeout=20).status_code == 200
      and alpha.s.get(BASE + f"/api/v1/users/{B_ID}", timeout=20).status_code == 404
      and alpha.s.get(BASE + f"/api/v1/users/{C_ID}", timeout=20).status_code == 404,
      "an account opens a profile of its own cohort and no other, same campus or not")
names = board(admin)
check(all(n in names for n in (A_NAME, A2_NAME, B_NAME, C_NAME)),
      "the admin's scoreboard has all four")

print("\n== and the room picker of the staff pages knows it ==")
STATS = "/admin/workshop/stats"


def stats(**scope):
    html = admin.get(BASE + STATS, params=scope, timeout=20).text
    people = re.search(r"<b>(\d+)</b>\s*<small[^>]*>participant", html)
    return html, int(people.group(1)) if people else None


REF_A, REF_B = "@" + CAMPUS_A.casefold(), "@" + CAMPUS_B.casefold()
ID_A, ID_B, ID_C = (table[c]["id"] for c in (CODE_A, CODE_B, CODE_C))
html, everyone = stats(campus="")
check(html.count(f'value="{REF_A}"') == 1 and html.count(f'value="{REF_B}"') == 1,
      "the two campuses are listed once each, however they were written")
html, people = stats(campus=REF_A)
check(people == 3, f"campus {CAMPUS_A}: three participants ({people})")
check("Coding Club · 2026-11-10" in html and f">{CODE_C}<" in html
      and "Stage de seconde" not in html,
      "its two rooms are named by event and date, or by the code")
check(stats(campus=REF_A, session=ID_A)[1] == 2, "the room of the first code: two")
check(stats(campus=REF_A, session=ID_C)[1] == 1, "the room of the other: one")
check(stats(campus=REF_A, session=ID_B)[1] == 3,
      "a room of another campus is not a choice: the campus, whole")
check(stats(campus=REF_B)[1] == 1, f"campus {CAMPUS_B}: one participant")
check(stats(campus="")[1] == everyone and everyone >= 4, "and the whole instance again")

# ---------------------------------------------------------------------------
print("\n== retiring a code keeps its accounts and admits nobody new ==")
check(post(action="retire", id=ID_A).status_code == 302
      and rows()[CODE_A]["state"] == "retired", "the code is retired")
check(refused(Visitor().ask(CODE_A)) and refused(Visitor().register(CODE_A)),
      "the door refuses it, with the same answer as an unknown code")
check(alpha.me().status_code == 200 and rows()[CODE_A]["accounts"] == 2,
      "its accounts are there and still signed in")
check(Visitor().ask(CODE_B).status_code == 200, "the other code still admits")
check(post(action="reopen", id=ID_A).status_code == 302
      and Visitor().ask(CODE_A).status_code == 200, "reopened, it admits again")

print("\n== banning a cohort does not touch another ==")
check(post(action="ban", id=ID_A).status_code == 302, "the cohort is banned")
check(alpha.me().status_code == 403 and alpha2.me().status_code == 403,
      "its two accounts are shut out")
check(beta.me().status_code == 200 and gamma.me().status_code == 200,
      "the other cohorts are not, the one of the same campus included")
row = rows()[CODE_A]
check(row["state"] == "retired" and "badge-danger" in row["html"],
      "the code is retired with it, and the page says banned")
check(refused(Visitor().register(CODE_A, signin=alpha.signin)),
      "a banned account's sign-in value is refused like a wrong code")
check(refused(Visitor().register(CODE_A)), "and the code makes no new account")
banned = api("GET", f"/users/{A_ID}").json()["data"]
check(banned.get("banned") is True and banned["name"] == A_NAME,
      "banned, not deleted")
check(post(action="unban", id=ID_A).status_code == 302
      and alpha.me().status_code == 200 and alpha2.me().status_code == 200,
      "un-banned, both are back")
check(rows()[CODE_A]["state"] == "retired", "the code stays retired until reopened")
post(action="reopen", id=ID_A)

# ---------------------------------------------------------------------------
print("\n== a window and a cap close a code by themselves ==")
EARLY, LATE, FULL = f"EARLY-{RUN}", f"LATE-{RUN}", f"FULL-{RUN}"
check(create(EARLY, valid_from=utc(1)) == 302
      and create(LATE, valid_until=utc(-1)) == 302
      and create(FULL, cap="3", valid_from=utc(-1), valid_until=utc(1)) == 302,
      "three codes: one for later, one that is over, one for three accounts")
table = rows()
check((table[EARLY]["state"], table[LATE]["state"], table[FULL]["state"])
      == ("not open yet", "closed by its window", "admits people"),
      "the page says which is which")
check(refused(Visitor().ask(EARLY)) and refused(Visitor().register(EARLY))
      and refused(Visitor().ask(LATE)) and refused(Visitor().register(LATE)),
      "too early and too late are the same answer as an unknown code")

visitors = [Visitor() for _ in range(9)]
with ThreadPoolExecutor(max_workers=9) as pool:
    answers = list(pool.map(lambda one: one.register(FULL), visitors))
won = [one for one, r in zip(visitors, answers) if r.status_code == 200]
for one in won:
    made_users.append(one.me().json()["data"]["id"])
check(len(won) == 3, f"nine press together on a cap of three: {len(won)} get in")
check(all(refused(r) for r in answers if r.status_code != 200),
      "and the six others get the same answer as an unknown code")
check(rows()[FULL]["state"] == "full" and rows()[FULL]["accounts"] == 3,
      "the page says full, with three accounts")
check(refused(Visitor().ask(FULL)), "the door refuses a tenth")
check(Visitor().register(FULL, signin=won[0].signin).status_code == 200,
      "and still knows one of the three")

print("\n== only a code nobody came in on can be deleted ==")
check(post(action="delete", id=table[FULL]["id"]).status_code == 400
      and FULL in rows(), "a code with accounts is not deleted")
check(post(action="delete", id=table[EARLY]["id"]).status_code == 302
      and EARLY not in rows(), "an unused one is")
check(post(action="delete", id=999999).status_code == 404
      and post(action="explode", id=ID_A).status_code == 400,
      "an unknown code is a 404, an unknown action a 400")

# ---------------------------------------------------------------------------
print("\n== the throttle counts wrong codes ==")
SOON = f"SOON-{RUN}"
early_bird = Visitor()
check(all(refused(early_bird.ask(SOON)) for _ in range(12)),
      "a room types a code twelve times before it exists")
check(create(SOON) == 302 and early_bird.ask(SOON).status_code == 200,
      "created, it works at once: their tries did not close it")

# The brake counts per minute of the clock: start the two halves in one.
left = 60 - time.time() % 60
if left < 40:
    print(f"       (waiting {left:.0f} s for a new minute)")
    time.sleep(left + 1)
typo = Visitor()
check(all(refused(typo.ask(f"TYPO-{RUN}")) for _ in range(350)),
      "one typo, 350 times: refused each time")
check(typo.ask(CODE_B).status_code == 200,
      "and the door is still open: one wrong text is counted ten times, no more")
lister = Visitor()
for n in range(300):
    lister.ask(f"LIST-{RUN}-{n}")
check(refused(lister.ask(CODE_B)) and refused(Visitor().register(CODE_B)),
      "300 different wrong codes in a minute: the door refuses a right one too")
check(beta.me().status_code == 200, "people already in are not concerned")
left = 60 - time.time() % 60
print(f"       (waiting {left:.0f} s for the minute to end)")
time.sleep(left + 1)
check(lister.ask(CODE_B).status_code == 200, "the minute over, the door is open again")

# ---------------------------------------------------------------------------
print("\n== and it puts the instance back ==")
restore()
left_over = [c for c in made_codes if c in rows()]
check(not left_over, f"no code of this run is left: {left_over}")
gone = [u for u in made_users if api("GET", f"/users/{u}").status_code != 404]
check(not gone, f"no account either: {gone}")
made_users.clear()
made_codes.clear()
awards.clear()

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
