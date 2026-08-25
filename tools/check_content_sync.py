#!/usr/bin/env python3
"""Check that each vendored subject still matches the repo it came from.

    python3 tools/check_content_sync.py [subject ...]

`tools/sync_subject.py` imports from `content/<subject>`; the subject's own
GitHub repo is what its CI lints and what the author edits. Nothing keeps the
two in step, so an edit in either place is invisible until a session runs on
stale content. This makes that loud, the same way `check_parser_sync.py` does
for the parser.

The repos are private, so fetching goes through `gh api` — the maintainer's own
authenticated CLI — rather than raw.githubusercontent. No `gh`, no network, no
mapping: the check reports **skip**, which is not a pass.

`subject.yaml` is compared as *parsed YAML*, not as bytes: the vendored copy
carries a header comment saying it is the vendored copy, and that difference is
deliberate. Everything else is compared byte for byte.
"""
import argparse
import base64
import difflib
import json
import subprocess
import sys
from pathlib import Path

import yaml

CONTENT = Path(__file__).resolve().parent.parent / "content"
UPSTREAMS = CONTENT / "upstreams.yaml"
# Anything else in a subject repo (README.md is generated, .github/ is CI) is
# the repo's own business, not content the platform imports.
TRACKED_SUFFIXES = (".md", ".yaml", ".yml")
IGNORED = {"README.md", "upstreams.yaml"}


def _gh(args):
    """`gh api …` -> parsed JSON, or None when gh cannot answer."""
    try:
        out = subprocess.run(["gh", "api", *args], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"gh unavailable ({e})"
    if out.returncode != 0:
        return None, out.stderr.strip().splitlines()[-1] if out.stderr else "gh failed"
    try:
        return json.loads(out.stdout), None
    except ValueError:
        return None, "gh returned no JSON"


def _remote_files(repo, ref):
    """{path: text} for the content files of a subject repo."""
    tree, err = _gh([f"repos/{repo}/git/trees/{ref}?recursive=1"])
    if tree is None:
        return None, err
    paths = [n["path"] for n in tree.get("tree", [])
             if n["type"] == "blob"
             and n["path"].endswith(TRACKED_SUFFIXES)
             and "/" not in n["path"]
             and n["path"] not in IGNORED]
    files = {}
    for path in paths:
        blob, err = _gh([f"repos/{repo}/contents/{path}?ref={ref}"])
        if blob is None:
            return None, f"{path}: {err}"
        files[path] = base64.b64decode(blob.get("content", "")).decode()
    return files, None


def _local_files(subject_dir):
    return {p.name: p.read_text() for p in sorted(subject_dir.iterdir())
            if p.is_file() and p.name.endswith(TRACKED_SUFFIXES)
            and p.name not in IGNORED}


def _same_manifest(local, remote):
    """subject.yaml differs by design in its header comment, not in its data."""
    try:
        return yaml.safe_load(local) == yaml.safe_load(remote)
    except yaml.YAMLError:
        return False


def compare(subject, repo, ref="main"):
    """(status, detail) where status is 'ok', 'skip' or 'fail'."""
    subject_dir = CONTENT / subject
    if not subject_dir.is_dir():
        return "fail", f"content/{subject} does not exist"

    remote, err = _remote_files(repo, ref)
    if remote is None:
        return "skip", f"{subject}: cannot read {repo} ({err})"

    local = _local_files(subject_dir)
    problems = []
    for name in sorted(set(local) | set(remote)):
        if name not in remote:
            problems.append(f"  content/{subject}/{name} is not in {repo}")
        elif name not in local:
            problems.append(f"  {repo}:{name} is not vendored in content/{subject}")
        elif name == "subject.yaml":
            if not _same_manifest(local[name], remote[name]):
                problems.append(f"  {name} declares different values on the two sides")
        elif local[name] != remote[name]:
            diff = "".join(difflib.unified_diff(
                remote[name].splitlines(keepends=True),
                local[name].splitlines(keepends=True),
                fromfile=f"{repo}:{name}@{ref}",
                tofile=f"content/{subject}/{name}"))
            problems.append(f"  {name} differs:\n{diff}")

    if problems:
        return "fail", f"{subject} has drifted from {repo}@{ref}\n" + "\n".join(problems)
    return "ok", f"content/{subject} matches {repo}@{ref} ({len(local)} files)"


def load_upstreams():
    if not UPSTREAMS.is_file():
        return {}
    return yaml.safe_load(UPSTREAMS.read_text()) or {}


def check_all(subjects=None, ref="main"):
    """[(subject, status, detail)] for every mapped subject."""
    upstreams = load_upstreams()
    wanted = subjects or sorted(upstreams)
    results = []
    for subject in wanted:
        repo = upstreams.get(subject)
        if not repo:
            results.append((subject, "skip", f"{subject}: no upstream in "
                                             f"content/upstreams.yaml"))
            continue
        status, detail = compare(subject, repo, ref)
        results.append((subject, status, detail))
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subjects", nargs="*", help="subject directories to check")
    ap.add_argument("--ref", default="main", help="ref to compare against")
    args = ap.parse_args()

    failed = False
    for _, status, detail in check_all(args.subjects or None, args.ref):
        if status == "fail":
            print(f"FAIL {detail}", file=sys.stderr)
            failed = True
        else:
            print(f"{status} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
