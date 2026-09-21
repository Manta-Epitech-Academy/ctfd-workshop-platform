#!/usr/bin/env python3
"""Does the supervisor tier open exactly what it should, and nothing else?

    python3 scripts/supervisor_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §32). It sets a supervisor code on the instance, joins through the
form the way a teacher would, and then walks two lists: the pages a supervisor
must be able to open, and the pages and endpoints they must not. The second
list is the one that matters — the design's safety property is that a
supervisor is not an admin, so the check is that every admin door still
refuses them.

Shaped like scripts/workshop_check.py — positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back.** The code is restored to whatever it was, and
every account it creates is deleted through the admin API, so it can be run
against an instance a session is about to use. Interrupt it and the same
cleanup runs; kill -9 it and you are left with a code to clear and one or two
`svcheck*` accounts to delete.
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
CODE = "svcheck-" + RUN
fails = []

# What a supervisor opens, and what they must be refused. Every path under
# /admin that is not in the first list belongs in spirit to the second; these
# are the ones worth naming because each is a way to change the instance.
ALLOWED = [
    "/admin/workshop/stats",
    "/admin/workshop/answers",
    "/admin/workshop/answers.csv",
    "/admin/workshop/submissions",
    "/admin/workshop/submissions?type=incorrect",
    "/admin/workshop/feedback",
    "/admin/workshop/feedback.csv",
]
REFUSED_PAGES = [
    "/admin", "/admin/config", "/admin/users", "/admin/challenges",
    "/admin/statistics", "/admin/submissions", "/admin/pages", "/admin/notifications",
    "/admin/workshop/settings", "/admin/workshop/sync", "/admin/workshop/jump",
]
REFUSED_API = [
    "/api/v1/configs", "/api/v1/users/1", "/api/v1/statistics/users",
    "/api/v1/submissions", "/api/v1/workshop/jump/links",
]


def check(cond, label):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        fails.append(label)


def nonce(s, path="/"):
    r = s.get(BASE + path, timeout=20)
    m = (re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text)
         or re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", r.text))
    if not m:
        sys.exit(f"no CSRF nonce on {path} (HTTP {r.status_code})")
    return m.group(1)


class Admin:
    def __init__(self):
        self.s = requests.Session()
        n = nonce(self.s, "/login")
        self.s.post(BASE + "/login", data={"name": "admin", "password": ADMIN_PASS,
                                           "nonce": n, "_submit": "Submit"}, timeout=20)
        self.nonce = nonce(self.s)
        if "/login" in self.s.get(BASE + "/admin/config", timeout=20).url:
            sys.exit("admin login failed")

    def api(self, method, path, **kw):
        return self.s.request(method, BASE + "/api/v1" + path, timeout=20,
                              headers={"CSRF-Token": self.nonce,
                                       "Content-Type": "application/json"}, **kw)

    def config(self, key):
        r = self.api("GET", f"/configs/{key}")
        return (r.json().get("data") or {}).get("value") if r.ok else None

    def set_config(self, key, value):
        r = self.api("PATCH", "/configs", json={key: value})
        if not r.ok:
            sys.exit(f"could not set {key}: HTTP {r.status_code} {r.text[:200]}")

    def manage(self, **form):
        """A POST to the settings page's supervisor forms, as the admin."""
        return self.s.post(BASE + "/admin/workshop/supervisors", timeout=20,
                           data={"nonce": self.nonce, **form}, allow_redirects=True)

    def delete_user(self, name):
        r = self.api("GET", f"/users?view=admin&field=name&q={name}")
        for u in (r.json().get("data") or []) if r.ok else []:
            if u["name"] == name:
                self.api("DELETE", f"/users/{u['id']}")


admin = Admin()
previous_code = admin.config("workshop_supervisor_code") or ""
created = []


def cleanup():
    admin.set_config("workshop_supervisor_code", previous_code)
    for name in created:
        admin.delete_user(name)


atexit.register(cleanup)


