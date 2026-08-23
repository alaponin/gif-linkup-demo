#!/usr/bin/env bash
# infra/ci/db-sync-remote.sh -- runs ON THE DROPLET, piped over SSH by
# db-sync.sh, which prepends four KP2_SYNC_*_B64 variable assignments to
# the stream (base64 so multi-line PEM and &-laden DSNs survive the trip;
# stdin so nothing ever appears on argv). Not meant to be run by hand --
# without those variables it refuses immediately.
#
# Idempotent by construction, safe on every `up` and `deploy`: the CA cert
# write overwrites the same bytes, the .env upsert replaces the same three
# keys with the same values (everything else in .env untouched), and
# `python -m store init` applies only migrations not yet recorded in
# schema_version -- inside pg_advisory_xact_lock -- then re-runs
# grants.sql, which is written to be re-runnable.
set -euo pipefail
: "${KP2_SYNC_CA_B64:?}" "${KP2_SYNC_DSN_B64:?}" "${KP2_SYNC_RO_B64:?}" "${KP2_SYNC_ADMIN_B64:?}"

PACK="/opt/kp2/repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack"
CA_PATH="/opt/kp2/do-db-ca.crt"
cd "$PACK"

# The two `docker compose run --rm join-api` calls below are the one place in
# this pack that reaches that image WITHOUT going through scripts/lib-stack.sh,
# so nothing else resolves the containers' uid for them and
# docker-compose.yml's `${KP2_HOST_UID:-0}` default would make them UID 0 --
# the posture docs/security-review-2026-08-23.md's finding H1 is about. Set
# directly here, not via KP2_CONTAINER_UID, because KP2_CONTAINER_UID is a
# lib-stack.sh input and lib-stack.sh is not in this path.
#
# 10001 literally, and it is fine that this runs BEFORE remote-deploy.sh has
# created the `kp2` account: both calls are throwaway `run --rm` invocations
# that only talk to Postgres (store.init's postgres branch returns before it
# touches the filesystem; dump-records only reads), so an id with no host
# account behind it yet has nothing it needs to write.
export KP2_HOST_UID=10001
export KP2_HOST_GID=10001

# One scratch dir, one trap. The .env this builds carries both DSNs, so an
# abort between writing it and `install`-ing it must not leave it behind.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# A fresh droplet has no .env yet (this step runs BEFORE remote-deploy.sh
# -- see db-sync.sh's header for why). Generate it here; remote-deploy.sh's
# own gen-secrets call then finds it complete and appends nothing. Needed
# now because docker-compose.yml interpolates the WHOLE file (`:?` on the
# X-Road credentials) for any `docker compose run`, including the
# bootstrap below.
[ -f .env ] || scripts/gen-secrets.sh

echo "== db-sync-remote: CA cert -> $CA_PATH =="
tmp="$TMP/ca"
printf '%s' "$KP2_SYNC_CA_B64" | base64 -d > "$tmp"
grep -q 'BEGIN CERTIFICATE' "$tmp" || { echo "decoded CA is not a PEM certificate" >&2; exit 1; }
# Public cert, not a secret -- 644, same as fetch-db-ca-cert.sh.
install -m 644 "$tmp" "$CA_PATH"

dsn=$(printf '%s' "$KP2_SYNC_DSN_B64" | base64 -d)
dsn_ro=$(printf '%s' "$KP2_SYNC_RO_B64" | base64 -d)
admin_dsn=$(printf '%s' "$KP2_SYNC_ADMIN_B64" | base64 -d)

# Upsert one KEY='value' line into .env, single-quoted: .env is
# shell-sourced and a DSN's '&' backgrounds the rest of an unquoted
# assignment, silently truncating it (.env.example's own warning).
upsert() { # upsert KEY VALUE
  local key="$1" val="$2" tmp
  # Refuse rather than escape. The shell's quote-escaping idiom does NOT
  # round-trip through docker compose's .env reader -- it rejects the WHOLE
  # file ("unexpected character"), so every later `docker compose` on the
  # droplet breaks, not just this key. DO-issued passwords are alphanumeric,
  # so this is a tripwire, not a path anything is expected to take.
  case $val in *\'*) echo "$key: value contains a single quote, which .env cannot carry" >&2; exit 1;; esac
  tmp="$TMP/env"
  { grep -v "^${key}=" .env || true; printf "%s='%s'\n" "$key" "$val"; } > "$tmp"
  install -m 600 "$tmp" .env
}
echo "== db-sync-remote: KP2_JOIN_DB_URL / _RO / KP2_DB_CA_CERT -> .env (mode 600) =="
upsert KP2_DB_CA_CERT     "$CA_PATH"
upsert KP2_JOIN_DB_URL    "$dsn"
upsert KP2_JOIN_DB_URL_RO "$dsn_ro"

# Schema bootstrap AS ADMIN -- runbook.md's mandated order, so joinapi
# never becomes table owner (ownership would silently bypass every GRANT
# in grants.sql; store.py refuses to start in that state, loudly). The
# admin DSN lives in this process's environment for the one call and is
# never written to .env -- same one-off discipline as
# KP2_JOIN_DB_ADMIN_URL in join-store-import.sh. --build because on a
# fresh droplet this runs before anything else has built the join-api
# image. KP2_DB_CA_CERT is already in .env above, so compose mounts the
# real cert (not the /dev/null default) and sslmode=verify-full holds.
# --no-deps on both calls: this runs before the deploy step, and store
# operations need only the database -- nothing else in the stack should
# come up as a side effect this early.
# </dev/null is load-bearing, not decoration: db-sync.sh pipes this script
# into `ssh ... bash -s`, so bash is reading its own source from stdin and
# has NOT read ahead. `docker compose run` defaults to --interactive (-T only
# drops the TTY), so without this it drains the REST OF THIS FILE into the
# container -- the read-back below never runs and the script still exits 0.
# Verified locally: the line after an un-redirected `compose run` vanishes.
# Same class as the stdin conflict scripts/member.sh:159 documents.
echo "== db-sync-remote: schema bootstrap as admin (python -m store init) =="
KP2_JOIN_DB_URL="$admin_dsn" docker compose run --build --no-deps --rm -T -e KP2_JOIN_DB_URL join-api python -m store init </dev/null

# Read-back as the app's own role, from .env -- proves the joinapi DSN,
# the CA path, verify-full and the firewall rule end to end, and is
# exactly the call acceptance.sh's 2.7 will make later in the pipeline.
# Count only: records are demo data, but logs don't need them.
echo "== db-sync-remote: read-back as joinapi =="
n=$(docker compose run --no-deps --rm -T join-api python -m store dump-records </dev/null | wc -l)
echo "== db-sync-remote: store reachable, ${n} record(s) =="
