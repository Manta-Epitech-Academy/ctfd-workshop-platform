#!/usr/bin/env python3
"""Does the other door open only for a live code, and stay invisible otherwise?

    python3 scripts/external_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §42). Four things, and the last is the one a human reviewer will not
catch by reading a diff:

  * the admin owns both halves — the switch and the codes — and either one
    missing is a 404, not a closed page;
  * several codes admit people at once, and each account keeps the one it came
    in on, in the CTFd custom field;
  * a cohort can be revoked and restored by its code, without touching another
    cohort, and revoking bans rather than deletes;
  * **nothing anywhere names the route** except the admin page.

Shaped like scripts/supervisor_check.py — positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back**: the switch and the codes are restored to what
they were and every account it makes is deleted, including on an exception or a
Ctrl-C. Run it against a development instance — it fills the throttle on
purpose, so the address it runs from cannot use the door for fifteen minutes
afterwards, and it says so rather than failing obscurely on a second run.
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
RUN = uuid.uuid4().hex[:6]
ROUTE = "/external/join"
PAGE = "/admin/workshop/external"
CODE_A = f"RUN-EVENT-{RUN}"
CODE_B = f"PAR-LYCEE-{RUN}"
WINDOW_MINUTES = 15
fails = []


def check(cond, label):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


def session():
    return requests.Session()


def nonce(s, path="/"):
    r = s.get(BASE + path, timeout=20)
    m = (re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text)
         or re.search(r"['\"]csrfNonce['\"]\s*:\s*['\"]([0-9a-f]+)", r.text))
    return m.group(1) if m else None


admin = session()
admin.post(BASE + "/login", timeout=20,
           data={"name": "admin", "password": ADMIN_PASS,
                 "nonce": nonce(admin, "/login"), "_submit": "Submit"})
TOKEN = nonce(admin, PAGE)
if TOKEN is None or admin.get(BASE + PAGE, timeout=20).status_code != 200:
    sys.exit(f"{PAGE} does not answer — not an admin session, or an old image")


def post(**data):
    return admin.post(BASE + PAGE, timeout=20, data={"nonce": TOKEN, **data})


def api(method, path, **kw):
    return admin.request(method, BASE + "/api/v1" + path,
                         headers={"CSRF-Token": TOKEN,
                                  "Content-Type": "application/json"},
                         timeout=20, **kw)


def page():
    return admin.get(BASE + PAGE, timeout=20).text


def live_codes(html=None):
    """Only the codes that still admit people.

    Parsed per row and not with one `findall` over the table: a retired code
    keeps a row for as long as its cohort exists, so a scrape of every `<code>`
    cannot tell "live" from "was live", and a test that cannot tell them apart
    passes when retiring stops working.
    """
    html = html or page()
    body = html.split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    out = []
    for row in body.split("<tr>"):
        if "badge-success" not in row:
            continue
        m = re.search(r"<code>([^<]+)</code>", row)
        if m:
            out.append(m.group(1))
    return out


start = page()
was_on = bool(re.search(r'id="ws-ext-on"[^>]*\bchecked', start))
was_codes = live_codes(start)
made = []


def restore():
    for code in live_codes():
        if code not in was_codes:
            post(action="remove", code=code)
    for code in was_codes:
        post(action="add", code=code)
    post(action="switch", **({"enabled": "on"} if was_on else {}))
    for uid in made:
        api("DELETE", f"/users/{uid}")


atexit.register(restore)


def join(code, name):
    s = session()
    n = nonce(s, ROUTE)
    if n is None:
        return s, None
    s.post(BASE + ROUTE, timeout=20, allow_redirects=True,
           data={"nonce": n, "code": code, "name": name,
                 "email": f"{name}@example.invalid", "password": f"{name}-pass"})
    row = next((u for u in (api("GET", f"/users?view=admin&q={name}&field=name")
                            .json().get("data") or []) if u["name"] == name), None)
    if row:
        made.append(row["id"])
    return s, row


post(action="switch", enabled="on")
post(action="add", code=CODE_A)
if requests.get(BASE + ROUTE, timeout=20).status_code == 404:
    sys.exit(f"the throttle from an earlier run still holds this address down. "
             f"Wait {WINDOW_MINUTES} minutes, or run from another address.")

print("== the admin owns both halves ==")
post(action="remove", code=CODE_A)
check(requests.get(BASE + ROUTE, timeout=20).status_code == 404,
      "switched on with no code -> 404")
post(action="add", code=CODE_A)
post(action="switch")                       # no `enabled` key: off
check(requests.get(BASE + ROUTE, timeout=20).status_code == 404,
      "a code kept, but switched off -> 404")
check(CODE_A in live_codes(), "and the code is kept, not thrown away")
post(action="switch", enabled="on")
check(requests.get(BASE + ROUTE, timeout=20).status_code == 200,
      "both -> the form is served")

print("\n== several codes at once, each remembered on its accounts ==")
post(action="add", code=CODE_B)
check({CODE_A, CODE_B} <= set(live_codes()), f"both are live: {live_codes()}")
post(action="add", code=CODE_A.lower())
check(len([c for c in live_codes() if c.lower() == CODE_A.lower()]) == 1,
      "adding one that differs only in case does not make a second")

_s, a1 = join(CODE_A, f"exta-{RUN}")
_s, b1 = join(CODE_B, f"extb-{RUN}")
check(a1 and b1, "one account joins on each code")
if a1 and b1:
    full = api("GET", f"/users/{a1['id']}").json()["data"]
    check(full.get("type") == "user", f"an ordinary participant ({full.get('type')})")
    check(full.get("hidden") is not True, "not hidden: on the scoreboard like anybody")
    fields = {f.get("name"): f.get("value") for f in (full.get("fields") or [])}
    check(fields.get("External code") == CODE_A,
          f"and carries the code it came in on: {fields}")
    other = {f.get("name"): f.get("value") for f in
             (api("GET", f"/users/{b1['id']}").json()["data"].get("fields") or [])}
    check(other.get("External code") == CODE_B, f"the other cohort's is its own: {other}")

print("\n== that account can do nothing else ==")
who = session()
who.post(BASE + "/login", timeout=20,
         data={"name": f"exta-{RUN}", "password": f"exta-{RUN}-pass",
               "nonce": nonce(who, "/login"), "_submit": "Submit"})
doors = {d: who.get(BASE + d, timeout=20, allow_redirects=False).status_code
         for d in ("/admin", "/admin/users", PAGE, "/admin/workshop/stats",
                   "/admin/workshop/answers", "/admin/workshop/plugin")}
check(all(c in (302, 403, 404) for c in doors.values()),
      f"every staff and admin door refuses it: {doors}")
check(who.get(BASE + "/workshop", timeout=20).status_code == 200,
      "while the workshop itself opens, like any participant")

print("\n== revoke by code, and only that code ==")
post(action="revoke", code=CODE_A)
check(api("GET", f"/users/{a1['id']}").json()["data"].get("banned") is True,
      "the cohort is banned")
check(api("GET", f"/users/{b1['id']}").json()["data"].get("banned") is not True,
      "and the other cohort is untouched")
check(api("GET", f"/users/{a1['id']}").status_code == 200,
      "banned, not deleted: the account is still there with its solves")
post(action="restore", code=CODE_A)
check(api("GET", f"/users/{a1['id']}").json()["data"].get("banned") is not True,
      "and it can be undone")

print("\n== retiring a code keeps its people ==")
post(action="remove", code=CODE_A)
check(CODE_A not in live_codes(), "the code no longer admits anybody")
check(CODE_A in page(), "but the cohort is still listed, retired")
_s, late = join(CODE_A, f"extlate-{RUN}")
check(late is None, "and it no longer works at the door")

print("\n== a wrong code, and running out of tries ==")
for _ in range(12):
    s = session()
    n = nonce(s, ROUTE)
    if n is None:
        break
    s.post(BASE + ROUTE, timeout=20,
           data={"nonce": n, "code": "not-a-code", "name": f"no-{RUN}",
                 "email": f"no-{RUN}@example.invalid", "password": "nope"})
check(not (api("GET", f"/users?view=admin&q=no-{RUN}&field=name").json().get("data") or []),
      "a wrong code makes no account")
check(requests.get(BASE + ROUTE, timeout=20).status_code == 404,
      "and enough of them stop the address being answered, with a 404")

print("\n== and nothing tells anybody it is there ==")
surfaces = {
    "/": requests.get(BASE + "/", timeout=20).text,
    "/login": requests.get(BASE + "/login", timeout=20).text,
    "/supervisor/join": requests.get(BASE + "/supervisor/join", timeout=20).text,
    "/workshop (signed in)": admin.get(BASE + "/workshop", timeout=20).text,
    "/challenges (signed in)": admin.get(BASE + "/challenges", timeout=20).text,
    "/admin/workshop/settings": admin.get(BASE + "/admin/workshop/settings", timeout=20).text,
}
for where, html in surfaces.items():
    check("external/join" not in html, f"{where} does not name the route")
check("external/join" in page(), "the admin page does — the one place that should")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