def join(s, code, name):
    n = nonce(s, "/supervisor/join")
    return s.post(BASE + "/supervisor/join", timeout=20, allow_redirects=False,
                  data={"code": code, "name": name, "email": f"{name}@svcheck.invalid",
                        "password": "svcheck-pass-" + RUN, "nonce": n})


# --------------------------------------------------------------------------
print("closed door")
admin.set_config("workshop_supervisor_code", "")
anon = requests.Session()
check(anon.get(BASE + "/supervisor/join", timeout=20).status_code == 404,
      "with no code set, /supervisor/join is a 404")
check("supervisor" not in anon.get(BASE + "/login", timeout=20).text.lower(),
      "and the login page does not mention it")

print("open door")
admin.set_config("workshop_supervisor_code", CODE)
check(anon.get(BASE + "/supervisor/join", timeout=20).status_code == 200,
      "with a code set, /supervisor/join renders")
check("/supervisor/join" in anon.get(BASE + "/login", timeout=20).text,
      "and the login page links to it")

wrong = requests.Session()
name_wrong = "svcheckw" + RUN
r = join(wrong, "not-the-code", name_wrong)
# The message is translated (the instance speaks French by default), so the
# check is for an error alert on the re-rendered form, not for its words.
check(r.status_code == 200 and 'alert-danger' in r.text, "a wrong code is refused on the form")
check("/login" in wrong.get(BASE + "/admin/workshop/answers", timeout=20,
                            allow_redirects=False).headers.get("Location", ""),
      "and leaves the visitor signed out")
check(not admin.api("GET", f"/users?view=admin&field=name&q={name_wrong}").json().get("data"),
      "and creates no account")

sup = requests.Session()
name_sup = "svcheck" + RUN
created.append(name_sup)
r = join(sup, CODE, name_sup)
check(r.status_code == 302 and "/admin/workshop/stats" in r.headers.get("Location", ""),
      "the right code creates the account and lands on the statistics")

print("what a supervisor opens")
for path in ALLOWED:
    r = sup.get(BASE + path, timeout=20, allow_redirects=False)
    check(r.status_code == 200, f"GET {path} -> {r.status_code}")
page = sup.get(BASE + "/admin/workshop/answers", timeout=20).text
check('href="/admin/config"' not in page and 'href="/admin/users"' not in page
      and "/admin/challenges/" not in page,
      "the answer sheet carries no admin links for them")
check("Supervision" in sup.get(BASE + "/workshop", timeout=20).text,
      "the workshop navbar shows the Supervision entry")

print("what a supervisor is refused")
for path in REFUSED_PAGES:
    r = sup.get(BASE + path, timeout=20, allow_redirects=False)
    check(r.status_code in (302, 403) and "/admin/workshop" not in r.headers.get("Location", "/x"),
          f"GET {path} -> {r.status_code}")
n = nonce(sup, "/workshop")
for path in REFUSED_API:
    # Not following redirects: `admins_only` sends a non-JSON request to the
    # login page, and following it would read as a 200. A 404 is the other
    # honest refusal — core hides a hidden account from everybody but admins.
    r = sup.get(BASE + path, timeout=20, headers={"CSRF-Token": n}, allow_redirects=False)
    check(r.status_code in (302, 403, 404), f"GET {path} -> {r.status_code}")
    r = sup.get(BASE + path, timeout=20, allow_redirects=False,
                headers={"CSRF-Token": n, "Content-Type": "application/json"})
    check(r.status_code in (403, 404), f"GET {path} as JSON -> {r.status_code}")
r = sup.post(BASE + "/admin/workshop/supervisors", timeout=20, allow_redirects=False,
             data={"nonce": n, "action": "code", "code": "hijack"})
check(r.status_code in (302, 403) and admin.config("workshop_supervisor_code") == CODE,
      "they cannot change the supervisor code")
r = sup.post(BASE + "/admin/workshop/settings", timeout=20, allow_redirects=False,
             data={"nonce": n, "workshop_mode": "self_serve"})
