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
DEST_DIR="$PACK_DIR/out/join-migrated/$TIMESTAMP"
mkdir -p "$DEST_DIR"
DUMP_FILE="$DEST_DIR/kp2_join.dump"

# `docker compose run --rm` throws the container away on exit -- a file it
# wrote INSIDE the container (e.g. via pg_dump -f) would be lost with it.
# pg_dump writes the custom-format dump to stdout when -f is omitted, so
# the host-side redirect below is what actually lands the bytes on disk;
# confirmed live that `docker compose run`'s own container-lifecycle
# messages ("Creating...", "Created...") land on stderr, not stdout, so
# this redirect captures exactly the dump and nothing else.
log "exporting kp2_join (pg_dump -Fc) -> $DUMP_FILE"
docker compose -f "$PACK_DIR/docker-compose.yml" run --rm -T join-api \
  pg_dump -Fc "$KP2_JOIN_DB_URL" > "$DUMP_FILE"

[ -s "$DUMP_FILE" ] || fail "pg_dump exited 0 but $DUMP_FILE is empty -- something is wrong even though the command reported success"

# The plan's own explicit gate step: "verify the export opens." A small
# bind mount just for this -- the dump now lives on the host, not inside
# whatever throwaway container pg_dump ran in.
log "verifying the export opens (pg_restore --list)"
docker compose -f "$PACK_DIR/docker-compose.yml" run --rm -T \
  -v "$DUMP_FILE:/tmp/kp2-join-export-verify.dump:ro" \
  join-api pg_restore --list /tmp/kp2-join-export-verify.dump

log "export verified: $DUMP_FILE"
log "move this file somewhere durable before destroying the cluster (plan §6.3) -- DigitalOcean deletes automated backups when the cluster itself is destroyed, so this file is the only copy of the evidence once that happens."
