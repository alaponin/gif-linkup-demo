# KP2 build pack — DigitalOcean infra.
# State lives in a DigitalOcean Spaces bucket (S3-compatible); the partial
# backend config is supplied at init time from backend.hcl:
#   terraform init -backend-config=backend.hcl
# Credentials for the backend come from the environment:
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY  (a Spaces access key pair)

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.40"
    }
  }

  backend "s3" {}
}

provider "digitalocean" {
  token = var.do_token
}
