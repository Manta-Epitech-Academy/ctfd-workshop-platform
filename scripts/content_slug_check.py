#!/usr/bin/env python3
"""Does every step say which content it belongs to?

    python3 scripts/content_slug_check.py <base-url> <admin-pass> [content ...]

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §66). It changes nothing: it reads the instance as an admin, after a
sync, and checks what `record_content` (tools/sync_subject.py) left behind.

  - every visible step carries one `ws-content:` topic, and it names the
    content the instance serves (`workshop_content`);
  - a hidden step carries one at most, and never two;
  - no step that is not a workshop step carries one.

Name contents after the password and each must be carried by at least one
hidden step: that is the rotation. Sync content A, then B, then run this with
`A`; sync A again and run it with `B`.

Shaped like scripts/supervisor_check.py: positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.
"""
import re
import sys
from collections import Counter

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
ROTATED = sys.argv[3:]
PREFIX = "ws-content:"
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


def api(path):
    r = admin.get(BASE + "/api/v1" + path, timeout=20)
    if r.status_code != 200:
        sys.exit(f"GET {path} -> {r.status_code}: not an admin session?")
    return r.json().get("data")


current = (api("/configs/workshop_content") or {}).get("value")
visible, hidden, strangers = [], [], []
for ch in api("/challenges?view=admin") or []:
    topics = [t["value"] for t in api(f"/challenges/{ch['id']}/topics") or []]
    carried = [t[len(PREFIX):] for t in topics if t.startswith(PREFIX)]
    if not any(t.startswith("ws:") for t in topics):
        strangers.append((ch["name"], carried))
        continue
    state = api(f"/challenges/{ch['id']}")["state"]
    (hidden if state == "hidden" else visible).append((ch["name"], carried))

print(f"\n== the instance serves {current!r}: "
      f"{len(visible)} visible step(s), {len(hidden)} hidden ==")
check(bool(current), "the instance says which content it serves")
check(bool(visible), "there are steps to look at")
wrong = [(n, c) for n, c in visible if c != [current]]
check(not wrong, f"every visible step carries {current!r} and nothing else: {wrong[:3]}")
twice = [(n, c) for n, c in hidden if len(c) > 1]
check(not twice, f"no hidden step carries two contents: {twice[:3]}")
check(not [s for s in strangers if s[1]],
      "a challenge that is not a workshop step carries none")

held = Counter(c[0] for _, c in hidden if c)
bare = [n for n, c in hidden if not c]
print(f"\n== hidden steps: {dict(held)}, {len(bare)} with no content ==")
for content in ROTATED:
    check(held.get(content, 0) > 0,
          f"hidden steps still say they belong to {content!r}: {held.get(content, 0)}")
    check(content != current, f"{content!r} is not what the instance serves now")

print()
if fails:
    print("FAILURES:", fails)
    sys.exit(1)
print("ALL GREEN")
