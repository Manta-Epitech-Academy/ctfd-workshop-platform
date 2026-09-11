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
#                                       lolight, the syntax highlighter the core
#                                       theme already uses — see below for why a
#                                       second copy is the right answer.
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

# The core theme highlights `pre code` exactly once, on DOMContentLoaded
# (themes/core/assets/js/theme/highlight.js), and does not put lolight on
# `window`. The workshop page fills a step's body in place after a solve, so
# every code block from the second step onwards arrived unhighlighted — measured
# on a real run: 0 `.ll-*` spans in every body the page injected itself.
#
# Reaching into the core bundle to re-export lolight would be a core edit
# (CLAUDE.md), so the plugin carries its own copy. Same version the theme
# depends on (package.json: "lolight": "^1.4.0"), 2.6 KB, UMD, global `lolight`.
# Its DOMContentLoaded auto-run targets `.lolight`, a class nothing here uses,
# so loading it twice highlights nothing twice.
LOLIGHT_VERSION="1.4.1"
LOLIGHT_URL="https://unpkg.com/lolight@${LOLIGHT_VERSION}/dist/lolight.min.js"
LOLIGHT_SHA256="29ca09f5cd831f7e60bd73a7a4f6e911b7dcaadada97f6d0ba8d653fe4c24a2a"

# The image's interpreter, from CTFd/Dockerfile's base. If a CTFd upgrade moves
# it, this is the line to change — a wheel built for the wrong tag imports as
# "no module named yaml" inside the container while working perfectly here.
PY_VERSION="3.11"
PY_PLATFORM="manylinux2014_x86_64"

force=""
[[ "${1:-}" == "--force" ]] && force=1

if [[ -n "$force" || ! -d "$PY_DIR/yaml" ]]; then
  # Resolved and checked BEFORE the rm below. This used to say `pip3` and delete
  # first: on a machine without pip3 on PATH (a distro that ships only
  # `python3 -m pip`, or none at all) the script wiped a working vendor tree and
  # then died, leaving the container unable to import yaml at all.
  PIP=""
  if command -v pip3 >/dev/null 2>&1; then PIP="pip3"
  elif command -v pip >/dev/null 2>&1; then PIP="pip"
  elif python3 -m pip --version >/dev/null 2>&1; then PIP="python3 -m pip"
  else
    echo "no pip found (tried pip3, pip, python3 -m pip)." >&2
    echo "PyYAML is left as it is. Install pip, or build it through the image:" >&2
    echo "  docker exec <ctfd container> sh -c 'pip install --target /tmp/vend \\" >&2
    echo "      --no-deps --only-binary=:all: $PYYAML'" >&2
    echo "  docker cp <ctfd container>:/tmp/vend/. $PY_DIR/" >&2
    exit 1
  fi
  rm -rf "$PY_DIR"
  mkdir -p "$PY_DIR"
  echo "vendor: $PYYAML for CPython $PY_VERSION / $PY_PLATFORM"
  $PIP install --quiet --target "$PY_DIR" --no-deps --only-binary=:all: \
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

if [[ -n "$force" || ! -f "$JS_DIR/lolight.min.js" ]]; then
  mkdir -p "$JS_DIR"
  echo "vendor: lolight $LOLIGHT_VERSION"
  curl -fsSL -o "$JS_DIR/lolight.min.js.part" "$LOLIGHT_URL"
  got="$(sha256sum "$JS_DIR/lolight.min.js.part" | cut -d' ' -f1)"
  if [[ "$got" != "$LOLIGHT_SHA256" ]]; then
    rm -f "$JS_DIR/lolight.min.js.part"
    echo "lolight sha256 mismatch: expected $LOLIGHT_SHA256, got $got" >&2
    exit 1
  fi
  mv "$JS_DIR/lolight.min.js.part" "$JS_DIR/lolight.min.js"
else
  echo "vendor: lolight already there (--force to redo)"
fi

echo "vendor: done. The container picks these up on its next restart."
