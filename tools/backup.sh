#!/bin/bash
# Back up every instance's database, plus the uploads, and rotate old copies.
#
#   tools/backup.sh                 # all instances in deploy/instances.yaml
#   tools/backup.sh miniasm santa   # just these
#
# Run from anywhere; paths are resolved from the script's own location. Driven
# by systemd — see docs/DEPLOY.md, "Scheduling it".
#
# Environment:
#   WS_BACKUP_DEST   where to write        (default /srv/backups)
#   WS_BACKUP_KEEP   copies to keep, each  (default 14)
#
# Everything that cannot be regenerated is the database and the uploads; the
# content, the runtime dists and the containers all rebuild from the repo.
#
# pipefail is load-bearing. `mysqldump | gzip > file` exits 0 when mysqldump
# dies, because gzip succeeded on the empty stream it was handed, and leaves a
# 20-byte archive that `gzip -t` calls valid. Without this line a broken backup
# reports success and is only discovered during a restore.
set -o pipefail
set -u

# A dump contains the whole config table: the registration code, the token
# secret and every password hash on the instance. Nothing here is for other
# users of the machine to read.
umask 077

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEST=${WS_BACKUP_DEST:-/srv/backups}
KEEP=${WS_BACKUP_KEEP:-14}
MANIFEST=$ROOT/deploy/instances.yaml
STAMP=$(date +%F)
failed=0

log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { log "FATAL: $*"; exit 1; }

[ -r "$MANIFEST" ] || die "no manifest at $MANIFEST"
mkdir -p "$DEST" || die "cannot create $DEST"

if [ $# -gt 0 ]; then
  names=("$@")
else
  mapfile -t names < <(python3 -c '
import sys, yaml
m = yaml.safe_load(open(sys.argv[1]))
for inst in m.get("instances", []):
    print(inst["name"])
' "$MANIFEST") || die "cannot read instance names from $MANIFEST"
fi
[ ${#names[@]} -gt 0 ] || die "no instances to back up"

# Keep the $KEEP most recent copies of one pattern. Deliberately count-based
# rather than `find -mtime +N`: if backups have been failing for a month, an
# age rule deletes the last good copy precisely when it is the only one left.
rotate() {
  local pattern=$1 old
  mapfile -t old < <(ls -1t "$DEST"/$pattern 2>/dev/null | tail -n +$((KEEP + 1)))
  [ ${#old[@]} -eq 0 ] && return 0
  log "  rotate: removing ${#old[@]} old copy/copies of $pattern"
  rm -f -- "${old[@]}"
}

for name in "${names[@]}"; do
  container="ctfd-$name-db-1"
  out="$DEST/backup-$name-$STAMP.sql.gz"

  if ! docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null | grep -q true; then
    log "SKIP $name: $container is not running"
    failed=1
    continue
  fi

  # --single-transaction: one consistent InnoDB snapshot instead of tables read
  # as they drift. Byte-identical to a plain dump on an idle instance, so there
  # is no reason not to.
  if docker exec "$container" sh -c \
       'exec mysqldump -uctfd -p"$MARIADB_PASSWORD" --single-transaction --quick ctfd' \
       | gzip > "$out.part"; then
    # A complete mysqldump ends with this line. Checking the trailer catches a
    # dump truncated by a container dying mid-stream, which pipefail does not.
    if zcat "$out.part" | tail -1 | grep -q '^-- Dump completed'; then
      mv "$out.part" "$out"
      log "OK   $name -> $(basename "$out") ($(du -h "$out" | cut -f1))"
    else
      rm -f "$out.part"
      log "FAIL $name: dump has no completion trailer, discarded"
      failed=1
      continue
    fi
  else
    rm -f "$out.part"
    log "FAIL $name: mysqldump exited non-zero, discarded"
    failed=1
    continue
  fi

  rotate "backup-$name-*.sql.gz"
done

# Uploads. One archive for all instances: they are small, and a per-instance
# split would need the data directory names, which the env files own.
uploads="$DEST/backup-uploads-$STAMP.tar.gz"
if compgen -G "$ROOT/.data-prod-*/CTFd/uploads" >/dev/null; then
  if tar czf "$uploads.part" -C "$ROOT" .data-prod-*/CTFd/uploads; then
    mv "$uploads.part" "$uploads"
    log "OK   uploads -> $(basename "$uploads") ($(du -h "$uploads" | cut -f1))"
    rotate "backup-uploads-*.tar.gz"
  else
    rm -f "$uploads.part"
    log "FAIL uploads: tar exited non-zero, discarded"
    failed=1
  fi
else
  log "SKIP uploads: no .data-prod-*/CTFd/uploads under $ROOT"
fi

if [ "$failed" -ne 0 ]; then
  log "finished WITH FAILURES"
  exit 1
fi
log "finished cleanly, keeping $KEEP copies each in $DEST"
