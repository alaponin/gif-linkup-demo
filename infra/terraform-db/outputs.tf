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
