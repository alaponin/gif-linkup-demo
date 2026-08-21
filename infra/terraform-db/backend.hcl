# Partial backend config for DigitalOcean Spaces (S3-compatible).
# Not secret — the access key pair comes from the environment
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY), never from this file.
#
# Same bucket as ../terraform/backend.hcl, but a DIFFERENT key: this
# module's state must never collide with the droplet module's state
# (plan §6.3 — the two are separate root modules on purpose, so one
# `terraform destroy` can never take both the droplet and the cluster).

bucket = "kp2-terraform-state"
key    = "kp2-linkup/db-terraform.tfstate"

# The s3 backend insists on an AWS region name; Spaces ignores it.
region = "us-east-1"

endpoints = {
  s3 = "https://fra1.digitaloceanspaces.com"
}

# Spaces is not AWS — skip every AWS-specific validation.
skip_credentials_validation = true
skip_requesting_account_id  = true
skip_metadata_api_check     = true
skip_region_validation      = true
skip_s3_checksum            = true
