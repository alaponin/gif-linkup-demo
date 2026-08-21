# The persistent evidence-layer store (plan §6): a managed Postgres
# cluster, deliberately its own root module/state -- see versions.tf and
# backend.hcl for why. This module owns the cluster, its database, its
# two roles, and its trusted-sources firewall. It does NOT run any SQL
# against the cluster (grants live in apps/join-api/migrations/grants.sql,
# already applied by store.init() at process startup -- see Task 1); this
# module's job stops at "the roles exist."

provider "digitalocean" {
  token = var.do_token
}

# engine/version: check DO's console for the current offering at commit
# time (plan's own "verify at commit time" honesty rule, same as
# cluster_size above) -- "pg" / "16" are what's current as of this
# writing.
#
# No private_network_uuid: omitting it places the cluster in the
# account's default VPC for var.region, deliberately matching the
# droplet's own placement (../terraform/main.tf's digitalocean_droplet
# also sets no vpc_uuid, so it too sits in that region's default VPC).
# Both resources landing in the same default VPC is what makes the
# cluster reachable over its PRIVATE hostname from the droplet at all --
# not an oversight, the intended shape.
resource "digitalocean_database_cluster" "kp2_join" {
  name       = "kp2-join"
  engine     = "pg"
  version    = "16"
  region     = var.region
  size       = var.cluster_size
  node_count = var.node_count

  # Plan §6.3: cluster destruction is gated structurally as well as
  # procedurally (scripts/join-store-export.sh is the procedural half).
  # A `terraform destroy` run against ../terraform/ (the droplet module)
  # cannot touch this resource at all -- it lives in a different state.
  # This is the backstop for accidental `terraform destroy` run against
  # THIS module's own state.
  lifecycle {
    prevent_destroy = true
  }
}

# Deliberately only "kp2_join" -- NOT "kp2_metrics". The controller's
# sign-off ruling deferred kp2_metrics until xroad-metrics actually
# lands; creating it now-empty was explicitly not chosen (plan §1, §9).
resource "digitalocean_database_db" "kp2_join" {
  cluster_id = digitalocean_database_cluster.kp2_join.id
  name       = "kp2_join"
}

# Roles only -- DO generates and rotates these roles' passwords itself.
# GRANTs are apps/join-api/migrations/grants.sql's job (Task 1), applied
# by store.init(). Terraform running SQL against the cluster would
# duplicate what store.py already does correctly.
resource "digitalocean_database_user" "joinapi" {
  cluster_id = digitalocean_database_cluster.kp2_join.id
  name       = "joinapi"
}

resource "digitalocean_database_user" "joinapi_ro" {
  cluster_id = digitalocean_database_cluster.kp2_join.id
  name       = "joinapi_ro"
}

# The concrete mechanism behind "public access disabled, trusted-sources
# list holds exactly the droplet" (plan §5 Network row): DO's managed
# databases have both a public and private endpoint by default, and a
# firewall trusting only the droplet's resource id is what makes the
# public endpoint practically unreachable -- nothing else is trusted, no
# 0.0.0.0/0-style rule anywhere here.
resource "digitalocean_database_firewall" "kp2_join" {
  cluster_id = digitalocean_database_cluster.kp2_join.id

  rule {
    type  = "droplet"
    value = var.droplet_id
  }
}

# The cluster's CA cert. This is a data source, not a cluster-resource
# attribute (confirmed via `terraform providers schema -json` against
# the digitalocean provider 2.99.1 schema -- digitalocean_database_ca has
# no counterpart attribute on digitalocean_database_cluster). Feeds
# outputs.tf's ca_certificate output, which scripts/fetch-db-ca-cert.sh
# downloads to disk.
data "digitalocean_database_ca" "kp2_join" {
  cluster_id = digitalocean_database_cluster.kp2_join.id
}
