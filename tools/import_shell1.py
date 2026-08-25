#!/usr/bin/env python3
"""One-way importer: shell-1's challenge directories -> a convention 2.0 subject.

    python3 tools/import_shell1.py ../shell-1/challenges content/shell_1

`shell-1-challenges` predates the convention: one directory per challenge, a
CTFd-shaped `challenge.yml`, and the answer in `private/flag.txt` (GPG-encrypted
in the repo — run its `decrypt.sh` first). This turns that into the shape the
platform reads, and it is re-runnable, because the upstream keeps moving:

    challenge.yml description   -> the exercise body, verbatim
    name                        -> the heading, minus the "Shell 10x — " prefix
                                   the document title already carries
    value                       -> `points:` on the marker, since they are
                                   hand-tuned (1 to 445, summing to 9,999)
    hints                       -> <details> blocks behind a hint marker
    files + inline references   -> img/, referenced relatively (§3.8 forbids
                                   hotlinking, and the sync uploads them)
    private/flag.txt            -> flags.yaml, keyed by directory name
    requirements.prerequisites  -> nothing: the graph is a pure chain, which is
                                   exactly what source order already produces

`private/writeup.md` is deliberately not imported: instructor material stays in
the source repo (PLAN.md §20).
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

CHAPTERS = [("shell_101", "Shell 101", "shell101.md"),
            ("shell_102", "Shell 102", "shell102.md")]
# "Shell 101 — 004 — ls" -> "004 — ls": the document is already called Shell 101.
NAME_PREFIX = re.compile(r"^Shell\s*10[12]\s*[—-]\s*")


def tracked_dirs(root):
    """Challenge directories **git knows about**, not whatever is on disk.

    This is not pedantry: the working copy carries five directories from an old
    naming scheme that were deleted upstream and never cleaned, each without a
    flag. Reading the disk imports them as five broken exercises and invents a
    content problem (PLAN.md §20.6). Falls back to the disk only when the source
    is not a git checkout, and says so.
    """
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return {root / Path(line).parent for line in out.stdout.splitlines()
                    if line.endswith("challenge.yml")}
    except (OSError, subprocess.SubprocessError):
        pass
    print("  ! not a git checkout: importing whatever is on disk", file=sys.stderr)
    return None


def challenges(root, chapter, tracked):
    """Challenge directories, in name order.

    Order is the reading order and therefore the prerequisite chain, so it has
    to be the directory order the author chose.
    """
    for d in sorted((root / chapter).iterdir()):
        if not (d / "challenge.yml").is_file():
            continue
        if tracked is not None and d not in tracked:
            continue
        yield d


def convert(source, dest):
    source, dest = Path(source), Path(dest)
    if not (source / "shell_101").is_dir():
        sys.exit(f"{source} does not look like shell-1-challenges")
    (dest / "img").mkdir(parents=True, exist_ok=True)

    tracked = tracked_dirs(source)
    flags, missing, total, counts = {}, [], 0, {}
    for chapter, title, filename in CHAPTERS:
        lines = [f"# {title}", ""]
        count = 0
        for d in challenges(source, chapter, tracked):
            meta = yaml.safe_load((d / "challenge.yml").read_text())
            body = (meta.get("description") or "").rstrip()

            # Images: copy beside the subject and rewrite the reference. The
            # filenames are unique across the set today; prefix with the
            # challenge anyway, so a later collision cannot silently overwrite.
            for name in meta.get("files") or []:
                src = d / name
                if not src.is_file():
                    missing.append(f"{d.name}: image {name} not found")
                    continue
                out = f"{d.name}-{name}"
                shutil.copyfile(src, dest / "img" / out)
                body = body.replace(f"({name})", f"(img/{out})")

            marker = {"type": "exercise", "id": d.name}
            if meta.get("value") is not None:
                marker["points"] = meta["value"]
            lines += [f"## {NAME_PREFIX.sub('', meta['name']).strip()}",
                      "<!-- ws: " + yaml.safe_dump(
                          marker, default_flow_style=True, allow_unicode=True,
                          sort_keys=False).strip() + " -->", "", body, ""]

            for hint in meta.get("hints") or []:
                text = hint if isinstance(hint, str) else hint.get("content", "")
                lines += ["<!-- ws: {type: hint} -->",
                          "<details><summary>Indice</summary>", "",
                          str(text).strip(), "", "</details>", ""]

            flag = (d / "private" / "flag.txt")
            if flag.is_file():
                flags[d.name] = flag.read_text().strip()
            else:
                missing.append(f"{d.name}: no private/flag.txt (run decrypt.sh?)")
            count += 1

        lines += [f"## Fin de {title}", "",
                  f"Tu as terminé {title}.", ""]
        (dest / filename).write_text("\n".join(lines))
        counts[title] = count
        total += count

    (dest / "flags.yaml").write_text(
        "# Authored answers for `validation: flag` (CONTENT_CONVENTION §3.3b).\n"
        "# Imported from shell-1-challenges private/flag.txt — NOT public.\n"
        + yaml.safe_dump({"flags": flags}, allow_unicode=True, sort_keys=True))

    for problem in missing:
        print(f"  ! {problem}", file=sys.stderr)
    print(f"imported {total} challenges "
          + ", ".join(f"{v} in {k}" for k, v in counts.items())
          + f"; {len(flags)} answers")
    return total, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="path to shell-1-challenges")
    ap.add_argument("dest", help="subject directory to write")
    args = ap.parse_args()
    _, missing = convert(args.source, args.dest)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
