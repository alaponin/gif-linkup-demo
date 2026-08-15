# One droplet, one firewall, filed under a pre-existing DO project. The
# pack's own deployment.yaml stays target: docker-local — the droplet is
# just a remote docker-local host reached over SSH, which is what keeps
# this the simplest possible DigitalOcean shape (no DOKS, no App
# Platform, no pack changes).

# The project (ITU-KP) is created and owned in the DO control panel, NOT
# by Terraform: a data source looks it up, and project_resources files
# the droplet into it. Consequences worth knowing — `terraform destroy`
# unfiles the droplet and leaves the project standing (which is what you
# want for a project holding other work), and Terraform never touches the
# project's name, purpose or environment.
#
# Only project-assignable types can be listed in `resources`: droplets,
# volumes, Spaces buckets, load balancers, domains, database clusters,
# Kubernetes clusters and apps. SSH keys and firewalls are account-level
# objects with no project at all — they are not omitted here by choice.
data "digitalocean_project" "kp2" {
  name = var.do_project_name
}

resource "digitalocean_project_resources" "kp2" {
  project   = data.digitalocean_project.kp2.id
  resources = [digitalocean_droplet.kp2.urn]
}

resource "digitalocean_ssh_key" "deploy" {
  name       = "${var.name_prefix}-deploy"
  public_key = var.ssh_public_key
}

resource "digitalocean_droplet" "kp2" {
  name       = "${var.name_prefix}-host"
  region     = var.region
  size       = var.droplet_size
  image      = "ubuntu-24-04-x64"
  ssh_keys   = [digitalocean_ssh_key.deploy.fingerprint]
  monitoring = true
  user_data  = file("${path.module}/cloud-init.yaml")
  tags       = ["kp2", "demo", "ephemeral"]
}

# SSH in, nothing else in. Ubuntu's systemd-timesyncd keeps NTP happy
# (docs/deployment-targets.md, "Require NTP"); outbound stays open for
# apt, Docker Hub and ghcr.io image pulls.
resource "digitalocean_firewall" "kp2" {
  name        = "${var.name_prefix}-ssh-only"
  droplet_ids = [digitalocean_droplet.kp2.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.admin_cidrs
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}
