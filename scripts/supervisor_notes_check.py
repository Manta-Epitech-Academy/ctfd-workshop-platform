#!/usr/bin/env python3
"""Do supervisor notes reach the answer sheet, and only the answer sheet?

    python3 scripts/supervisor_notes_check.py <base-url> <admin-pass>

The feature's test suite (PLAN.md §69), shaped like scripts/supervisor_check.py:
positional arguments, `check(cond, label)`, `ALL GREEN` or `FAILURES: [...]`,
non-zero exit on any failure.

Two halves. The linter is proved on a fixture, with no instance. The import is
proved by syncing that fixture, so it needs a **fresh instance**: the fixture
becomes the instance's content, like scripts/phase2_validate.py does.
"""
import contextlib
import io
import re
import shutil
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import ws_parser  # noqa: E402
from sync_subject import sync  # noqa: E402

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
fails = []

NOTES = """# Notes encadrant

Ce texte est un préambule, il ne va nulle part.

## Un
<!-- ws:note a -->
Regarder la ligne 3 en premier.

```lua
-- <!-- ws:note faux -->
print("ok")
```

### Si ça bloque encore

Relire la boîte à outils.<script>alert(1)</script>

## Quiz
<!-- ws:note q1 -->
La bonne réponse se trouve dans le schéma.
"""


def check(cond, label):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        fails.append(label)


def fixture():
    root = Path(tempfile.mkdtemp(prefix="notes_check-")) / "fx"
    root.mkdir(parents=True)
    (root / "subject.yaml").write_text(
        'schema_version: "2.0"\n'
        'project: {name: Notes, slug: notescheck, summary: s, entrypoint: intro.md}\n'
        'platform: {validation_default: checkpoint, points_default: 10}\n'
        'documents: [intro.md, main.md]\n')
    (root / "intro.md").write_text("# Notes\n\nIntro.\n")
    (root / "main.md").write_text(
        "# Exercices\n\n## Un\n<!-- ws: {type: exercise, id: a} -->\n\nFaire.\n\n"
        "## Deux\n<!-- ws: {type: exercise, id: b} -->\n\nFaire.\n\n"
        "## Trois\n<!-- ws: {type: exercise, id: c, validation: quiz} -->\n\nFaire.\n\n"
        "<!-- ws: {type: quiz, id: inside, kind: single} -->\n> Question ?\n\n"
        "- A. oui\n- B. non\n\n"
        "## Quiz\n<!-- ws: {type: quiz, id: q1, kind: single} -->\n> Question ?\n\n"
        "- A. oui\n- B. non\n\n"
        "## Fin\n\nMerci.\n")
    (root / "quiz_answers.yaml").write_text("answers:\n  inside: [A]\n  q1: [B]\n")
    return root


# ------------------------------------------------------------------ lint --
print("== the linter ==")
root = fixture()
check(ws_parser.lint(root) == [], "a subject with no notes file is fine")
notes = root / "supervisor_notes.md"
notes.write_text(NOTES)
check(ws_parser.lint(root) == [], "a valid notes file passes")
found = ws_parser.load_supervisor_notes(root)
check(set(found) == {"a", "q1"}, "one note per marker, the fenced example ignored")
check("Si ça bloque encore" in found.get("a", "") and "## Quiz" not in found.get("a", ""),
      "a note keeps its own headings and loses the next note's label")
check("préambule" not in "".join(found.values()), "the preamble belongs to no step")

for label, extra, expect in [
    ("a note for a step that does not exist", "<!-- ws:note ghost -->\nx\n",
     "matches no step id"),
    ("a note for a question inside a step", "<!-- ws:note inside -->\nx\n",
     "file the note under 'c'"),
    ("a second note for one step", "<!-- ws:note a -->\nx\n", "a second note"),
    ("an empty note", "<!-- ws:note b -->\n", "is empty"),
    ("an image", "<!-- ws:note b -->\n![x](img/x.png)\n", "has an image"),
    ("a remote image", "<!-- ws:note b -->\n![x](https://example.org/x.png)\n",
     "has an image"),
    ("a scene", "<!-- ws:note b -->\n<!-- ws:scene {src: s.glb} -->\n",
     "scene or an animation"),
    ("a linked file", "<!-- ws:note b -->\n[le fichier](files/x.zip)\n",
     "links a file"),
]:
    notes.write_text(NOTES + "\n" + extra)
    check(any(expect in p for p in ws_parser.lint(root)), f"refused: {label}")
