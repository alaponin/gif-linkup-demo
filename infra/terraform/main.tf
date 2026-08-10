# One droplet, one firewall, one project. The pack's own deployment.yaml
# stays target: docker-local — the droplet is just a remote docker-local
# host reached over SSH, which is what keeps this the simplest possible
# DigitalOcean shape (no DOKS, no App Platform, no pack changes).

resource "digitalocean_project" "kp2" {
  name        = var.project_name
  description = "KP2 Linkup demonstration federation (X-Road 7.7.0). Demo only — never production."
  purpose     = "Service or API"
  environment = "Development"
  resources   = [digitalocean_droplet.kp2.urn]
}

resource "digitalocean_ssh_key" "deploy" {
  name       = "${var.project_name}-deploy"
  public_key = var.ssh_public_key
}

resource "digitalocean_droplet" "kp2" {
  name       = "${var.project_name}-host"
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
  name        = "${var.project_name}-ssh-only"
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
