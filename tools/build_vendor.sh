#!/usr/bin/env bash
# Build the dependencies the CTFd image does not ship, for the sync page
# (PLAN.md §26.5). Gitignored build artifacts, like plugins/workshop/runtimes/.
#
#   tools/build_vendor.sh          # build both, skip what is already there
#   tools/build_vendor.sh --force  # rebuild from scratch
#
# Two things, for two runtimes:
#
#   plugins/workshop/vendor/            PyYAML, imported by the parser inside
#                                       the container. Built for the *image's*
#                                       interpreter (CPython 3.11, manylinux
#                                       x86_64), not for whatever is on this
#                                       machine — pip is told so explicitly.
#   plugins/workshop/assets/vendor/     openpgp.js, loaded by the admin page to
#                                       decrypt an answers file in the browser.
#                                       The passphrase never reaches the server
#                                       (PLAN.md §26.3).
#                                       canvas-confetti, for the workshop page's
#                                       completion moments.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY_DIR="$ROOT/plugins/workshop/vendor"
JS_DIR="$ROOT/plugins/workshop/assets/vendor"

# Pinned, and checked. A dependency that decrypts answers is not something to
# take at whatever version a CDN feels like serving today.
PYYAML="pyyaml==6.0.2"
OPENPGP_VERSION="6.2.2"
OPENPGP_URL="https://unpkg.com/openpgp@${OPENPGP_VERSION}/dist/openpgp.min.mjs"
OPENPGP_SHA256="05fc93ab2c58a5187b94f990cf5a496c6a6a0459bc7207364ed8c97800c9cace"

CONFETTI_VERSION="1.9.3"
CONFETTI_URL="https://unpkg.com/canvas-confetti@${CONFETTI_VERSION}/dist/confetti.browser.js"
CONFETTI_SHA256="e103ab02784339d56c93ca3debe2c5a299372cafc5215148d55283de046e86d1"

# The image's interpreter, from CTFd/Dockerfile's base. If a CTFd upgrade moves
# it, this is the line to change — a wheel built for the wrong tag imports as
# "no module named yaml" inside the container while working perfectly here.
PY_VERSION="3.11"
PY_PLATFORM="manylinux2014_x86_64"

force=""
[[ "${1:-}" == "--force" ]] && force=1

if [[ -n "$force" || ! -d "$PY_DIR/yaml" ]]; then
  rm -rf "$PY_DIR"
  mkdir -p "$PY_DIR"
  echo "vendor: $PYYAML for CPython $PY_VERSION / $PY_PLATFORM"
  pip3 install --quiet --target "$PY_DIR" --no-deps --only-binary=:all: \
       --platform "$PY_PLATFORM" --python-version "$PY_VERSION" "$PYYAML"
else
  echo "vendor: PyYAML already built (--force to redo)"
fi

if [[ -n "$force" || ! -f "$JS_DIR/openpgp.min.mjs" ]]; then
  mkdir -p "$JS_DIR"
  echo "vendor: openpgp.js $OPENPGP_VERSION"
  curl -fsSL -o "$JS_DIR/openpgp.min.mjs.part" "$OPENPGP_URL"
  got="$(sha256sum "$JS_DIR/openpgp.min.mjs.part" | cut -d' ' -f1)"
  if [[ "$got" != "$OPENPGP_SHA256" ]]; then
    rm -f "$JS_DIR/openpgp.min.mjs.part"
    echo "openpgp.js sha256 mismatch: expected $OPENPGP_SHA256, got $got" >&2
    exit 1
  fi
  mv "$JS_DIR/openpgp.min.mjs.part" "$JS_DIR/openpgp.min.mjs"
else
  echo "vendor: openpgp.js already there (--force to redo)"
fi

if [[ -n "$force" || ! -f "$JS_DIR/confetti.browser.js" ]]; then
  mkdir -p "$JS_DIR"
  echo "vendor: canvas-confetti $CONFETTI_VERSION"
  curl -fsSL -o "$JS_DIR/confetti.browser.js.part" "$CONFETTI_URL"
  got="$(sha256sum "$JS_DIR/confetti.browser.js.part" | cut -d' ' -f1)"
  if [[ "$got" != "$CONFETTI_SHA256" ]]; then
    rm -f "$JS_DIR/confetti.browser.js.part"
    echo "canvas-confetti sha256 mismatch: expected $CONFETTI_SHA256, got $got" >&2
    exit 1
  fi
  mv "$JS_DIR/confetti.browser.js.part" "$JS_DIR/confetti.browser.js"
else
  echo "vendor: canvas-confetti already there (--force to redo)"
fi

echo "vendor: done. The container picks these up on its next restart."