notes.write_text("# Rien\n\nPas de marqueur.\n")
problems, advice = ws_parser.lint_all(root)
check(not problems and any("holds no note" in a for a in advice),
      "a file with no marker is advice, not a refusal")
notes.write_text(NOTES)
notes.rename(root / "supervisor_notes.md.gpg")
problems, advice = ws_parser.lint_all(root)
check(not problems and any("not checked" in a for a in advice),
      "notes only encrypted: not refused, and said so")
(root / "supervisor_notes.md.gpg").rename(notes)


# --------------------------------------------------------------- instance --
s = requests.Session()


def nonce(path="/"):
    r = s.get(BASE + path, timeout=20)
    m = (re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text)
         or re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", r.text))
    return m.group(1) if m else ""


def run_sync():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = sync(str(root), BASE, "admin", ADMIN_PASS)
    return out.getvalue(), result


def sheet():
    return s.get(BASE + "/admin/workshop/answers", timeout=20).text


print("== the import ==")
anon = requests.post(BASE + "/api/v1/workshop/notes", json={}, timeout=20,
                     allow_redirects=False)
check(anon.status_code in (302, 401, 403), "an anonymous visitor cannot write notes")
check("/login" in requests.get(BASE + "/admin/workshop/answers", timeout=20).url,
      "an anonymous visitor cannot read the sheet")

log, result = run_sync()
ids = {**result["ex_ids"], **{q: cid for q, (cid, _) in result["quiz_ids"].items()}}
check("notes: 2 supervisor note(s)" in log, "the sync says it wrote two notes")

s.post(BASE + "/login", data={"name": "admin", "password": ADMIN_PASS,
                              "nonce": nonce("/login?staff=1"), "_submit": "Submit"},
       timeout=20)
page = sheet()
check(page.count('class="btn btn-sm btn-outline-secondary ws-an-note-toggle"') == 2,
      "the sheet has a Show note button on the two steps with a note")
m = re.search(r'<div class="ws-an-note" id="ws-an-note-%d" hidden>(.*?)</div>\s*</div>'
              % ids["a"], page, re.S)
check(bool(m), "the note is on the page, hidden until asked for")
body = m.group(1) if m else ""
check("Regarder la ligne 3" in body and "<pre>" in body,
      "it is rendered from markdown, code block included")
check("<script>alert(1)</script>" not in page, "a script in a note is not served")
check(f'id="ws-an-note-{ids["b"]}"' not in page, "a step with no note has no button")

h = {"CSRF-Token": nonce(), "Content-Type": "application/json"}
for cid in (ids["a"], ids["q1"]):
    ch = s.get(f"{BASE}/api/v1/challenges/{cid}", headers=h, timeout=20).json()
    data = ch.get("data") or {}
    check(not any(t in str(data) for t in ("Regarder la ligne", "dans le schéma")),
          f"the challenge {cid} carries nothing of its note")
r = s.post(BASE + "/api/v1/workshop/notes", headers=h, timeout=20,
           json={"challenge_ids": [ids["a"]], "notes": {str(ids["b"]): "x"}})
check(r.status_code == 400, "a note outside the steps being replaced is refused")
r = s.post(BASE + "/api/v1/workshop/notes", headers=h, timeout=20,
           json={"challenge_ids": [ids["a"]], "notes": {str(ids["a"]): "  "}})
check(r.status_code == 400, "an empty note is refused")

notes.write_text(NOTES.split("## Quiz")[0])
log, _ = run_sync()
page = sheet()
check(f'id="ws-an-note-{ids["q1"]}"' not in page and f'id="ws-an-note-{ids["a"]}"' in page,
      "a note taken out of the file goes on the next sync, the others stay")

notes.rename(root / "supervisor_notes.md.gpg")
log, _ = run_sync()
check("left as they were" in log and f'id="ws-an-note-{ids["a"]}"' in sheet(),
      "a sync that cannot open the notes leaves them as they were")
(root / "supervisor_notes.md.gpg").unlink()
log, _ = run_sync()
check('class="ws-an-note"' not in sheet(), "no file at all: the subject has no notes")

shutil.rmtree(root.parent, ignore_errors=True)
print("ALL GREEN" if not fails else f"FAILURES: {fails}")
sys.exit(1 if fails else 0)
