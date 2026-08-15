# Partial backend config for DigitalOcean Spaces (S3-compatible).
# Not secret — the access key pair comes from the environment
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY), never from this file.
# The bucket has to exist before the first `terraform init`; this one was
# created by hand in the control panel (fra1). Bucket names are global, so
# a fork of this pack needs its own name here.

bucket = "kp2-terraform-state"
key    = "kp2-linkup/terraform.tfstate"

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
