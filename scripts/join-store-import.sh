#!/usr/bin/env bash
# scripts/join-store-import.sh -- the counterpart to join-store-export.sh:
# pg_restore a previously-exported dump (pg_dump -Fc format) into a
# Postgres cluster, meant to run at provision time against an empty
# cluster (plan §6.3). Destructive to whatever database it targets --
# this is meant to be run non-interactively by provisioning tooling, so
# it does NOT prompt for confirmation, but it DOES print the target
# (host/dbname, password redacted) before touching anything, so that's
# visible in whatever log captured the run.
#
# Two DSNs, one required and one optional: KP2_JOIN_DB_URL is always
# required (it's how this script confirms there IS a Postgres deployment
# to import into at all), but pg_restore of a custom-format dump runs
# `ALTER TABLE ... OWNER TO <original-owner>` and `setval(...)` on
# request_events_seq_seq -- both need privileges the restricted joinapi
# role deliberately doesn't have (joinapi has no UPDATE on the sequence,
# by design -- see apps/join-api/migrations/grants.sql). So: if
# KP2_JOIN_DB_ADMIN_URL is set, pg_restore runs against THAT DSN instead
# (infra/terraform-db's db_admin_dsn_template output is the source for
# it); if unset, this falls back to KP2_JOIN_DB_URL exactly as before,
# for a simpler setup where ownership isn't an issue.
#
# Same refusal checks and lib-core.sh-only sourcing as
# scripts/join-store-export.sh -- see that script's header comment for why.
set -euo pipefail
. "$(dirname "$0")/lib-core.sh"

# Same default as join-store-export.sh's $KP2_EXPORT_DIR -- not used to
# locate $1 below (this script always takes an explicit path), just so the
# usage message below reflects this shell's actual value: out/join-migrated/
# unless the operator exported KP2_EXPORT_DIR=/opt/kp2/exports first, which
# runbook.md's §6.3 has them do on the droplet (infra/ci/remote-deploy.sh's
# own export of the same variable is for its CI-driven process only and
# never reaches this interactive shell).
: "${KP2_EXPORT_DIR:=$PACK_DIR/out/join-migrated}"

DUMP_FILE=${1:?"Usage: scripts/join-store-import.sh <dump-file>  (join-store-export.sh writes dumps under \$KP2_EXPORT_DIR, currently $KP2_EXPORT_DIR)"}
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
    fail "KP2_JOIN_DB_URL is unset (or still the .env.example placeholder) in the environment and $PACK_DIR/.env -- this confirms there is a Postgres deployment to import into, regardless of which DSN pg_restore actually connects with (see KP2_JOIN_DB_ADMIN_URL above)." ;;
esac

# KP2_JOIN_DB_ADMIN_URL is optional. Nothing in this pack ever WRITES it
# into .env -- an admin DSN is deliberately not something
# scripts/gen-secrets.sh or .env.example manages, it's meant to be
# exported by the operator (or provisioning tooling) for just this run,
# straight from infra/terraform-db's db_admin_dsn_template output. (If it
# happens to already be present in .env, the sourcing above picks it up
# like any other .env variable -- there's just no mechanism that puts it
# there.) Falls back to KP2_JOIN_DB_URL when unset, so a deployment where
# ownership isn't an issue needs nothing extra.
effective_dsn="${KP2_JOIN_DB_ADMIN_URL:-$KP2_JOIN_DB_URL}"

# Password-redacted DSN for the log line only -- the real one is never
# echoed. Strips whatever sits between the first `:` after `://user` and
# the following `@`, which is the only DSN shape this pack's KP2_JOIN_DB_URL
# (and KP2_JOIN_DB_ADMIN_URL, same shape) is documented in (.env.example's
# postgresql://user:PASSWORD@host:port/db form) -- store.py's own
# _mask_dsn round-trips through psycopg's conninfo parser to also handle
# key=value DSNs, which this one-line sed does not attempt; not needed
# here since nothing in this pack ever writes either DSN in that form.
MASKED_DSN=$(printf '%s' "$effective_dsn" | sed -E 's#(://[^:/@]+:)[^@]*(@)#\1***\2#')

log "importing $DUMP_FILE into: $MASKED_DSN"
log "this OVERWRITES whatever is already in that database -- run only against an empty cluster (plan §6.3's provision-time import)"

# $effective_dsn must not land in this host process's argv (visible via
# `ps auxww` for the run's duration, which would defeat the masking
# above) -- same reason as join-store-export.sh's pg_dump call. `-e
# KP2_JOIN_DB_URL="$effective_dsn"` (inline value) would do exactly that;
# `-e KP2_JOIN_DB_URL` (bare key, no `=value`) instead tells `docker
# compose run` to resolve the value from THIS SCRIPT's own environment,
# so it has to be exported into that environment first. Once exported,
# the DSN travels host-env -> container-env, never through argv, and
# overrides docker-compose.yml's static environment block for this one
# run only -- no edit to that file, no second variable name for the
# container-side command to know about.
export KP2_JOIN_DB_URL="$effective_dsn"
docker compose -f "$PACK_DIR/docker-compose.yml" run --rm -T \
  -e KP2_JOIN_DB_URL \
  -v "$DUMP_FILE:/tmp/kp2-join-import.dump:ro" \
  join-api sh -c 'exec pg_restore -d "$KP2_JOIN_DB_URL" /tmp/kp2-join-import.dump'

log "import complete: $DUMP_FILE -> $MASKED_DSN"
