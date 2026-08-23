#!/usr/bin/env bash
# scripts/fetch-db-ca-cert.sh -- downloads the managed Postgres cluster's
# CA certificate and writes it to the path KP2_DB_CA_CERT points at
# (.env.example). Run after `terraform apply` in infra/terraform-db/, or
# after any droplet re-provision: the cert is stable per cluster but
# lives on the droplet's disk, so plan §6.2 has the provision step
# re-download it rather than assume it survived.
#
# LAPTOP-ONLY, and the CI path does not need it: this script shells out
# to `terraform output` against infra/terraform-db's state, which needs
# terraform plus the Spaces backend credentials -- neither of which the
# droplet has (cloud-init deliberately installs no terraform). On the
# workflow path, infra/ci/db-sync.sh reads the same output on the runner
# and pushes the cert to the droplet over SSH instead. This script is only the
# CA-cert leg of that section's three-things-must-survive checklist --
# KP2_JOIN_DB_URL in .env is an operator's manual edit (plan §4's "where
# secrets rest" decision), and re-adding the new droplet to the cluster's
# trusted sources is `terraform apply` itself, once infra/terraform-db's
# var.droplet_id is updated to the new droplet's id.
set -euo pipefail
. "$(dirname "$0")/lib-core.sh"

if [ -z "${KP2_DB_CA_CERT:-}" ] && [ -f "$PACK_DIR/.env" ]; then
  set -a; . "$PACK_DIR/.env"; set +a
fi
[ -n "${KP2_DB_CA_CERT:-}" ] || fail "KP2_DB_CA_CERT is unset in the environment and $PACK_DIR/.env -- this is the host path to write the CA cert to (see .env.example)."

log "fetching ca_certificate from infra/terraform-db's Terraform state"
# Write to a temp file and verify before touching the real path -- writing
# straight to $KP2_DB_CA_CERT would truncate it before terraform even
# runs, so any failure (uninitialized dir, missing backend credentials,
# nothing applied yet) leaves a zero-byte cert where a good one used to
# be, and the next join-api restart fails with a confusing TLS handshake
# error instead of a clear one from this script.
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
( cd "$PACK_DIR/infra/terraform-db" && terraform output -raw ca_certificate ) > "$tmp"
grep -q 'BEGIN CERTIFICATE' "$tmp" || fail "terraform output -raw ca_certificate did not return a PEM certificate"
mv "$tmp" "$KP2_DB_CA_CERT"

# Public cert, not a secret -- readable is fine, unlike .env's 600.
chmod 644 "$KP2_DB_CA_CERT"

log "wrote CA certificate to $KP2_DB_CA_CERT"
