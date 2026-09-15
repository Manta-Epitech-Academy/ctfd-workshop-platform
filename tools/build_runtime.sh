#!/usr/bin/env bash
# Build a runtime dist and install it where the workshop plugin serves it.
#
#   tools/build_runtime.sh tic80 [<git-ref>]
#
# Produces plugins/workshop/runtimes/<id>/<version>/ (gitignored — dists are
# rebuilt from a pinned ref, never vendored). Declare the same id/version in
# the subject's `runtime:` block and sync; the pane appears.
#
# Everything runs in Docker: no Node on the host, and the build is the same on
# any machine. See PLAN.md §14 and docs/RUNTIME_PROTOCOL.md.
set -euo pipefail

RUNTIME_ID="${1:?usage: build_runtime.sh <runtime-id> [git-ref]}"
REF="${2:-}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK_DIR:-${TMPDIR:-/tmp}/ws-runtime-build}"

# KIND selects how the dist is produced:
#   vite    npm ci && npm run build, with VITE_BASE baked in
#   static  the repo *is* the site; PREPARE fetches its vendored deps
#
# Per-case knobs: SUBMODULES=1 clones them too, NODE_IMAGE picks the build
# image, PASS_ENV lists environment variables forwarded into the build, and
# BUILD_CMD replaces the default npm sequence when a repo needs its own.
case "$RUNTIME_ID" in
  tic80)
    KIND="vite"
    # The `_runtime` repository, not kevin-cazal/tic80-web-editor: that one
    # is archived (2026-09-15), this one is what production builds from.
    SOURCE="https://github.com/kevin-cazal/tic80-web-editor_runtime.git"
    # TIC-80 PRO must be compiled from source with Emscripten to get the
    # patched embed API — 20-40 minutes and several GB (see the repo's
    # docker/tic80.Dockerfile). The image its CI publishes already contains
    # that artifact, so the two WASM files are copied out of it instead of
    # recompiling. Set TIC80_WASM_FROM=local to use your own public/tic80/
    # build instead.
    WASM_IMAGE="ghcr.io/kevin-cazal/tic80-web-editor_runtime:latest"
    WASM_IMAGE_DIR="/usr/share/nginx/html/runtime/tic80/latest/tic80"
    WASM_FILES="tic80.js tic80.wasm"
    ;;
  v86)
    KIND="vite"
    # The Alpine guest in the browser (PLAN.md §18). Built from the shell-rpg
    # product because that repo *is* v86-runner plus a welcome screen — and the
    # id is `v86`, not `shell-rpg`, because one dist serves every v86 subject:
    # what differs between them is which bundle to boot, which travels as
    # `runtime.params` (PLAN.md §19.2). The app is a thin shell around the
    # v86-runner submodule, so the checkout needs its submodules.
    SOURCE="https://github.com/kevin-cazal/shell-rpg.git"
    SUBMODULES=1
    # node:22 rather than :alpine — the repo's own prepare-assets.sh wants a
    # shell with curl for the case where the BIOS blobs are not in node_modules.
    NODE_IMAGE="node:22"
    # The ~330 MB `.v86b` is NOT part of the dist and never touches this
    # instance: the app downloads it from a CDN, its mirrors, or the mirroring
    # cache in compose/mirror (PLAN.md §18.8). Point the build at your own
    # mirror by exporting these before running:
    #
    #   VITE_OFFICIAL_BUNDLE_URL=http://your-host:8090/shell-rpg-256M.v86b
    #
    # Left unset, the repo's own defaults apply.
    PASS_ENV="VITE_OFFICIAL_BUNDLE_URL VITE_MIRROR_BUNDLE_URLS VITE_VM_MEMORY_MB"
    # The application code lives in the submodule, so its dependencies have to
    # be installed as well — `fzstd`, which decompresses the bundle, is one of
    # them and the build fails to resolve it otherwise. This is the sequence
    # the repo's own Pages workflow runs, copied rather than reinvented.
    BUILD_CMD="npm ci --no-audit --no-fund \
      && npm ci --no-audit --no-fund --prefix submodules/v86-runner \
      && npm run prepare \
      && cp -f public/coi-serviceworker.js submodules/v86-runner/public/ \
      && npm run build"
    ;;
  miniasm)
    KIND="static"
    # WDR+E: a browser VM and IDE for the paper computer's instruction set
    # (PLAN.md §21). This is the *port* — same app, plus the completion token it
    # reveals when a platform supplies a secret; upstream kevin-cazal/miniasm is
    # untouched and still runs standalone.
    SOURCE="https://github.com/kevin-cazal/miniasm_runtime.git"
    # No bundler: the repo root is the site. Its own script fetches Monaco and
    # Blockly into vendor/ — the port loads them relatively, so the workshop
    # keeps its editor in a room with no internet, and the dist works under any
    # path prefix.
    PREPARE="./scripts/vendor.sh"
    STATIC_EXCLUDE=(.git .github node_modules tests solutions other_implementations
                    package.json package-lock.json README.md brainstorm.md)
    ;;
  pacman)
    KIND="static"
    # Runtime only — the workshop text lives in pacman-ghost-ai_new and is
    # rendered by CTFd. While this repo is private, cloning over https needs
    # `gh auth setup-git`; RUNTIME_SOURCE overrides it with an ssh URL.
    SOURCE="https://github.com/kevin-cazal/pacman-ghost-ai_runtime.git"
    # No bundler: the repo root is the site, and every URL in index.html is
    # relative, so it works unchanged under /runtime/<id>/<version>/. Monaco
    # and Fengari are not vendored — the same two scripts the repo's own Pages
    # workflow runs fetch them into lib/.
    PREPARE="./scripts/setup-monaco.sh && ./scripts/setup-fengari.sh"
    # Repo furniture: nothing the served page loads. The workshop text is not
    # in this repo at all any more (PLAN.md §14 phase D), so there is nothing
    # content-shaped left to exclude.
    STATIC_EXCLUDE=(.git .github node_modules tests scripts
                    playwright.config.js package.json package-lock.json
                    Dockerfile .dockerignore README.md)
    ;;
  *)
    echo "unknown runtime '$RUNTIME_ID' — add a case here" >&2
    exit 2
    ;;
