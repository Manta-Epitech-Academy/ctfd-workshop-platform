#!/usr/bin/env python3
"""Check that the vendored parser matches the published one.

    python3 tools/check_parser_sync.py [--ref main]

The convention's central promise is that CI and the importer run **the same
parser** (docs/CONTENT_CONVENTION.md §3.9). Subject repos lint against
`ws_parser.py` published in workshop-content-tools; this repository imports its
own `tools/ws_parser.py`. Two copies means the promise holds only as long as
somebody keeps them identical, so make the divergence loud instead.

Canonical copy: https://github.com/kevin-cazal/workshop-content-tools

Fix a mismatch by copying in whichever direction is correct — usually develop
here, then push the file to workshop-content-tools and pin subject repos to the
new ref.
"""
import argparse
import difflib
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAW = ("https://raw.githubusercontent.com/kevin-cazal/workshop-content-tools/"
       "{ref}/ws_parser.py")
LOCAL = Path(__file__).parent / "ws_parser.py"


def compare(ref="main", timeout=20):
    """Returns (status, detail) where status is 'ok', 'skip' or 'fail'.

    'skip' means the published copy could not be fetched. Callers should report
    it as such rather than as a pass — it proves nothing either way.
    """
    url = RAW.format(ref=ref)
    try:
        published = urllib.request.urlopen(url, timeout=timeout).read().decode()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Offline is not a failure: this is a consistency check, not a gate on
        # being able to work on a train.
        return "skip", f"cannot reach {url} ({e})"

    local = LOCAL.read_text()
    if local == published:
        return "ok", f"tools/ws_parser.py matches workshop-content-tools@{ref}"

    diff = "".join(difflib.unified_diff(
        published.splitlines(keepends=True), local.splitlines(keepends=True),
        fromfile=f"published@{ref}", tofile="tools/ws_parser.py"))
    return "fail", f"tools/ws_parser.py differs from workshop-content-tools@{ref}\n\n{diff}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="main",
                    help="ref of workshop-content-tools to compare against")
    args = ap.parse_args()

    status, detail = compare(args.ref)
    if status == "fail":
        print(f"FAIL {detail}", file=sys.stderr)
        return 1
    print(f"{status} {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
