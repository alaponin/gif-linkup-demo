#!/usr/bin/env bash
# scripts/join-store-import.sh -- the counterpart to join-store-export.sh:
# pg_restore a previously-exported dump (pg_dump -Fc format) into a
# Postgres cluster, meant to run at provision time against an empty
# cluster (plan §6.3). Destructive to whatever database KP2_JOIN_DB_URL
# names -- this is meant to be run non-interactively by provisioning
# tooling, so it does NOT prompt for confirmation, but it DOES print the
# target (host/dbname, password redacted) before touching anything, so
# that's visible in whatever log captured the run.
#
# Same refusal checks and lib-core.sh-only sourcing as
# scripts/join-store-export.sh -- see that script's header comment for why.
set -euo pipefail
. "$(dirname "$0")/lib-core.sh"

DUMP_FILE=${1:?"Usage: scripts/join-store-import.sh <dump-file>"}
[ -f "$DUMP_FILE" ] || fail "no dump file at $DUMP_FILE"
# Absolute path -- the bind mount below needs one, and a relative $1 would
# otherwise resolve against whatever directory happened to be current when
# this script was invoked, not $PACK_DIR.
DUMP_FILE="$(cd "$(dirname "$DUMP_FILE")" && pwd)/$(basename "$DUMP_FILE")"

DATASTORE_KIND=$(yq_get "$PACK_DIR/deployment.yaml" datastore.kind 2>/dev/null || echo sqlite)
[ "$DATASTORE_KIND" = "postgres" ] || fail "deployment.yaml's datastore.kind is '$DATASTORE_KIND', not 'postgres' -- there is no Postgres cluster configured to import into."

# Same single-variable source-with-fallback as join-store-export.sh -- see
# that script's comment on why this doesn't need cmd_refresh's two-variable
# save/restore dance.
if [ -z "${KP2_JOIN_DB_URL:-}" ] && [ -f "$PACK_DIR/.env" ]; then
  set -a; . "$PACK_DIR/.env"; set +a
fi
case "${KP2_JOIN_DB_URL:-}" in
  ""|*CHANGEME*)
    fail "KP2_JOIN_DB_URL is unset (or still the .env.example placeholder) in the environment and $PACK_DIR/.env -- this is the DSN pg_restore writes into." ;;
esac

# Password-redacted DSN for the log line only -- the real one is never
# echoed. Strips whatever sits between the first `:` after `://user` and
# the following `@`, which is the only DSN shape this pack's KP2_JOIN_DB_URL
# is documented in (.env.example's postgresql://user:PASSWORD@host:port/db
# form) -- store.py's own _mask_dsn round-trips through psycopg's conninfo
# parser to also handle key=value DSNs, which this one-line sed does not
# attempt; not needed here since nothing in this pack ever writes
# KP2_JOIN_DB_URL in that form.
MASKED_DSN=$(printf '%s' "$KP2_JOIN_DB_URL" | sed -E 's#(://[^:/@]+:)[^@]*(@)#\1***\2#')

log "importing $DUMP_FILE into: $MASKED_DSN"
log "this OVERWRITES whatever is already in that database -- run only against an empty cluster (plan §6.3's provision-time import)"

docker compose -f "$PACK_DIR/docker-compose.yml" run --rm -T \
  -v "$DUMP_FILE:/tmp/kp2-join-import.dump:ro" \
  join-api pg_restore -d "$KP2_JOIN_DB_URL" /tmp/kp2-join-import.dump

log "import complete: $DUMP_FILE -> $MASKED_DSN"