check(r.status_code in (302, 403), "they cannot change the workshop mode")

print("hidden from the room")
users = admin.api("GET", "/users").json().get("data") or []
check(all(u["name"] != name_sup for u in users),
      "not in the public user list")
me = sup.get(BASE + "/api/v1/users/me", timeout=20).json().get("data") or {}
check(me.get("name") == name_sup, "but their own session works")
sheet = admin.s.get(BASE + "/admin/workshop/answers", timeout=20).text
check(name_sup not in sheet, "not among the participants on the answer sheet")

print("management")
settings = admin.s.get(BASE + "/admin/workshop/settings", timeout=20).text
check(name_sup in settings and "the code" in settings,
      "listed on the settings page, let in by the code")

name_user = "svchecku" + RUN
created.append(name_user)
r = admin.api("POST", "/users", json={"name": name_user, "email": f"{name_user}@svcheck.invalid",
                                      "password": "svcheck-pass-" + RUN, "type": "user",
                                      "verified": True})
check(r.ok, "a plain participant account can be created for the grant test")
r = admin.manage(action="grant", who=name_user)
check("notice=granted" in r.url, "an existing account can be granted the role by name")
user = requests.Session()
n = nonce(user, "/login")
user.post(BASE + "/login", data={"name": name_user, "password": "svcheck-pass-" + RUN,
                                 "nonce": n, "_submit": "Submit"}, timeout=20)
check(user.get(BASE + "/admin/workshop/stats", timeout=20, allow_redirects=False).status_code == 200,
      "and they open the statistics")
uid = next(u["id"] for u in (admin.api("GET", f"/users?view=admin&field=name&q={name_user}").json()["data"])
           if u["name"] == name_user)
r = admin.manage(action="revoke", user_id=uid)
check("notice=revoked" in r.url, "the role can be revoked")
r = user.get(BASE + "/admin/workshop/stats", timeout=20, allow_redirects=False)
check(r.status_code == 302, "and the statistics then bounce them to login")
check(user.get(BASE + "/workshop", timeout=20).status_code == 200,
      "while their account still signs in as a participant")

name_made = "svcheckm" + RUN
created.append(name_made)
r = admin.manage(action="create", name=name_made, email=f"{name_made}@svcheck.invalid",
                 password="svcheck-pass-" + RUN)
check("notice=created" in r.url, "an admin can create a supervisor account with no code")
r = admin.manage(action="create", name=name_made, email=f"{name_made}@svcheck.invalid",
                 password="svcheck-pass-" + RUN)
check("notice=invalid" in r.url and "why=" in r.url and not r.url.endswith("why="),
      "and is told why a duplicate is refused")
made = requests.Session()
n = nonce(made, "/login")
made.post(BASE + "/login", data={"name": name_made, "password": "svcheck-pass-" + RUN,
                                 "nonce": n, "_submit": "Submit"}, timeout=20)
check(made.get(BASE + "/admin/workshop/stats", timeout=20, allow_redirects=False).status_code == 200,
      "the created account signs in and opens the statistics")
mid = next(u["id"] for u in (admin.api("GET", f"/users?view=admin&field=name&q={name_made}").json()["data"])
           if u["name"] == name_made)
r = admin.manage(action="delete", user_id=uid)
check("notice=nobody" in r.url, "delete refuses an account that is not a supervisor")
r = admin.manage(action="delete", user_id=mid)
check("notice=deleted" in r.url, "delete removes a supervisor account")
check(not [u for u in (admin.api("GET", f"/users?view=admin&field=name&q={name_made}").json().get("data") or [])
           if u["name"] == name_made],
      "and the account is gone")
check(made.get(BASE + "/admin/workshop/stats", timeout=20, allow_redirects=False).status_code == 302,
      "and its session no longer opens anything")

admin.set_config("workshop_supervisor_code", "")
check(sup.get(BASE + "/admin/workshop/answers", timeout=20, allow_redirects=False).status_code == 200,
      "clearing the code keeps the supervisors already in")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
