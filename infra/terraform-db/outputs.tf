# Deliberately the cluster's `private_host` attribute, never `host` (the
# public endpoint). The plan is explicit the DSN must use the private
# hostname -- the firewall in main.tf only trusts the droplet, so the
# public endpoint is unreachable from anywhere else anyway, but using the
# private hostname is what keeps the traffic inside the VPC instead of
# routing out to the public internet and back.
output "db_private_host" {
  description = "VPC-private hostname of the cluster. Use this in KP2_JOIN_DB_URL, never db_host's public counterpart."
  value       = digitalocean_database_cluster.kp2_join.private_host
}

output "db_port" {
  description = "Cluster connection port (same for public and private endpoints)."
  value       = digitalocean_database_cluster.kp2_join.port
}

output "joinapi_password" {
  description = "DO-generated password for the joinapi role."
  value       = digitalocean_database_user.joinapi.password
  sensitive   = true
}

output "joinapi_ro_password" {
  description = "DO-generated password for the joinapi_ro role."
  value       = digitalocean_database_user.joinapi_ro.password
  sensitive   = true
}

# Not sensitive on purpose -- the plan says so explicitly: it's a public
# cert, not a secret. Don't mark it sensitive, don't redact it in output.
output "ca_certificate" {
  description = "Cluster CA certificate (PEM). Public -- not a secret. scripts/fetch-db-ca-cert.sh writes this to disk for sslrootcert."
  value       = data.digitalocean_database_ca.kp2_join.certificate
}

# Convenience output: goes straight from `terraform output` to a working
# KP2_JOIN_DB_URL line, matching .env.example's documented shape and
# docker-compose.yml's do-db-ca.crt mount target (Task 4), rather than
# an operator hand-assembling the DSN from the four outputs above.
output "kp2_join_dsn_template" {
  description = "Full KP2_JOIN_DB_URL value, password interpolated. Sensitive -- contains the password."
  value       = "postgresql://joinapi:${digitalocean_database_user.joinapi.password}@${digitalocean_database_cluster.kp2_join.private_host}:${digitalocean_database_cluster.kp2_join.port}/kp2_join?sslmode=verify-full&sslrootcert=/pack-secrets/do-db-ca.crt"
  sensitive   = true
}

# DO creates a default admin/superuser account for every managed cluster
# automatically -- no new resource needed, the cluster resource already
# computes these two attributes. Needed because pg_restore's dump-restore
# runs ALTER TABLE ... OWNER TO <original-owner> and setval(...) on the
# sequence, neither of which the restricted joinapi role has (or should
# have) privileges for -- see scripts/join-store-import.sh's
# KP2_JOIN_DB_ADMIN_URL.
output "db_admin_user" {
  description = "DO's default admin/superuser role name for the cluster. Needed for pg_restore (ALTER TABLE ... OWNER TO, sequence setval) -- joinapi lacks those privileges by design."
  value       = digitalocean_database_cluster.kp2_join.user
}

output "db_admin_password" {
  description = "DO-generated password for the admin/superuser role."
  value       = digitalocean_database_cluster.kp2_join.password
  sensitive   = true
}

# Convenience output mirroring kp2_join_dsn_template above, but for the
# admin role against the kp2_join database -- this is what
# KP2_JOIN_DB_ADMIN_URL should be set to for scripts/join-store-import.sh.
output "db_admin_dsn_template" {
  description = "Full KP2_JOIN_DB_ADMIN_URL value (admin role), password interpolated. Sensitive -- contains the password."
  value       = "postgresql://${digitalocean_database_cluster.kp2_join.user}:${digitalocean_database_cluster.kp2_join.password}@${digitalocean_database_cluster.kp2_join.private_host}:${digitalocean_database_cluster.kp2_join.port}/kp2_join?sslmode=verify-full&sslrootcert=/pack-secrets/do-db-ca.crt"
  sensitive   = true
}
