#!/usr/bin/env bash
# Decrypt or re-encrypt the authored answer files.
#
#   GPG_PASSPHRASE=... tools/answers.sh decrypt   # before a sync
#   GPG_PASSPHRASE=... tools/answers.sh encrypt   # after editing an answer
#
# The plaintext is gitignored. This repo is public: an answer file must only
# ever be committed in its .gpg form (docs/CONTENT_CONVENTION.md §3.3b, §3.7).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACTION="${1:?usage: answers.sh decrypt|encrypt}"

FILES=(
  "content/shell_1/flags.yaml"
  "content/pypong/quiz_answers.yaml"
)

if [[ -z "${GPG_PASSPHRASE:-}" ]]; then
  echo "Set GPG_PASSPHRASE in the environment." >&2
  exit 1
fi

for f in "${FILES[@]}"; do
  case "$ACTION" in
    decrypt)
      [[ -f "$ROOT/$f.gpg" ]] || continue
      gpg --batch --yes --pinentry-mode loopback --passphrase "$GPG_PASSPHRASE" \
        -o "$ROOT/$f" --decrypt "$ROOT/$f.gpg"
      echo "  decrypted $f.gpg -> $f"
      ;;
    encrypt)
      [[ -f "$ROOT/$f" ]] || continue
      gpg --batch --yes --pinentry-mode loopback --passphrase "$GPG_PASSPHRASE" \
        --symmetric --cipher-algo AES256 -o "$ROOT/$f.gpg" "$ROOT/$f"
      echo "  encrypted $f -> $f.gpg"
      ;;
    *) echo "unknown action '$ACTION' (decrypt|encrypt)" >&2; exit 2 ;;
  esac
done
