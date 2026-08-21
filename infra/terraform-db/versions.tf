# KP2 build pack — managed Postgres cluster, a SEPARATE root module/state
# from ../terraform/ (the droplet module). Deliberately separate: plan
# §6.3 requires that a `terraform destroy` of the ephemeral stack can
# never take the persistent evidence store with it, and physical
# separation (own directory, own state file) is the primary mechanism —
# `lifecycle { prevent_destroy = true }` on the cluster resource in
# main.tf is the backstop, not a substitute.
#
# State lives in the same DigitalOcean Spaces bucket as the droplet
# module, under a different key (see backend.hcl) so the two states never
# collide:
#   terraform init -backend-config=backend.hcl
# Credentials for the backend come from the environment:
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY  (a Spaces access key pair)

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    digitalocean = {
      source = "digitalocean/digitalocean"
      # Same constraint as ../terraform/versions.tf (pinned to 2.99.1 in
      # that module's .terraform.lock.hcl at the time this was written).
      version = "~> 2.40"
    }
  }

  backend "s3" {}
}
