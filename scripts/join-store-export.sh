#!/usr/bin/env bash
# scripts/join-store-export.sh -- pg_dump -Fc of the Postgres-backed join
# store, the procedural half of the cluster-destruction gate (plan §6.3):
# export, verify the export opens (pg_restore --list), then destroy.
# DigitalOcean deletes a managed cluster's automated backups when the
# cluster is destroyed -- there is no "restore the cluster we deleted last
# month," so this is the last line of evidence continuity before that.
#
# Refuses outright on a SQLite deployment -- there is nothing to export:
# `cp` of out/join-store/join-store.sqlite3 is already that backend's
# evidence mechanism (scripts/migrate-join-store.py's own
# out/join-migrated/<timestamp>/ convention, which this script mirrors for
# its own output).
#
# lib-core.sh only, not lib-stack.sh -- same reason scripts/member.sh's
# cmd_refresh avoids it: lib-stack.sh's api_key/api helpers are curl-based
# X-Road admin-API helpers this script has no use for, and its credential
# guard covers XROAD_* secrets this script never touches. docker compose is
# invoked directly, the same construction lib-stack.sh's own COMPOSE array
# uses, inlined.
#
# The join-api image has no Docker socket (Dockerfile's own note on design
# decision 8) and never runs as root inside the container in this compose
# service definition's normal path -- pg_dump/pg_restore run as whatever
# `user:` docker-compose.yml's join-api service sets (KP2_HOST_UID/GID, or
# root in a throwaway `run`), same as every other command this pack already
# runs inside that image.
set -euo pipefail
. "$(dirname "$0")/lib-core.sh"

# Owner-only from here on: both $DEST_DIR (mkdir, below) and $DUMP_FILE (the
# host-side redirect further down) inherit this, so the dump -- the whole
# join store, applicant contact/payload data and the token table included --
# is never group- or world-readable. A chmod after the fact would leave a
# window; umask before either is created does not.
umask 077

# Where exports land. Defaults to the laptop-local convention
# (out/join-migrated/, same as scripts/migrate-join-store.py's own archive
# directory) so docker-local's zero-setup path is unchanged. This script is
# run interactively on the droplet (runbook.md §6.3, cluster destruction is
# never a CI action) -- infra/ci/remote-deploy.sh's own export of this same
# variable covers only its own CI-driven process, not that later shell, so
# the operator must set KP2_EXPORT_DIR=/opt/kp2/exports on the command line
# there themselves. Outside every container bind mount, which is the half
# of the join-store-export finding a umask alone cannot fix.
: "${KP2_EXPORT_DIR:=$PACK_DIR/out/join-migrated}"

# Missing deployment.yaml defaults to "sqlite", same as app.py's own
# _DATASTORE_KIND resolution -- a pack with no deployment.yaml at all has
# nothing Postgres to export from either.
DATASTORE_KIND=$(yq_get "$PACK_DIR/deployment.yaml" datastore.kind 2>/dev/null || echo sqlite)
[ "$DATASTORE_KIND" = "postgres" ] || fail "deployment.yaml's datastore.kind is '$DATASTORE_KIND', not 'postgres' -- there is nothing to export from a SQLite deployment. cp of out/join-store/join-store.sqlite3 is already that backend's evidence mechanism (scripts/migrate-join-store.py's own out/join-migrated/<timestamp>/ convention, which this script mirrors)."

# Only one variable to source-with-fallback here, unlike member.sh
# cmd_refresh's two-variable save/restore dance around sourcing .env (that
# dance exists so sourcing .env for one missing variable doesn't clobber an
# operator's deliberately-exported OTHER variable) -- with a single
# variable there is nothing to clobber, so: source .env only when
# KP2_JOIN_DB_URL isn't already in the environment.
if [ -z "${KP2_JOIN_DB_URL:-}" ] && [ -f "$PACK_DIR/.env" ]; then
  set -a; . "$PACK_DIR/.env"; set +a
fi
case "${KP2_JOIN_DB_URL:-}" in
  ""|*CHANGEME*)
    fail "KP2_JOIN_DB_URL is unset (or still the .env.example placeholder) in the environment and $PACK_DIR/.env -- this is the DSN pg_dump connects with." ;;
esac

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST_DIR="$KP2_EXPORT_DIR/$TIMESTAMP"
mkdir -p "$DEST_DIR"
DUMP_FILE="$DEST_DIR/kp2_join.dump"

# A pg_dump that dies mid-stream (set -e exits this script) would otherwise
# leave a truncated dump sitting in this timestamped evidence directory --
# caught later by pg_restore --list below if this script gets that far
# again, but not cleaned up even then. Armed before pg_dump runs, disarmed
# only after this script's own verification step (pg_restore --list) has
# actually passed, so any non-zero exit before a fully verified dump exists
# removes the partial file instead of leaving it to be mistaken for a real
# export.
trap 'rm -f "$DUMP_FILE"' EXIT

# `docker compose run --rm` throws the container away on exit -- a file it
# wrote INSIDE the container (e.g. via pg_dump -f) would be lost with it.
# pg_dump writes the custom-format dump to stdout when -f is omitted, so
# the host-side redirect below is what actually lands the bytes on disk;
# confirmed live that `docker compose run`'s own container-lifecycle
# messages ("Creating...", "Created...") land on stderr, not stdout, so
# this redirect captures exactly the dump and nothing else.
#
# $KP2_JOIN_DB_URL is NOT passed as an argv here -- docker-compose.yml
# already injects it into join-api's own environment (its `environment:`
# block), so `sh -c '... "$KP2_JOIN_DB_URL"'` reads it from inside the
# container instead. Interpolating the DSN into the command line would put
# the password in this host process's argv, visible to any local user via
# `ps auxww` (or a `bash -x` trace) for the run's duration -- exactly what
# the masking discipline elsewhere in this pair of scripts exists to avoid.
log "exporting kp2_join (pg_dump -Fc) -> $DUMP_FILE"
docker compose -f "$PACK_DIR/docker-compose.yml" run --rm -T join-api \
  sh -c 'exec pg_dump -Fc "$KP2_JOIN_DB_URL"' > "$DUMP_FILE"

[ -s "$DUMP_FILE" ] || fail "pg_dump exited 0 but $DUMP_FILE is empty -- something is wrong even though the command reported success"

# The plan's own explicit gate step: "verify the export opens." A small
# bind mount just for this -- the dump now lives on the host, not inside
# whatever throwaway container pg_dump ran in.
log "verifying the export opens (pg_restore --list)"
# --user, not the compose service's own default: the dump is 0600 (umask
# 077, above), so this one-shot container must run as its owner to read it.
# Needed on a laptop today (join-api's default user may differ from the
# operator invoking this script) and after Phase B unconditionally, once
# join-api's default user becomes the dedicated `kp2` identity, which will
# never own an operator-created dump.
docker compose -f "$PACK_DIR/docker-compose.yml" run --rm -T \
  --user "$(id -u):$(id -g)" \
  -v "$DUMP_FILE:/tmp/kp2-join-export-verify.dump:ro" \
  join-api pg_restore --list /tmp/kp2-join-export-verify.dump

trap - EXIT  # verified good -- keep the file, don't remove it on exit

log "export verified: $DUMP_FILE"
log "move this file somewhere durable before destroying the cluster (plan §6.3) -- DigitalOcean deletes automated backups when the cluster itself is destroyed, so this file is the only copy of the evidence once that happens."
