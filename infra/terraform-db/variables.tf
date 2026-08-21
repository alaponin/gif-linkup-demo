variable "do_token" {
  description = "DigitalOcean API token (write scope). In CI: TF_VAR_do_token from the DO_TOKEN secret."
  type        = string
  sensitive   = true
}

variable "region" {
  description = <<-EOT
    DigitalOcean region for the cluster. MUST match whatever region the
    droplet module (../terraform/variables.tf's own `region` variable)
    was actually applied with -- there is no automatic coupling between
    the two modules' variable values, Terraform will happily let this
    drift from the droplet's region, and a mismatch means the cluster's
    default-VPC placement (see main.tf) is NOT the same network the
    droplet sits in. This is an operator discipline note, not something
    either module enforces for you.
  EOT
  type        = string
  default     = "fra1"
}

variable "cluster_size" {
  description = <<-EOT
    Managed Postgres cluster tier slug. db-s-1vcpu-1gb is currently
    DigitalOcean's smallest single-node Postgres tier, roughly the
    plan's own named ~$15/month figure (docs/plans/join-datastore-
    postgres-digitalocean-plan.md §3, §6.2) -- verify current pricing/
    slug at commit time, DO renames and reprices these tiers.
  EOT
  type        = string
  default     = "db-s-1vcpu-1gb"
}

variable "node_count" {
  description = <<-EOT
    Cluster node count. 1 = single-node (demo-honest per the plan's §5
    Availability row). The +1 standby upgrade is a console/API action at
    a later date, not a variable this module exposes -- that decision
    isn't being made now, so there's no knob for it yet.
  EOT
  type        = number
  default     = 1
}

variable "droplet_id" {
  description = <<-EOT
    ID of the droplet the cluster's firewall should trust (see
    digitalocean_database_firewall.kp2_join in main.tf). Get it from the
    droplet module:
      terraform output -raw droplet_id   (run from ../terraform/)
    A plain variable, not `terraform_remote_state`, is deliberate: it
    keeps this module's state and config from referencing the droplet
    module's state file at all, so destroying the droplet module can
    never touch this module's state by construction. No default --
    every apply must supply the current droplet's id explicitly.
  EOT
  type        = string
}
