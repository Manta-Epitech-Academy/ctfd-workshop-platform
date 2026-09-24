#!/usr/bin/env python3
"""Does the workshop switch turn everything off, and nothing else?

    python3 scripts/toggle_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §39). It walks the two states: with the plugin on, every route
answers and the navbar leads to the subject; with it off, every route this
plugin registers is a 404, `/` is CTFd's own front door again, and the admin
bar carries exactly one entry — the page that switches it back.

The third list is the one that matters, and it is what makes this a switch
rather than an uninstall: the Epitech theme stays on in both states.

Shaped like scripts/supervisor_check.py — positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back**, whichever state it found it in, including on an
exception or a Ctrl-C.

Routes are matched by href and never by label: the participant shell renders
in the reader's language, so "Workshop" reads « Atelier » to a French browser.
"""
import atexit
import re
import sys

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
PLUGIN = "/admin/workshop/plugin"
fails = []

# Every surface the switch has to reach, one per blueprint it guards.
WORKSHOP_ROUTES = [
    "/workshop", "/toolbox", "/api/v1/workshop/graph",
    "/admin/workshop/settings", "/admin/workshop/answers", "/admin/workshop/stats",
    "/admin/workshop/submissions", "/admin/workshop/feedback",
    "/admin/workshop/sync", "/admin/workshop/jump",
]


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
if s.get(BASE + PLUGIN, timeout=20).status_code != 200:
    sys.exit(f"{PLUGIN} does not answer — not an admin session, or an old image")


def enabled():
    return "badge-success" in s.get(BASE + PLUGIN, timeout=20).text


def switch(to):
    s.post(BASE + PLUGIN, timeout=20,
           data={"nonce": nonce(PLUGIN), "enabled": to})


WAS = enabled()
atexit.register(lambda: switch("on" if WAS else "off"))


def codes(paths):
    return {p: s.get(BASE + p, timeout=20, allow_redirects=False).status_code
            for p in paths}


def nav(path="/challenges"):
    html = s.get(BASE + path, timeout=20).text
    inner = html.split('id="epi-nav"', 1)[-1].split("</nav>", 1)[0]
    return re.findall(r'class="epi-nav-link[^"]*"\s+href="([^"]+)"', inner)


def admin_menu():
    html = s.get(BASE + "/admin/statistics", timeout=20).text
    return sorted(set(re.findall(r'href="(/admin/workshop/[^"]+)"', html)))


print("== switched on ==")
switch("on")
check(enabled(), "the page reports the plugin as enabled")
c = codes(WORKSHOP_ROUTES)
check(all(v in (200, 302) for v in c.values()), f"every workshop route answers: {c}")
check("/workshop" in nav() and "/toolbox" in nav(), f"the navbar leads to it: {nav()}")
check(len(admin_menu()) > 3, f"the admin bar carries the workshop tools: {admin_menu()}")
root = s.get(BASE + "/", timeout=20, allow_redirects=False)
check(root.status_code in (301, 302) and "/workshop" in root.headers.get("Location", ""),
      f"/ lands on the workshop ({root.status_code})")

print("== switched off ==")
switch("off")
check(not enabled(), "the page reports the plugin as disabled")
c = codes(WORKSHOP_ROUTES)
check(all(v == 404 for v in c.values()), f"every workshop route 404s: {c}")
check(s.get(BASE + PLUGIN, timeout=20).status_code == 200,
      "except this one, which is the way back")
check(s.get(BASE + "/", timeout=20, allow_redirects=False).status_code == 200,
      "/ serves the index Page again")
check(s.get(BASE + "/challenges", timeout=20).status_code == 200, "/challenges answers")
off_nav = nav()
check("/workshop" not in off_nav and "/toolbox" not in off_nav,
      f"the navbar drops the workshop entries: {off_nav}")
check("/challenges" in off_nav, "and offers the board instead")
check("/users" in off_nav, "and the participant list core's navbar has")
check(admin_menu() == [PLUGIN], f"the admin bar keeps only the switch: {admin_menu()}")

print("== the Epitech theme is untouched by the switch ==")
board = s.get(BASE + "/challenges", timeout=20).text
check("epitech-theme.css" in board and "workshop.css" in board,
      "both stylesheets are still linked")
check("epi-header" in board, "and the Epitech shell still renders")
login = requests.get(BASE + "/login", timeout=20).text
check("epi-login" in login, "the login page keeps its Epitech panel")
check("Jump" not in login, "but loses the Jump button")

print("== switched back on ==")
switch("on")
c = codes(WORKSHOP_ROUTES)
check(all(v in (200, 302) for v in c.values()), f"every route answers again: {c}")
check("/workshop" in nav(), f"and the navbar has the workshop back: {nav()}")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
