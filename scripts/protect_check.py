#!/usr/bin/env python3
"""Can an admin delete, demote or ban the instance's first administrator?

    python3 scripts/protect_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §40). It walks the three refusals against the protected account, then
the same three against a throwaway account to prove the guard is narrow and has
not simply broken user administration, and finally the two identification rules
— the pin, and the fallback to the admin *named* `admin`, which is what an
instance restored from a CTFd backup still has when its ids have all moved.

The pin is proved by moving it ONTO the throwaway account and watching the
throwaway become undeletable. It is never proved by deleting the account the
pin just left: that account is the real administrator, and a test that has to
destroy what it is testing is not a test.

Shaped like scripts/supervisor_check.py — positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**Run it against a development instance.** It aims real destructive requests
at a real administrator and expects every one to bounce; if the guard is not
there, the reversible ones are undone and the irreversible one is never sent.

**It puts the instance back**: the throwaway account it creates is deleted at
exit and the pin is restored to whatever it was, including on an exception or a
Ctrl-C. It never writes to the real protected account — every request aimed at that one is expected to be refused, and a
failure of this script is a request that went through, not one that landed.
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
fails = []


def check(cond, label):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


s = requests.Session()


def nonce(path="/"):
    r = s.get(BASE + path, timeout=20)
    m = (re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text)
         or re.search(r"['\"]csrfNonce['\"]\s*:\s*['\"]([0-9a-f]+)", r.text))
    if m is None:
        sys.exit(f"no CSRF nonce on {path} — is the admin password right?")
    return m.group(1)


s.post(BASE + "/login", timeout=20,
       data={"name": "admin", "password": ADMIN_PASS,
             "nonce": nonce("/login"), "_submit": "Submit"})
TOKEN = nonce("/admin/users")
H = {"CSRF-Token": TOKEN, "Content-Type": "application/json"}


def api(method, path, **kw):
    return s.request(method, BASE + "/api/v1" + path, headers=H, timeout=20, **kw)


r = api("GET", "/workshop/protected-user")
if r.status_code != 200:
    sys.exit(f"/api/v1/workshop/protected-user answered {r.status_code} — "
             "not an admin session, or an image without this change")
KEEPER = r.json()["data"]
if not KEEPER:
    sys.exit("this instance has no admin account at all; nothing to protect")
KID, KNAME = KEEPER["id"], KEEPER["name"]
print(f"protected account: #{KID} {KNAME!r}\n")

# A throwaway admin, to prove the guard refuses the protected account and not
# simply every admin.
SPARE_NAME = f"pcheck-{RUN}"
SPARE_PASS = f"pcheck-{RUN}-pass"
spare = api("POST", "/users", json={
    "name": SPARE_NAME, "email": f"pcheck-{RUN}@example.invalid",
    "password": SPARE_PASS, "type": "admin", "verified": True,
    "hidden": True})
if spare.status_code != 200:
    sys.exit(f"could not create the throwaway account: {spare.status_code} {spare.text[:200]}")
SPARE = spare.json()["data"]["id"]
atexit.register(lambda: api("DELETE", f"/users/{SPARE}"))


def still_there(uid):
    return api("GET", f"/users/{uid}").status_code == 200


def field(uid, key):
    return api("GET", f"/users/{uid}").json()["data"].get(key)


print("== the protected administrator ==")
# Order matters, and it is a safety property rather than a style choice. The
# two reversible attacks run first; the delete runs only if they were refused.
# A delete that is NOT refused destroys the very account this script exists to
# protect, so it is never the probe that finds out whether the guard is live.
r = api("PATCH", f"/users/{KID}", json={"type": "user"})
guard_live = r.status_code == 403
check(guard_live, f"demotion to `user` is refused ({r.status_code})")
if not guard_live:
    api("PATCH", f"/users/{KID}", json={"type": "admin"})   # put it back
check(field(KID, "type") == "admin", "and the account is still an admin")

r = api("PATCH", f"/users/{KID}", json={"banned": True})
check(r.status_code == 403, f"banning is refused ({r.status_code})")
if r.status_code != 403:
    guard_live = False
    api("PATCH", f"/users/{KID}", json={"banned": False})   # put it back
check(field(KID, "banned") is not True, "and the account is not banned")

if guard_live:
    r = api("DELETE", f"/users/{KID}")
    check(r.status_code == 403, f"DELETE is refused ({r.status_code})")
    check("cannot be deleted" in r.text, f"with a reason: {r.json().get('errors')}")
    check(still_there(KID), "and the account is still there")
else:
    check(False, "DELETE not attempted: the guard let a reversible change "
                 "through, so an irreversible one would have landed")

r = api("PATCH", f"/users/{KID}", json={"name": f"renamed-{RUN}"})
check(r.status_code == 403, f"renaming is refused ({r.status_code})")
check(field(KID, "name") == KNAME, "and the account keeps its name")

r = api("PATCH", f"/users/{KID}", json={"name": KNAME, "email": field(KID, "email"),
                                        "type": "admin", "banned": False})
check(r.status_code == 200,
      f"a save that changes none of those still goes through ({r.status_code}) "
      "— the admin form posts the whole row every time")

r = api("PATCH", f"/users/{KID}", json={"affiliation": f"protect-check-{RUN}"})
check(r.status_code == 200, f"and an ordinary edit still goes through ({r.status_code})")
api("PATCH", f"/users/{KID}", json={"affiliation": None})

print("\n== taking the account over, from another admin's session ==")
# `spare_s` is a second admin: the guard's actor test has to be exercised by
# somebody who is not the protected account.
spare_s = requests.Session()
m = re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"',
              spare_s.get(BASE + "/login", timeout=20).text)
spare_s.post(BASE + "/login", timeout=20,
             data={"name": SPARE_NAME, "password": SPARE_PASS,
                   "nonce": m.group(1), "_submit": "Submit"})
spare_token = re.search(r"['\"]csrfNonce['\"]\s*:\s*['\"]([0-9a-f]+)",
                        spare_s.get(BASE + "/admin/users", timeout=20).text)
check(spare_token is not None, "the throwaway admin can open the admin panel")
SH = {"CSRF-Token": spare_token.group(1), "Content-Type": "application/json"}

r = spare_s.patch(f"{BASE}/api/v1/users/{KID}", headers=SH, timeout=20,
                  json={"password": f"stolen-{RUN}"})
check(r.status_code == 403, f"another admin cannot change its password ({r.status_code})")
r = spare_s.patch(f"{BASE}/api/v1/users/{KID}", headers=SH, timeout=20,
                  json={"email": f"stolen-{RUN}@example.invalid"})
check(r.status_code == 403, f"nor its email ({r.status_code})")

print("\n== wiping the instance, from another admin's session ==")
r = spare_s.post(f"{BASE}/admin/reset", timeout=20, allow_redirects=False,
                 data={"nonce": SH["CSRF-Token"], "accounts": "on"})
check(r.status_code == 403, f"/admin/reset is refused ({r.status_code})")
check(still_there(KID), "and every account is still there")
r = spare_s.post(f"{BASE}/admin/import", timeout=20, allow_redirects=False,
                 data={"nonce": SH["CSRF-Token"]})
check(r.status_code == 403, f"/admin/import is refused ({r.status_code})")
r = spare_s.get(f"{BASE}/admin/reset", timeout=20, allow_redirects=False)
check(r.status_code == 200, f"but the page itself still opens ({r.status_code})")

print("\n== the protected account itself is not locked out ==")
r = s.post(BASE + "/admin/reset", timeout=20, allow_redirects=False,
           data={"nonce": TOKEN})   # no boxes ticked: /admin/reset deletes nothing
check(r.status_code != 403, f"it can still open a reset ({r.status_code})")

print("\n== and none of this depends on the workshop switch (§39) ==")
if s.get(BASE + "/admin/workshop/plugin", timeout=20).status_code == 200:
    def flip(to):
        s.post(BASE + "/admin/workshop/plugin", timeout=20,
               data={"nonce": TOKEN, "enabled": to})
    was_on = "badge-success" in s.get(BASE + "/admin/workshop/plugin", timeout=20).text
    atexit.register(lambda: flip("on" if was_on else "off"))
    flip("off")
    r = api("DELETE", f"/users/{KID}")
    check(r.status_code == 403,
          f"with the workshop off, the account is still protected ({r.status_code})")
    check(api("GET", "/workshop/protected-user").status_code == 200,
          "and the endpoint the admin panel reads still answers")
    flip("on" if was_on else "off")
else:
    print("  skip  this image has no workshop switch")

print("\n== how the protected account is identified ==")
# 404 when the key has never been set, which is the common case: CTFd's config
# endpoint reads a row, not a default.
_cfg = s.get(BASE + "/api/v1/configs/workshop_protected_user", headers=H, timeout=20)
was_pinned = ((_cfg.json().get("data") or {}).get("value") or ""
              if _cfg.status_code == 200 else "")


def pin(user_id):
    return s.post(BASE + "/admin/workshop/protected", timeout=20,
                  data={"nonce": TOKEN, "user_id": user_id or ""})


def unpin_at_exit():
    pin(was_pinned)


atexit.register(unpin_at_exit)

pin(SPARE)
now = api("GET", "/workshop/protected-user").json()["data"]
check(now and now["id"] == SPARE, f"pinning moves the protection: {now}")
# Proved on the throwaway, never by deleting the account the pin just left.
r = api("DELETE", f"/users/{SPARE}")
check(r.status_code == 403, f"and the newly pinned account is undeletable ({r.status_code})")

pin("")
now = api("GET", "/workshop/protected-user").json()["data"]
check(now and now["id"] == KID, f"clearing the pin falls back: {now}")
check(now and now["name"].lower() == "admin",
      "to the admin NAMED admin, which is what survives a backup import "
      "with its id changed")

print("\n== any other admin, which the guard must not touch ==")
r = api("PATCH", f"/users/{SPARE}", json={"type": "user"})
check(r.status_code == 200, f"a second admin can be demoted ({r.status_code})")
r = api("PATCH", f"/users/{SPARE}", json={"banned": True})
check(r.status_code == 200, f"and banned ({r.status_code})")
api("PATCH", f"/users/{SPARE}", json={"banned": False})
r = api("DELETE", f"/users/{SPARE}")
check(r.status_code == 200, f"and deleted ({r.status_code})")
check(not still_there(SPARE), "and it is gone")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