esac

SOURCE="${RUNTIME_SOURCE:-$SOURCE}"

# Run the build as the invoking user. Left as root, npm and the setup scripts
# write root-owned files into the shared checkout, and the next run cannot even
# `git fetch` into it — it has to be deleted from a root container first.
# HOME must be writable or npm refuses to start.
DOCKER_AS_ME="--user $(id -u):$(id -g) -e HOME=/tmp"

SRC="$WORK/$RUNTIME_ID"
mkdir -p "$WORK"
# A pinned ref is a short commit sha — that is what a subject records
# (content/miniasm/subject.yaml). Git accepts a sha in neither
# `clone --branch` nor `fetch <ref>`: both want a branch or a tag. So a pinned
# build needs real history to resolve against, and only an unpinned one can
# take the cheap --depth 1 path. Getting this wrong is invisible on a machine
# that already has the checkout cached, and fails on a fresh server.
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch --tags --force origin
  if [ -n "$REF" ] && ! git -C "$SRC" rev-parse --verify -q "$REF^{commit}" >/dev/null; then
    # A checkout cached shallow by an earlier unpinned build cannot see an
    # older pin. Deepen it rather than start over.
    git -C "$SRC" fetch --unshallow --tags origin 2>/dev/null || true
  fi
  TARGET="${REF:-FETCH_HEAD}"
elif [ -n "$REF" ]; then
  git clone "$SOURCE" "$SRC"
  TARGET="$REF"
else
  git clone --depth 1 "$SOURCE" "$SRC"
  TARGET="HEAD"
fi
git -C "$SRC" checkout --detach "$TARGET"
if [ "${SUBMODULES:-0}" = "1" ]; then
  # A product repo whose application code is a submodule (v86-runner) cannot
  # build without them. --depth 1: none of the history is wanted.
  git -C "$SRC" submodule update --init --recursive --depth 1
fi

VERSION="$(git -C "$SRC" rev-parse --short HEAD)"
DEST="$REPO_ROOT/plugins/workshop/runtimes/$RUNTIME_ID/$VERSION"
echo "==> $RUNTIME_ID @ $VERSION -> $DEST"

if [ -n "${WASM_FILES:-}" ] && [ "${TIC80_WASM_FROM:-image}" = "image" ]; then
  # `docker create` + `docker cp`: no container runs, and the files come
  # out owned by the invoking user, unlike a bind-mounted `cp` as root.
  mkdir -p "$SRC/public/tic80"
  docker pull -q "$WASM_IMAGE" >/dev/null
  CID="$(docker create "$WASM_IMAGE")"
  for f in $WASM_FILES; do
    echo "==> copying prebuilt $f out of $WASM_IMAGE"
    docker cp -q "$CID:$WASM_IMAGE_DIR/$f" "$SRC/public/tic80/$f"
  done
  docker rm -f "$CID" >/dev/null
fi

rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"

if [ "$KIND" = "vite" ]; then
  # The dist is served under /runtime/<id>/<version>/, so it must be built for
  # that base — Vite bakes absolute asset URLs at build time.
  ENV_ARGS=()
  for v in ${PASS_ENV:-}; do
    [ -n "${!v:-}" ] && ENV_ARGS+=(-e "$v=${!v}")
  done
  docker run --rm -v "$SRC:/app" -w /app $DOCKER_AS_ME \
    -e "VITE_BASE=/runtime/$RUNTIME_ID/$VERSION/" -e "BUILD_ID=$VERSION" \
    "${ENV_ARGS[@]}" \
    "${NODE_IMAGE:-node:22-alpine}" \
    sh -c "${BUILD_CMD:-npm ci --no-audit --no-fund && npm run build}"
  cp -r "$SRC/dist" "$DEST"
else
  # Static: the checkout is the site. Only its vendored dependencies have to be
  # fetched, by the repo's own scripts, so this build never second-guesses which
  # Monaco or Fengari version the runtime expects.
  # node:22 rather than :alpine — the setup scripts need bash and curl, and
  # installing them with apk would mean running as root, which is exactly what
  # leaves the shared checkout undeletable.
  docker run --rm -v "$SRC:/app" -w /app $DOCKER_AS_ME node:22 \
    bash -c "$PREPARE"
  mkdir -p "$DEST"
  EXCLUDES=()
  for e in "${STATIC_EXCLUDE[@]}"; do EXCLUDES+=(--exclude="$e"); done
  tar -C "$SRC" "${EXCLUDES[@]}" -cf - . | tar -C "$DEST" -xf -
fi
echo "==> installed. Declare in subject.yaml:"
echo "    runtime: { id: $RUNTIME_ID, version: $VERSION }"
