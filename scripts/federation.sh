#!/usr/bin/env bash
# Snapshot and restore the federation's deployed state -- testing-strategy
# plan Task 3. The state is just the ~19 named kp2-* Docker volumes.
# Measured live (docs/production-delta.md): snapshot ~64s, restore mechanics
# ~52s, but ~315s (~5.25 min) total before the restored federation is
# actually healthy and verified working -- container boot time after the
# untar, not the untar itself. Still ~3x faster than a full redeploy from
# zero (~918s, scripts/verify.sh --full, README.md).
#
#   scripts/federation.sh snapshot [name]   # default name: a UTC timestamp
#   scripts/federation.sh restore <name>    # WIPES current volumes first
#   scripts/federation.sh list
#   scripts/federation.sh rm <name>
#
# SHELF LIFE: a restored snapshot is not restorable forever. X-Road's own
# authorisation-cache/OCSP-freshness window (docs/xroad-770-notes.md "Known
# traps": ~10 hours idle -> IncorrectValidationInfo: OCSP response is too
# old -> Server.ClientProxy.SslAuthenticationFailed on every cross-server
# call) is measured from when the servers last actually fetched a fresh
# OCSP response -- i.e. from SNAPSHOT time, not restore time. A snapshot
# taken today and restored next week starts that idle clock at "today", not
# "next week", and should be assumed stale on arrival. Confirmed live: an
# immediate snapshot -> restore cycle worked end to end; a federation whose
# volumes had existed ~18 real hours failed a restart with exactly this
# symptom (token status OK, not a PIN mismatch) and did not self-heal. The
# true boundary between those two points, and the multi-day case this
# file's own plan asked about, could not be observed inside one working
# session -- see docs/production-delta.md for what was and was not measured.
set -euo pipefail
PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAP_DIR="$PACK_DIR/.snapshots"

log()  { printf '\033[1;34m[federation]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[federation FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

volumes() { docker volume ls --format '{{.Name}}' | grep '^kp2-' | sort; }

restart_containers() {
  bash -c '
    set -euo pipefail
    cd "'"$PACK_DIR"'"
    . scripts/lib.sh
    "${COMPOSE[@]}" -f hurl/compose.hurl.yml up -d
  '
}

cmd_snapshot() {
  local name=${1:-$(date -u +%Y%m%dT%H%M%SZ)}
  local dir="$SNAP_DIR/$name"
  [ -d "$dir" ] && fail "snapshot '$name' already exists ($dir) -- choose a different name, or rm it first"
  local vols; vols=$(volumes)
  [ -n "$vols" ] || fail "no kp2-* volumes found -- nothing to snapshot"
  mkdir -p "$dir"

  log "stopping containers (volumes kept)"
  "$PACK_DIR/scripts/teardown.sh"

  local vol
  for vol in $vols; do
    log "tarring $vol"
    # Snapshotting a running PostgreSQL volume yields a torn database --
    # this is why containers are stopped first, not optional.
    docker run --rm -v "${vol}:/v:ro" -v "$dir:/s" alpine \
      tar czf "/s/${vol}.tgz" -C /v .
  done

  log "restarting containers"
  restart_containers

  local size; size=$(du -sh "$dir" | cut -f1)
  log "snapshot '$name' is $size -- .snapshots/ is gitignored; these are not small, clean them up (scripts/federation.sh rm)"
}

cmd_restore() {
  local name=${1:?"restore needs a snapshot name -- scripts/federation.sh list"}
  local dir="$SNAP_DIR/$name"
  [ -d "$dir" ] || fail "no snapshot named '$name' -- scripts/federation.sh list"

  log "WARNING: this replaces every current kp2-* volume with '$name''s contents."
  log "purging current volumes"
  "$PACK_DIR/scripts/teardown.sh" --purge

  local tgz vol
  for tgz in "$dir"/*.tgz; do
    vol=$(basename "$tgz" .tgz)
    log "restoring $vol"
    # docker run against a not-yet-existing volume name creates it empty --
    # no separate `docker volume create` needed.
    docker run --rm -v "${vol}:/v" -v "$dir:/s:ro" alpine \
      sh -c "cd /v && tar xzf /s/${vol}.tgz"
  done

  log "restarting containers"
  restart_containers

  log "running --live to prove the restored federation actually works"
  "$PACK_DIR/scripts/verify.sh" --live
}

cmd_list() {
  [ -d "$SNAP_DIR" ] || { log "no snapshots (.snapshots/ does not exist)"; return 0; }
  local d
  for d in "$SNAP_DIR"/*/; do
    [ -d "$d" ] || continue
    printf '%s\t%s\n' "$(basename "$d")" "$(du -sh "$d" | cut -f1)"
  done
}

cmd_rm() {
  local name=${1:?"rm needs a snapshot name -- scripts/federation.sh list"}
  local dir="$SNAP_DIR/$name"
  [ -d "$dir" ] || fail "no snapshot named '$name'"
  rm -rf "$dir"
  log "removed snapshot '$name'"
}

case "${1:-}" in
  snapshot) shift; cmd_snapshot "$@" ;;
  restore)  shift; cmd_restore "$@" ;;
  list)     cmd_list ;;
  rm)       shift; cmd_rm "$@" ;;
  *) echo "usage: scripts/federation.sh snapshot [name] | restore <name> | list | rm <name>" >&2; exit 1 ;;
esac
