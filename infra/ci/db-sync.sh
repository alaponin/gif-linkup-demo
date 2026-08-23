#!/usr/bin/env bash
# infra/ci/db-sync.sh -- runs ON THE CI RUNNER (or a laptop holding the same
# credentials), between the workflow's rsync step and remote-deploy.sh.
# No-op unless deployment.yaml says datastore.kind: postgres, so sqlite
# deployments never pay for it.
#
# What it automates -- the whole manual chain runbook.md's "The Postgres
# join store" section used to require, including all three of §6.2's
# droplet-re-create steps:
#   1. `terraform apply` on infra/terraform-db with THIS droplet's id, so
#      the cluster firewall's trusted source follows every droplet
#      re-create (the step nothing used to watch -- miss it and join-api
#      just loses connectivity). The first run creates the cluster itself;
#      budget ~5-10 min of DO provisioning for that one.
#   2. Ships the cluster CA cert to the droplet (fetch-db-ca-cert.sh's job
#      on a laptop -- that script needs terraform + state credentials the
#      droplet deliberately does not have, so the runner fetches and pushes).
#   3. Writes KP2_JOIN_DB_URL / KP2_JOIN_DB_URL_RO / KP2_DB_CA_CERT into
#      the droplet's .env, then bootstraps the schema AS ADMIN
#      (`python -m store init`) so joinapi never becomes table owner --
#      the exact order runbook.md mandates, encoded instead of remembered.
#
# Secrets posture -- a deliberate, recorded change from "secrets never
# leave the droplet" (decision 2026-08-23): the DSNs transit this runner's
# memory and the SSH stream, never argv, never a log line (masked below
# under GitHub Actions). CI already holds DO_TOKEN, which can read the
# same DO-issued passwords from the API at will -- no new trust is
# granted, only a new path for material CI could already obtain.
#
# MUST run BEFORE remote-deploy.sh, not after: on a postgres deployment,
# acceptance.sh's module 2.7 reads the join store via
# `docker compose run join-api python -m store dump-records` and fails
# hard if the store is unreachable -- so the store has to work before the
# deploy step ever reaches acceptance.
#
# Needs in env: IP (droplet address), PACK_DIR (pack path inside the
# checkout), plus the workflow's existing credentials -- AWS_ACCESS_KEY_ID/
# AWS_SECRET_ACCESS_KEY (Spaces, terraform state) and TF_VAR_do_token.
# No new GitHub secrets.
set -euo pipefail
: "${IP:?droplet ip}"
: "${PACK_DIR:?pack path inside the checkout}"

TF_DROPLET="$PACK_DIR/infra/terraform"
TF_DB="$PACK_DIR/infra/terraform-db"

# Gate on deployment.yaml's datastore.kind -- the same key store.py reads
# (_resolve_backend), parsed the same way, not grepped: the file's comments
# mention "postgres" in prose several times.
kind=$(python3 - "$PACK_DIR/deployment.yaml" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1])) or {}
print((doc.get("datastore") or {}).get("kind", "sqlite"))
PY
)

if [ "$kind" != "postgres" ]; then
  echo "db-sync: datastore.kind=$kind -- nothing to do"
  exit 0
fi

echo "== db-sync: datastore.kind=postgres -- applying infra/terraform-db =="
droplet_id=$(terraform -chdir="$TF_DROPLET" output -raw droplet_id)
terraform -chdir="$TF_DB" init -input=false -backend-config=backend.hcl
# First run creates the cluster; every later run re-asserts the firewall
# rule against the CURRENT droplet id (§6.2 step 2). The rule is keyed on
# the droplet id, so a new id replaces the old rule rather than stacking.
terraform -chdir="$TF_DB" apply -auto-approve -input=false -var "droplet_id=$droplet_id"

ca=$(terraform -chdir="$TF_DB" output -raw ca_certificate)
dsn=$(terraform -chdir="$TF_DB" output -raw kp2_join_dsn_template)
dsn_ro=$(terraform -chdir="$TF_DB" output -raw joinapi_ro_dsn_template)
admin_dsn=$(terraform -chdir="$TF_DB" output -raw db_admin_dsn_template)

# Under GitHub Actions, ensure no later step can echo these readably.
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  echo "::add-mask::$dsn"
  echo "::add-mask::$dsn_ro"
  echo "::add-mask::$admin_dsn"
fi

# Prepend the payload as base64 env assignments to the remote script and
# pipe the whole thing over SSH stdin. base64 because the CA cert is
# multi-line PEM and the DSNs carry '&'; stdin because nothing may land on
# either machine's argv (`ps auxww` shows argv to any local user -- the
# same reason the export/import scripts pass DSNs via environment).
# `base64 | tr -d '\n'`, not `-w0`: macOS base64 has no -w, and this
# script is documented as laptop-runnable.
b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
{
  printf 'KP2_SYNC_CA_B64=%s\n'    "$(b64 "$ca")"
  printf 'KP2_SYNC_DSN_B64=%s\n'   "$(b64 "$dsn")"
  printf 'KP2_SYNC_RO_B64=%s\n'    "$(b64 "$dsn_ro")"
  printf 'KP2_SYNC_ADMIN_B64=%s\n' "$(b64 "$admin_dsn")"
  cat "$PACK_DIR/infra/ci/db-sync-remote.sh"
} | ssh -o ServerAliveInterval=30 "root@$IP" bash -s

echo "== db-sync: join store ready =="
