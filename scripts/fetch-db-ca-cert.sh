#!/usr/bin/env bash
# scripts/fetch-db-ca-cert.sh -- downloads the managed Postgres cluster's
# CA certificate and writes it to the path KP2_DB_CA_CERT points at
# (.env.example). Run after `terraform apply` in infra/terraform-db/, or
# after any droplet re-provision: the cert is stable per cluster but
# lives on the droplet's disk, so plan §6.2 has the provision step
# re-download it rather than assume it survived. This script is only the
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
( cd "$PACK_DIR/infra/terraform-db" && terraform output -raw ca_certificate ) > "$KP2_DB_CA_CERT"

# Public cert, not a secret -- readable is fine, unlike .env's 600.
chmod 644 "$KP2_DB_CA_CERT"

log "wrote CA certificate to $KP2_DB_CA_CERT"
