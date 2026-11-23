#!/usr/bin/env bash
# Upload a built runtime dist to the runtimes bucket (S3_PLAN.md, block 3).
#
#   tools/upload_runtime.sh <runtime-id> <version> [<dist-dir>] [--latest <dist-dir>] [--dry-run]
#
# Puts <dist-dir> under runtimes/<id>/<version>/ in the bucket, immutable
# (Cache-Control: public, max-age=31536000, immutable — the URL carries the
# commit sha, so a given path never changes content), and optionally a second
# dist under runtimes/<id>/latest/ with a five-minute cache: a Vite dist is
# built for one base URL, so the `latest` copy is a separate build, not a
# copy of the first (static runtimes use relative URLs and pass the same dir
# twice). <dist-dir> defaults to plugins/workshop/runtimes/<id>/<version>,
# what tools/build_runtime.sh produced.
#
# The proxy in front of CTFd serves these objects at /runtime/<id>/<version>/
# on CTFd's own origin (docs/DEPLOY.md, "Runtimes in the bucket"); nothing
# here is a URL a browser sees.
#
# Storage is any S3-compatible service, named by the RUNTIMES_S3_* and AWS_*
# environment tools/s3_cli.sh documents; that file also picks the `aws` CLI
# or amazon/aws-cli in Docker, like the rest of tools/ runs in Docker.
set -euo pipefail

RUNTIME_ID="${1:?usage: upload_runtime.sh <runtime-id> <version> [<dist-dir>] [--latest <dist-dir>] [--dry-run]}"
VERSION="${2:?usage: upload_runtime.sh <runtime-id> <version> [<dist-dir>] [--latest <dist-dir>] [--dry-run]}"
shift 2
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST=""
LATEST=""
DRY=()
while [ $# -gt 0 ]; do
  case "$1" in
    --latest) LATEST="${2:?--latest needs a directory}"; shift 2 ;;
    --dry-run) DRY=(--dryrun); shift ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) DIST="$1"; shift ;;
  esac
done
DIST="${DIST:-$REPO_ROOT/plugins/workshop/runtimes/$RUNTIME_ID/$VERSION}"

SAFE='^[A-Za-z0-9][A-Za-z0-9._-]*$'
[[ "$RUNTIME_ID" =~ $SAFE && "$VERSION" =~ $SAFE ]] || { echo "bad id/version: $RUNTIME_ID/$VERSION" >&2; exit 2; }
[ "$VERSION" != "latest" ] || { echo "'latest' is written with --latest, not as the version" >&2; exit 2; }
[ -f "$DIST/index.html" ] || { echo "no index.html in $DIST — build the runtime first" >&2; exit 2; }
[ -z "$LATEST" ] || [ -f "$LATEST/index.html" ] || { echo "no index.html in $LATEST" >&2; exit 2; }
DIST="$(cd "$DIST" && pwd)"
[ -z "$LATEST" ] || LATEST="$(cd "$LATEST" && pwd)"

# shellcheck source=tools/s3_cli.sh
. "$(dirname "$0")/s3_cli.sh"
if [ "$S3_IN_DOCKER" = 1 ]; then
  s3_mount "$DIST" /dist; IN_DIST=/dist
  [ -z "$LATEST" ] || { s3_mount "$LATEST" /latest; IN_LATEST=/latest; }
else
  IN_DIST="$DIST"; IN_LATEST="$LATEST"
fi

upload() {  # upload <host-dir> <dir-as-the-cli-sees-it> <prefix> <cache-control>
  local host="$1" src="$2" prefix="$3" cache="$4"
  local dest="s3://$RUNTIMES_S3_BUCKET/runtimes/$RUNTIME_ID/$prefix/"
  echo "==> $host -> $dest ($cache)"
  aws s3 sync "$src/" "$dest" --delete --no-progress "${DRY[@]}" --cache-control "$cache"
  # Types the CLI's mime table may not know, and that browsers refuse to run
  # under nosniff: WebAssembly.instantiateStreaming wants application/wasm,
  # a module script wants a JavaScript type. One server-side copy per kind.
  local ext_type ext type
  for ext_type in "wasm:application/wasm" "mjs:text/javascript"; do
    ext="${ext_type%%:*}"; type="${ext_type#*:}"
    if find "$host" -name "*.$ext" -print -quit | grep -q .; then
      aws s3 cp "$dest" "$dest" --recursive --exclude '*' --include "*.$ext" \
        --content-type "$type" --cache-control "$cache" --metadata-directive REPLACE \
        --no-progress "${DRY[@]}"
    fi
  done
}

upload "$DIST" "$IN_DIST" "$VERSION" "public, max-age=31536000, immutable"
[ -z "$LATEST" ] || upload "$LATEST" "$IN_LATEST" "latest" "public, max-age=300"
echo "==> done: $RUNTIMES_S3_BUCKET/runtimes/$RUNTIME_ID/$VERSION/${LATEST:+ and latest/}"
