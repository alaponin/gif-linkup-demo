variable "do_token" {
  description = "DigitalOcean API token (write scope). In CI: TF_VAR_do_token from the DO_TOKEN secret."
  type        = string
  sensitive   = true
}

variable "project_name" {
  description = "Name of the dedicated DigitalOcean project everything is filed under."
  type        = string
  default     = "kp2-linkup"
}

variable "region" {
  description = "DigitalOcean region. fra1/ams3 are the close ones from Tallinn."
  type        = string
  default     = "fra1"
}

variable "droplet_size" {
  description = <<-EOT
    Droplet size. The federation needs ~11 GiB RAM in steady state
    (runbook.md Prerequisites), so 16 GB is the floor: s-8vcpu-16gb
    ($96/mo, $0.143/hr). If deploys hit the 10-minute healthcheck budget
    (docs/deployment-targets.md), go bigger rather than raising retries.
  EOT
  type        = string
  default     = "s-8vcpu-16gb"
}

variable "ssh_public_key" {
  description = "Public half of the deploy key CI uses to SSH in. In CI: TF_VAR_ssh_public_key."
  type        = string
}

variable "admin_cidrs" {
  description = <<-EOT
    CIDRs allowed to reach SSH (port 22) — the ONLY inbound port. The pack
    binds every service to 127.0.0.1 on the droplet and its own lib-stack.sh
    refuses a non-loopback bind while the Test CA is in the stack, so all
    access (console :8090, CS UI :4000, ...) goes through an SSH tunnel.
    GitHub-hosted runners have no fixed IPs, so CI needs 0.0.0.0/0 here
    unless you use a self-hosted runner or tighten to GitHub's published
    ranges. SSH remains key-only either way.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}
