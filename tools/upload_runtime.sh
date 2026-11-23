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
# Storage is any S3-compatible service, named by environment:
#   RUNTIMES_S3_ENDPOINT    https://… (required)
#   RUNTIMES_S3_BUCKET      bucket name (required)
#   RUNTIMES_S3_REGION      region string the endpoint wants (default us-east-1)
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   a key limited to that bucket
# Uses the `aws` CLI when present (GitHub runners have it); otherwise runs
# amazon/aws-cli in Docker, like the rest of tools/ runs in Docker.
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
: "${RUNTIMES_S3_ENDPOINT:?set RUNTIMES_S3_ENDPOINT}"
: "${RUNTIMES_S3_BUCKET:?set RUNTIMES_S3_BUCKET}"
: "${AWS_ACCESS_KEY_ID:?set AWS_ACCESS_KEY_ID}"
: "${AWS_SECRET_ACCESS_KEY:?set AWS_SECRET_ACCESS_KEY}"
export AWS_DEFAULT_REGION="${RUNTIMES_S3_REGION:-us-east-1}"
# Path-style requests: every S3-compatible service accepts them, and a
# bucket name with a dot breaks virtual-host TLS otherwise.
export AWS_EC2_METADATA_DISABLED=true

DIST="$(cd "$DIST" && pwd)"
[ -z "$LATEST" ] || LATEST="$(cd "$LATEST" && pwd)"

# Path-style requests, which every S3-compatible service accepts (a bucket
# name with a dot breaks virtual-host TLS otherwise). The CLI reads that from
# its config file only, so write a private one rather than touch ~/.aws.
CONF="$(mktemp)"
trap 'rm -f "$CONF"' EXIT
printf '[default]\ns3 =\n  addressing_style = path\n' > "$CONF"

if command -v aws >/dev/null 2>&1; then
  aws() { AWS_CONFIG_FILE="$CONF" command aws --endpoint-url "$RUNTIMES_S3_ENDPOINT" "$@"; }
  IN_DIST="$DIST"; IN_LATEST="$LATEST"
else
  # Only the two dists and the config are mounted, read-only; the container
  # sees nothing else. Values are passed explicitly: a `docker` that goes
  # through sudo does not forward the caller's environment.
  MOUNTS=(-v "$DIST:/dist:ro" -v "$CONF:/aws-config:ro")
  [ -z "$LATEST" ] || MOUNTS+=(-v "$LATEST:/latest:ro")
  aws() {
    docker run --rm --network host "${MOUNTS[@]}" \
      -e "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID" -e "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY" \
      -e "AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION" -e AWS_EC2_METADATA_DISABLED=true -e AWS_CONFIG_FILE=/aws-config \
      amazon/aws-cli --endpoint-url "$RUNTIMES_S3_ENDPOINT" "$@"
  }
  IN_DIST="/dist"; IN_LATEST="/latest"
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
