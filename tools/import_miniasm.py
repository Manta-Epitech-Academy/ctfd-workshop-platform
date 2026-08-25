#!/usr/bin/env python3
"""One-way importer: MiniASM's exercise data -> a convention 2.0 subject.

    python3 tools/import_miniasm.py <miniasm_runtime checkout> content/miniasm

MiniASM (WDR+E) keeps its workshop in the app: `js/exercises.js` holds the
structure — id, category, type, `requires[]`, available opcodes, `unlocks`, and
the input/expected pairs it grades against — and `js/lang-fr.js` holds the text
under `exercises[id]`. This turns both into markdown the platform reads.

    lang-fr title/goal/description -> the exercise body
    lang-fr hints[]                -> <details> blocks behind a hint marker
    exercises.js requires[]        -> `requires:`, because this is a real DAG
                                      rather than a chain
    exercises.js category          -> one document per category
    type tutorial | challenge      -> points (a tutorial is worth less)
    exercise id                    -> `token_id`, which is what the completion
                                      token is derived from (PLAN.md §21)

The data is read by **running node against the app's own files**, not by
parsing JavaScript with regexes: they are the source of truth, and a port that
edits an exercise must not silently disagree with the subject built from it.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

# Reading order for the categories; anything unlisted follows, alphabetically.
CATEGORY_ORDER = ["arithmetic", "comparisons", "swaps"]
CATEGORY_TITLES = {"arithmetic": "Arithmétique",
                   "comparisons": "Comparaisons",
                   "swaps": "Échanges"}
# A tutorial is a guided step; a challenge is work. The app makes the
# distinction, so the points follow it.
POINTS = {"tutorial": 10, "challenge": 30}

# Loads the app's own modules under a minimal DOM-less shim and prints the
# joined data as JSON. `lang.js` and `exercises.js` expect a `window`.
EXTRACT_JS = r"""
// Load the app's own modules under a DOM-less shim, select French *before*
// exercises.js resolves its text, and print the merged records. The app builds
// each exercise by joining exercises.js (structure) with the current language
// (text), so this asks it rather than re-implementing the join.
const fs = require('fs');
const path = require('path');
const root = process.argv[2];
const window = {};
global.window = window;
global.document = { addEventListener() {}, getElementById: () => null };
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const load = (file) => new Function('window', 'document', 'localStorage',
  fs.readFileSync(path.join(root, file), 'utf8'))(
    window, global.document, global.localStorage);

['js/config.js', 'js/lang.js', 'js/lang-fr.js'].forEach(load);
if (!window.MiniASMLang.setCurrent('fr')) {
  console.error('MiniASM has no French strings');
  process.exit(2);
}
load('js/exercises.js');
console.log(JSON.stringify({ data: window.MiniASMExercises.EXERCISES }, null, 1));
"""


def extract(source):
    """{text, data} straight from the app, via node."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "extract.js"
        script.write_text(EXTRACT_JS)
        out = subprocess.run(["node", str(script), str(source)],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            sys.exit(f"could not read MiniASM's data:\n{out.stderr.strip()[:800]}")
        return json.loads(out.stdout)


def body_for(entry, text):
    """The exercise statement: its goal, then the description as written."""
    lines = []
    goal = (text.get("goal") or "").strip()
    if goal:
        lines += [f"**Objectif :** {goal}", ""]
    description = (text.get("description") or "").strip()
    if description:
        lines += [description, ""]
    allowed = entry.get("available") or []
    if allowed:
        lines += ["Instructions autorisées : "
                  + ", ".join(f"`{op}`" for op in allowed), ""]
    if entry.get("unlocks"):
        lines += [f"Réussir cet exercice débloque `{entry['unlocks']}`.", ""]
    return "\n".join(lines).rstrip()


def convert(source, dest):
    source, dest = Path(source), Path(dest)
    if not (source / "js" / "exercises.js").is_file():
        sys.exit(f"{source} does not look like a miniasm checkout")
    dest.mkdir(parents=True, exist_ok=True)

    # The app merges structure and text into one record per exercise, so there
    # is nothing to join here.
    data = extract(source)["data"]
    by_category = {}
    for entry in data:
        by_category.setdefault(entry["category"], []).append(entry)

    categories = ([c for c in CATEGORY_ORDER if c in by_category]
                  + sorted(c for c in by_category if c not in CATEGORY_ORDER))
    documents, total = [], 0
    for category in categories:
        title = CATEGORY_TITLES.get(category, category.title())
        lines = [f"# {title}", ""]
        for entry in sorted(by_category[category], key=lambda e: e["id"]):
            t = entry
            marker = {"type": "exercise", "id": f"ex{entry['id']}",
                      "points": POINTS.get(entry["type"], 25),
                      "token_id": entry["id"]}
            # The app's own prerequisites, kept: it gates exercises too, and the
            # two must agree or a step opens in one and not the other.
            if entry.get("requires"):
                marker["requires"] = [f"ex{r}" for r in entry["requires"]]
            heading = (t.get("title") or t.get("name")
                       or f"Exercice {entry['id']}").strip()
            lines += [f"## {heading}",
                      "<!-- ws: " + yaml.safe_dump(
                          marker, default_flow_style=True, allow_unicode=True,
                          sort_keys=False).strip() + " -->", "",
                      body_for(entry, t), ""]
            for hint in t.get("hints") or []:
                lines += ["<!-- ws: {type: hint} -->",
                          "<details><summary>Indice</summary>", "",
                          str(hint).strip(), "", "</details>", ""]
            total += 1
        lines += [f"## Fin — {title}", "",
                  f"Tu as terminé la partie {title.lower()}.", ""]
        name = f"{category}.md"
        (dest / name).write_text("\n".join(lines))
        documents.append(name)

    print(f"imported {total} exercises into {len(documents)} documents: "
          + ", ".join(documents))
    return documents, total


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a miniasm_runtime checkout")
    ap.add_argument("dest", help="subject directory to write")
    args = ap.parse_args()
    convert(args.source, args.dest)


if __name__ == "__main__":
    main()
