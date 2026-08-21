output "droplet_ip" {
  description = "Public IPv4 of the federation host. SSH-tunnel to reach anything on it."
  value       = digitalocean_droplet.kp2.ipv4_address
}

output "tunnel_hint" {
  description = "Copy-paste tunnel for the console, CS UI and join API."
  value       = "ssh -L 8090:127.0.0.1:8090 -L 4000:127.0.0.1:4000 -L 8091:127.0.0.1:8091 root@${digitalocean_droplet.kp2.ipv4_address}"
}

output "droplet_id" {
  description = "Droplet resource id. Feeds infra/terraform-db's var.droplet_id (its database firewall trusts this id, not an IP)."
  value       = digitalocean_droplet.kp2.id
}
