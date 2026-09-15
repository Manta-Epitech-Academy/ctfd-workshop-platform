#!/usr/bin/env python3
"""Sync a *workshop* — several subjects — into one CTFd instance (PLAN.md §19).

    python3 tools/sync_workshop.py content/workshops/discover-linux \\
        --url http://localhost:8080 --admin-user admin --admin-pass ...

A workshop is a starter subject plus zero or more advanced ones, and the rule
from CLAUDE.md is that **the starter is always finished first**. That is the
whole reason this exists: everything else a workshop needs, a subject sync
already does.

    subjects:
      - path: ../shell_rpg     # vendored subject directory
        role: starter
      - path: ../shell_1
        role: advanced
        order: 1               # optional; unordered advanced = free choice

Ordering, in board order and in prerequisites:

  starter -> advanced order:1 -> advanced order:2 -> …   (chained)
  starter -> every unordered advanced                    (free choice)

An advanced subject waits on the previous one's **closing step** — the step
where the participant says they are done with it (PLAN.md §19, D2). One edge,
and it means something to the person clicking it.

Instance-wide settings (the part list, the optional/free id sets, which closing
step ends the workshop) are accumulated here and written once, because each
subject sync would otherwise overwrite the previous subject's (§19.1).
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from sync_subject import (CTFdAdmin, entry_document, sync, upsert_page,  # noqa: E402
                          write_instance_config)
from ws_parser import lint_all  # noqa: E402


def load_manifest(workshop_dir):
    path = Path(workshop_dir) / "workshop.yaml"
    if not path.is_file():
        sys.exit(f"no workshop.yaml in {workshop_dir}")
    manifest = yaml.safe_load(path.read_text()) or {}
    subjects = manifest.get("subjects") or []
    if not subjects:
        sys.exit(f"{path}: no subjects")

    resolved = []
    for entry in subjects:
        if not entry.get("path"):
            sys.exit(f"{path}: every subject needs a `path` to a vendored subject directory")
        directory = (Path(workshop_dir) / entry["path"]).resolve()
        if not (directory / "subject.yaml").is_file():
            sys.exit(f"{path}: {directory} is not a subject directory")
        resolved.append({**entry, "dir": directory})

    starters = [s for s in resolved if s.get("role") == "starter"]
    if len(starters) != 1:
        sys.exit(f"{path}: expected exactly one subject with `role: starter`, "
                 f"found {len(starters)}")
    return manifest, resolved


def order_subjects(subjects):
    """Board order: the starter, then ordered advanced, then unordered ones."""
    starter = next(s for s in subjects if s.get("role") == "starter")
    advanced = [s for s in subjects if s is not starter]
    ordered = sorted([s for s in advanced if s.get("order") is not None],
                     key=lambda s: s["order"])
    free = [s for s in advanced if s.get("order") is None]
    return [starter] + ordered + free


def gate_for(entry, starter_final, previous_final):
    """What this subject waits on.

    An ordered advanced subject chains off the one before it; an unordered one
    hangs off the starter, so several of them are genuinely a free choice.
    """
    if entry.get("role") == "starter":
        return None
    return previous_final if entry.get("order") is not None else starter_final


def closing_of(result, name):
    """The step the *next* subject waits for.

    Normally the closing step of this subject's last document — the one where
    the participant says they are done (PLAN.md §19, D2). A subject whose last
    document ends on an exercise has none, and the caller used to fall back to
    the previous subject's, which for the first subject is `None`: the next
    subject then imported **with no prerequisite at all** and the whole workshop
    was open from the first minute. It failed silently and it failed open, which
    is the worst pair, so the fallback is now the subject's last required step
    and it says so out loud.
    """
    if result["final_step"]:
        return result["final_step"]
    exercises = [e for e in result["subject"].exercises if not e.optional]
    if not exercises:
        return None
    last = result["ex_ids"][exercises[-1].slug]
    print(f"  note: {name} has no closing step, so the next subject waits on "
          f"{exercises[-1].title!r} instead. Ending its last document with a few "
          f"lines of prose would give it one.")
    return last


def sync_workshop(workshop_dir, url, admin_user, admin_pass, codes_dir=None, *,
                  ctfd=None):
    manifest, subjects = load_manifest(workshop_dir)
    subjects = order_subjects(subjects)

    # Lint everything before touching the instance: a workshop half-imported
    # because the third subject does not parse is worse than one not imported.
    problems, advice = [], []
    for entry in subjects:
        errs, warns = lint_all(entry["dir"])
        problems += [f"{entry['dir'].name}: {p}" for p in errs]
        advice += [f"{entry['dir'].name}: {w}" for w in warns]
    if problems:
        for p in problems:
            print(f"FAIL {p}", file=sys.stderr)
        sys.exit(1)
    for a in advice:
        print(f"warn {a}")

    # `ctfd` is passed in by the admin sync page, which authenticates with the
    # instance's own preset admin token rather than a password (PLAN.md §26.5).
    ctfd = ctfd or CTFdAdmin(url, admin_user, admin_pass)
    workshop = manifest.get("workshop") or {}
    print(f"== workshop: {workshop.get('name') or workshop_dir} "
          f"({len(subjects)} subjects) ==")

    documents, optional_ids, free_ids = [], set(), set()
    # Accumulated exactly like runtime_params below, and for the same reason:
    # workshop_subjects is instance-wide, so a second subject writing it would
    # erase the first. sync() is called with standalone=False precisely so it
    # does not write any of these itself.
    subjects_cfg = {}
    totals = {"created": 0, "updated": 0}
    runtime, runtime_params = {}, {}
    position, starter_final, previous_final, final_step = 0, None, None, None
    entry_page = None

    for entry in subjects:
        role = entry.get("role", "advanced")
        print(f"\n-- {entry['dir'].name} ({role}) --")
        codes = (str(Path(codes_dir) / f"instructor_codes.{entry['dir'].name}.yaml")
                 if codes_dir else None)
        result = sync(str(entry["dir"]), url, admin_user, admin_pass, codes,
                      ctfd=ctfd, position_base=position, standalone=False,
                      gate_on=gate_for(entry, starter_final, previous_final))
        for key, count in (result.get("stats") or {}).items():
            totals[key] = totals.get(key, 0) + count

        subject = result["subject"]
        documents += result["documents"]
        optional_ids |= result["optional_ids"]
        free_ids |= result["free_ids"]
        position = result["last_position"]
        closing = closing_of(result, entry["dir"].name)
        previous_final = closing or previous_final
        final_step = result["final_step"] or final_step
        if role == "starter":
            starter_final = closing
            entry_page = subject
        # One dist serves every subject that shares a runtime; what differs is
        # data, and it travels per subject (PLAN.md §19.2).
        rt = result["runtime"]
        if rt and not runtime:
            runtime = {k: rt[k] for k in ("id", "version", "title", "src",
                                          "external", "params")
                       if k in rt}
            runtime["pane"] = rt.get("pane") or {}
        if rt.get("params"):
            runtime_params[subject.slug] = rt["params"]
        subjects_cfg[subject.slug] = result["cover"]

    # The public front door is the workshop's, not the last subject's.
    entry_doc = entry_document(entry_page)
    # Upsert for the same reason sync_subject does: an instance whose Pages were
    # wiped by /admin/reset has no `index` page to PATCH.
    upsert_page(ctfd, "index", {
        "title": workshop.get("name") or entry_page.name, "route": "index",
        "content": manifest.get("workshop", {}).get("summary") and
        f"# {workshop['name']}\n\n{workshop['summary']}\n\n{entry_doc.body_md}"
        or entry_doc.body_md,
        "format": "markdown", "draft": False, "hidden": False, "auth_required": False})
    print(f"\npage: {workshop.get('name') or entry_page.name!r} -> index")

    if runtime:
        if runtime_params:
            runtime["subjects"] = runtime_params
        ctfd.api("PATCH", "/configs/workshop_runtime",
                 json={"value": json.dumps(runtime)})
        print(f"runtime: {runtime['id']}@{runtime['version']} "
              f"({len(runtime_params)} subject-specific parameter sets)")

    write_instance_config(ctfd, documents, optional_ids, free_ids, final_step,
                          subjects_cfg=subjects_cfg)
    print(f"workshop done: {len(subjects)} subjects, {len(documents)} parts, "
          f"{len(optional_ids)} optional and {len(free_ids)} free steps, "
          f"closing on challenge {final_step}")
    return {"documents": documents, "subjects": len(subjects), "stats": totals}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workshop_dir", help="directory holding workshop.yaml")
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", required=True)
    ap.add_argument("--codes-dir", default=".",
                    help="where instructor_codes.<subject>.yaml live")
    args = ap.parse_args()
    sync_workshop(args.workshop_dir, args.url, args.admin_user, args.admin_pass,
                  args.codes_dir)


if __name__ == "__main__":
    main()
