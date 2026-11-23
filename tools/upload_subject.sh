#!/usr/bin/env bash
# Archive the checked-out commit of a subject (or workshop) repository into
# the content bucket, where an instance set to WORKSHOP_SUBJECTS_URL syncs
# from (plugins/workshop/source.py; docs/DEPLOY.md "Subjects in the bucket").
#
#   tools/upload_subject.sh <owner/name> [<ref-name>] [--dry-run]
#
# Run inside the repository's checkout (the CI's working directory, or a
# clone). Writes, under subjects/<owner>/<name>/:
#   <sha>.tar.gz      `git archive` of HEAD, one top-level directory like a
#                     GitHub tarball — immutable
#   <sha>.json        {sha, message, ref, submodules: {path: {repo, sha}}} —
#                     what source.py reads to resolve a sha and `ref: submodule`
#   refs/<ref>.json   the same record for the branch or tag (default: the
#                     current branch), refreshed on every push, cached a minute
# Storage: the RUNTIMES_S3_* and AWS_* environment of tools/s3_cli.sh — the
# same bucket and key as the runtimes, one prefix beside them.
set -euo pipefail

REPO="${1:?usage: upload_subject.sh <owner/name> [<ref-name>] [--dry-run]}"
shift
REF=""
DRY=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=(--dryrun); shift ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) REF="$1"; shift ;;
  esac
done
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "not an owner/name repository: $REPO" >&2; exit 2; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "run this inside the repository's checkout" >&2; exit 2; }
[ -f subject.yaml ] || [ -f workshop.yaml ] || { echo "no subject.yaml or workshop.yaml here — not a subject repository" >&2; exit 2; }

SHA="$(git rev-parse HEAD)"
REF="${REF:-$(git rev-parse --abbrev-ref HEAD)}"
[ "$REF" != "HEAD" ] || { echo "detached HEAD: name the ref (branch or tag) as the second argument" >&2; exit 2; }
NAME="${REPO#*/}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/refs"
git archive --format=tar.gz --prefix="${NAME}-${SHA:0:7}/" -o "$WORK/$SHA.tar.gz" HEAD
# The record: the subject line, the ref, and every submodule's pin — the
# archive carries a submodule as an empty directory, so the pin travels here.
python3 - "$SHA" "$REF" "$(git log -1 --format=%s)" > "$WORK/$SHA.json" <<'PY'
import json, re, subprocess, sys
sha, ref, message = sys.argv[1:4]
subs = {}
status = subprocess.run(["git", "submodule", "status"], capture_output=True, text=True).stdout
for line in status.splitlines():
    m = re.match(r"^[ +-]?([0-9a-f]{40}) (\S+)", line)
    if not m:
        continue
    url = subprocess.run(["git", "config", "-f", ".gitmodules", "--get", f"submodule.{m.group(2)}.url"],
                         capture_output=True, text=True).stdout.strip()
    r = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if r:
        subs[m.group(2)] = {"repo": r.group(1), "sha": m.group(1)}
json.dump({"sha": sha, "message": message, "ref": ref, "submodules": subs}, sys.stdout, indent=1)
PY
cp "$WORK/$SHA.json" "$WORK/refs/$REF.json"

# shellcheck source=tools/s3_cli.sh
. "$(dirname "$0")/s3_cli.sh"
IN="$WORK"
[ "$S3_IN_DOCKER" = 0 ] || { s3_mount "$WORK" /subject; IN=/subject; }
DEST="s3://$RUNTIMES_S3_BUCKET/subjects/$REPO"
echo "==> $REPO@$REF -> $DEST/$SHA ($(du -h "$WORK/$SHA.tar.gz" | cut -f1))"
IMMUTABLE="public, max-age=31536000, immutable"
aws s3 cp "$IN/$SHA.tar.gz" "$DEST/$SHA.tar.gz" --content-type application/gzip --cache-control "$IMMUTABLE" --no-progress "${DRY[@]}"
aws s3 cp "$IN/$SHA.json"   "$DEST/$SHA.json"   --content-type application/json --cache-control "$IMMUTABLE" --no-progress "${DRY[@]}"
aws s3 cp "$IN/refs/$REF.json" "$DEST/refs/$REF.json" --content-type application/json --cache-control "public, max-age=60" --no-progress "${DRY[@]}"
echo "==> done: $DEST/{$SHA.tar.gz,$SHA.json,refs/$REF.json}"
