# Exposing the console publicly — implementation plan, security analysis, over-engineering review

**Decision being implemented:** the KP2 demonstration console (`apps/console`, droplet port 8090)
becomes reachable from the public internet for a **known audience** behind HTTPS + basic auth.
Everything else — CS UI (:4000), the four SS admin UIs, join-api (:8091), the Test CA — stays
loopback-bound and tunnel-only, exactly as today. DigitalOcean-only resources; no external
services in the serving path except Let's Encrypt as the CA. No domain name: Let's Encrypt
issues certificates for bare IP addresses since Jan 2026 (GA), supported by certbot ≥ 5.4
(Mar 2026) via the mandatory `shortlived` profile (160 h validity — which fits a droplet that
lives for a 4-hour demo session).

**Load-bearing property, stated up front:** nothing in the pack changes. `deployment.yaml`
keeps `network.bind: 127.0.0.1`, `docker-compose.yml` is untouched, and `lib-stack.sh`'s
public-exposure refusal never fires, because the exposure is a *host-level* nginx proxy in
front of one loopback port — not a bind change. The entire delta lives in `infra/` plus one
workflow step and one GitHub secret.

---

## 1. The design in one paragraph

nginx runs on the droplet host (apt package, not a compose service), listens on :443 with a
Let's Encrypt IP certificate and `auth_basic`, and proxies exclusively to `127.0.0.1:8090`.
Port 80 serves only the ACME webroot and a redirect to HTTPS. The DO firewall opens 80 and
443 alongside the existing 22. The basic-auth credential is a **pre-hashed** bcrypt htpasswd
line held as a GitHub secret — CI never sees the cleartext; the operator generates it locally
once and tells the audience the password at the demo. A small `infra/ci/console-publish.sh`
runs on the droplet after the pack deploy: it starts the console, writes the htpasswd file,
obtains/renews the IP cert, and enables the nginx site — in that order, so the site is never
enabled before auth and TLS material exist (fail closed).

Audience URL: `https://<droplet-ip>` — a bare IP with a real padlock.

## 2. Changes, file by file

### 2.1 `infra/terraform/main.tf` — two firewall rules

```hcl
  # Console exposure (infra/CONSOLE-EXPOSURE.md): 80 is ACME
  # validation + redirect only; 443 is nginx -> auth_basic -> 127.0.0.1:8090.
  # Nothing else is proxied; every other service remains loopback + tunnel.
  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }
  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }
```

No other Terraform resources. (A reserved IP was considered and cut — see §6.1.)

### 2.2 `infra/terraform/cloud-init.yaml` — install nginx + certbot, stage the site config

Additions:

```yaml
packages:
  # ... existing list ...
  - nginx

runcmd:
  # ... existing lines ...
  - snap install --classic certbot        # Ubuntu 24.04 apt certbot is too old; IP certs need >= 5.4
  - ln -sf /snap/bin/certbot /usr/bin/certbot
  - rm -f /etc/nginx/sites-enabled/default # nothing served until console-publish.sh enables the site

write_files:
  - path: /etc/nginx/sites-available/kp2-console.conf
    content: |
      # (content of infra/nginx/kp2-console.conf, below)
```

The site config is **staged but not enabled** at boot. Between `terraform apply` and the
publish step, :443 has no listener and :80 serves nothing — an exposure window of zero.

### 2.3 `infra/nginx/kp2-console.conf` (new) — the whole exposure surface

```nginx
# The ONLY public route into the droplet besides SSH. Proxies one loopback
# port; adding a second location/upstream here is a security decision, not
# an edit.
limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=exchange:10m rate=1r/s;

server {
    listen 80 default_server;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl default_server;
    server_tokens off;

    # --cert-name kp2-console keeps this path stable across renewals
    ssl_certificate     /etc/letsencrypt/live/kp2-console/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/kp2-console/privkey.pem;

    auth_basic           "restricted";          # deliberately uninformative realm
    auth_basic_user_file /etc/nginx/kp2.htpasswd;

    limit_req zone=general burst=20 nodelay;

    # Every /api/exchange hit is several real, authenticated X-Road calls.
    location /api/exchange/ {
        limit_req zone=exchange burst=5;
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;            # console's Origin-vs-Host check depends on this
        proxy_read_timeout 60s;
    }

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_read_timeout 180s;                # /api/reset performs several logins at up to 10 s each
    }
}
```

### 2.4 `infra/ci/console-publish.sh` (new) — runs on the droplet, after `remote-deploy.sh`

```bash
#!/usr/bin/env bash
# Publish the console at https://<droplet-ip> behind basic auth. Idempotent:
# safe to run on every deploy (cert is renewed only when due, htpasswd is
# overwritten, console.sh up is a no-op on a healthy console).
# Requires: KP2_CONSOLE_HTPASSWD in the environment -- one pre-hashed
# htpasswd line (bcrypt), passed over SSH from the workflow secret.
set -euo pipefail
: "${KP2_CONSOLE_HTPASSWD:?pass the pre-hashed htpasswd line via env}"

PACK="/opt/kp2/repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack"
IP=$(curl -sf http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address)

# 1. The thing being exposed must be running (remote-deploy.sh does not start it).
"$PACK/scripts/console.sh" up

# 2. Auth material before any listener exists.
install -m 640 -g www-data /dev/null /etc/nginx/kp2.htpasswd
printf '%s\n' "$KP2_CONSOLE_HTPASSWD" > /etc/nginx/kp2.htpasswd

# 3. IP certificate (shortlived profile is mandatory for IP certs).
certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --webroot --webroot-path /var/www/html \
  --ip-address "$IP" --preferred-profile shortlived \
  --cert-name kp2-console --deploy-hook 'systemctl reload nginx'

# 4. Only now: enable and reload.
ln -sf /etc/nginx/sites-available/kp2-console.conf /etc/nginx/sites-enabled/kp2-console.conf
nginx -t && systemctl reload nginx
echo "console published: https://$IP (basic auth)"
```

Renewal for droplets that outlive the 160 h cert: the snap installs its own systemd renewal
timer; `--deploy-hook` reloads nginx. Nothing custom to build.

### 2.5 Workflow step (monorepo root — outside this pack directory)

In `.github/workflows/kp2-federation.yml`, after the existing remote-deploy step:

```yaml
- name: Publish console
  run: |
    ssh "root@$DROPLET_IP" "KP2_CONSOLE_HTPASSWD='${{ secrets.KP2_CONSOLE_HTPASSWD }}' \
      bash /opt/kp2/repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack/infra/ci/console-publish.sh"
```

Plus one new repo secret, **`KP2_CONSOLE_HTPASSWD`**, generated locally once:

```bash
htpasswd -nB kp2demo        # prompts for the password; paste the OUTPUT LINE into the secret
```

The secret is a bcrypt hash — a leak of the secret store does not leak the password.
This must be a **separate workflow step**, not lines inside `remote-deploy.sh` — see §4.4.

### 2.6 Documentation

`infra/DO-DEPLOYMENT.md` currently states the posture as "the DO firewall admits only SSH"
and "never opening the ports." Both sentences must be amended to record the scoped exception:
*443/80 open, terminating at nginx, proxying only 127.0.0.1:8090, behind basic auth over TLS;
all other ports unchanged.* One paragraph, not a new document.

## 3. What "public" now means, precisely

Reachable by anyone: the TLS handshake, an HTTP 401, and the ACME webroot. Reachable by
password holders: the full console — topology view, exchange demo, ACL grant/revoke/reset,
and the join tab when `KP2_JOIN_OPERATOR_TOKEN` is set (per the decision, the audience gets
the real console, mutations included; the blast radius of those mutations is the pack's own:
one mutable ACL, journaled and watchdog-reverted). Reachable by nobody: everything else —
unchanged loopback binds behind an SSH-only path.

## 4. Impact on the existing deployment scripts

Requested explicitly, checked against the scripts as they stand. (Note: the parent folder
`KP2-GIF/` itself contains no deployment script — the `build_kp2_module*.js` files there are
module-content builders. The deployment flow lives in `KP2-build-pack/scripts/` and
`infra/ci/`.)

**`scripts/deploy.sh` — no impact.** It delegates to `hurl/run-linkup.sh` and touches only
the federation. Host nginx is invisible to it.

**`scripts/lib-stack.sh` — no impact, one caveat to document.** Its exposure guard reads
`deployment.yaml`'s `network.bind`, which stays `127.0.0.1`, so the guard passes untouched —
including its absolute Test-CA refusal, which never evaluates. The guard's *spirit* also
holds: what it exists to prevent (unauthenticated publication of the SS proxy ports, CS UI,
and `/testca/sign`) is exactly what this design does not do. The caveat: the guard inspects
configuration, not listening sockets, so it **cannot see** host-level nginx. After this
change, "lib-stack.sh would have refused" no longer tells the whole exposure story — which is
precisely why §2.6's documentation paragraph is a required part of the change, not polish.

**`scripts/console.sh` — no impact.** `CONSOLE_URL="http://${XROAD_BIND}:8090"` stays
loopback; `up`/`down`/`reset`/`status` all act on the droplet locally, under nginx.
`console.sh down` becomes the instant kill switch: nginx then serves 502 to authenticated
users and the exposure is effectively over without touching the firewall.

**`scripts/gen-secrets.sh` — deliberately no impact.** The console password is
infrastructure-layer, not pack-layer: it lives in the GitHub secret and
`/etc/nginx/kp2.htpasswd`, never in `.env`. This keeps "no pack changes" true and keeps
gen-secrets' contract (X-Road secrets, generated on the host, never leaving it) unmuddied.
One dependency: the join tab needs `KP2_JOIN_OPERATOR_TOKEN` in `.env`, and remote-deploy.sh's
re-run path (`gen-secrets.sh` append mode) already provides it.

**`infra/ci/remote-deploy.sh` — two real interactions, one of them a trap:**

1. *It never starts the console.* Its sequence ends at `acceptance.sh`; the console is a
   `demo`-profile service started by `scripts/console.sh up`, which nothing in the current
   remote flow calls. Without §2.4's step 1, the workflow would open :443 onto a 502.
   `console-publish.sh` owns this, keeping remote-deploy.sh byte-identical.
2. *The early-exit path.* On re-runs against a live federation, remote-deploy.sh prints
   "acceptance only" and `exit 0` before reaching anything appended after it. Any publish
   logic placed inside that script would silently not run on exactly the most common
   invocation. Hence §2.5: publish is a separate SSH step that runs unconditionally after
   remote-deploy — also what makes htpasswd rotation work (change the secret, rerun
   `deploy`, no redeploy of the federation).

## 5. Security analysis

**5.1 Single shared password (accepted, bounded).** Basic auth with one credential is the
weakest deliberate choice here, accepted because the audience is known and the host is
ephemeral. Bounds: bcrypt hash at rest in CI; cleartext only in heads and on a slide;
rotation is one secret update + rerun; and a leaked password dies with the droplet at
`destroy`. Online brute force is blunted by the `general` rate zone (10 r/s/IP). If the same
password is reused across many sessions, that erodes — prefer rotating per demo series.

**5.2 What the password protects (the honest core).** An authenticated visitor can revoke/
grant one ACL, trigger resets, run exchanges, and approve joins if the operator token is set.
That is the design, but two notes: the ACL watchdog auto-reverts only after 120 s with **no**
heartbeat, and any open authenticated tab keeps heartbeating — a visitor can hold the ACL
revoked for as long as they keep the page open. And nginx's access log is now the audit trail
tying actions to source IPs; keep it on (default) for the session.

**5.3 TLS is never optional on the auth path.** Basic auth sends the credential on every
request, so :80 must never proxy — in this config it only redirects and serves ACME. The
ordering in `console-publish.sh` (htpasswd → cert → enable) means there is no state in which
the console is reachable without both TLS and auth. Cert-issuance failure fails closed: the
site never enables, the tunnel path still works. HSTS is omitted deliberately — it is
ill-defined for bare-IP origins and adds nothing here.

**5.4 Console's own guards keep working.** `proxy_set_header Host $host` preserves the
Origin-vs-Host CSRF check (`https://<ip>` matches). The `x-kp2-console` header requirement
continues to hold for state-changing endpoints. These now sit *behind* auth rather than being
the only line, which is the correct layering.

**5.5 Short-lived cert operational risk.** 160 h validity on an always-on droplet means an
expiry outage if renewal breaks silently (port 80 blocked, LE incident). The on-demand
lifecycle makes this mostly moot — each `up` issues fresh — but if the droplet is ever left
running across a week, `certbot renew --dry-run` belongs in the deploy step's output. A new
LE account per droplet (`--register-unsafely-without-email`) is fine at this cadence and well
inside LE rate limits.

**5.6 New parsing surface.** nginx itself becomes internet-facing parsing surface that wasn't
there before. Mitigations: stock Ubuntu nginx with security updates (`package_update` at
boot, short host lifetime), `server_tokens off`, no third-party modules, default-server-only
config, and a 401 to every unauthenticated scanner.

**5.7 Unchanged pre-existing exposures (out of scope, on the record).** SSH open to
0.0.0.0/0 (key-only) — unchanged, same rationale as before (GitHub runners have no fixed
egress). Test CA, CS UI fixed creds, SS proxy ports — all still loopback-only; this change
does not move them one millimeter closer to the internet.

## 6. Over-engineering review

**6.1 Reserved IP — CUT (reversing the earlier recommendation).** Scrutinized, it buys only
a stable URL string: the cert is re-issued per `up` regardless, the password is shared live
per session regardless, and the operator announces the URL at demo start regardless. Cost: a
manually-managed DO resource outside Terraform's destroy scope, an assignment resource, and
$5/mo while parked. A URL that changes per session is not a real cost for a live-demo tool.
Add it later only if attendees genuinely need a bookmarkable address between sessions.

**6.2 No `console_public` toggle variable.** A conditional exposure flag in Terraform is a
dead branch to test and reason about. The off switch already exists three times over:
`git revert` of this change, `terraform destroy` of the droplet, or `console.sh down`.

**6.3 No oauth2-proxy, per-user accounts, fail2ban, or WAF.** All were considered and
rejected for a known audience on a host that lives for hours: oauth2-proxy drags in an IdP
(the third party this design exists to avoid), per-user htpasswd adds ceremony with no
threat-model payoff, fail2ban duplicates what `limit_req` + bcrypt + ephemerality already
provide, and a WAF in front of a five-endpoint FastAPI demo is costume armor.

**6.4 No custom cert-renewal machinery.** The certbot snap's own timer plus `--deploy-hook`
is the entire renewal story. Any hand-rolled timer/cron here would be re-implementing a
default.

**6.5 nginx stays on the host, out of compose.** Putting the proxy in `docker-compose.yml`
(another service, another profile) would make the pack itself exposure-aware — the exact
property this design pays to preserve. Host nginx keeps the pack's verified surface
byte-identical and the exposure delta greppable in `infra/`.

**6.6 Rate limiting stops at two zones.** One general, one for `/api/exchange`. Per-endpoint
tiers, connection caps, and request-body limits beyond defaults would be tuning a demo like a
production API gateway.

**6.7 Residual-complexity check.** Final delta: 2 firewall rules, ~5 cloud-init lines, 1
nginx config, 1 publish script (~25 lines), 1 workflow step, 1 GitHub secret, 1 docs
paragraph. Removing anything that remains drops one of TLS, auth, the running console, or
the audit trail — i.e., the plan is at its floor.

## 7. Sources

- [Let's Encrypt: 6-day and IP address certificates generally available (Jan 2026)](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)
- [Let's Encrypt: six-day and IP certs in certbot ≥ 5.4 — flags and webroot example (Mar 2026)](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)
- [EFF: certbot IP address certificate support](https://www.eff.org/deeplinks/2026/03/certbot-and-lets-encrypt-now-support-ip-address-certificates)
- [Caddy #7399: no native public-IP certificate issuance (why nginx+certbot, not Caddy)](https://github.com/caddyserver/caddy/issues/7399)
- [DigitalOcean reserved IP pricing (basis for §6.1's cut)](https://docs.digitalocean.com/products/networking/reserved-ips/details/pricing/)
- [DigitalOcean: droplets have no default DNS names](https://www.digitalocean.com/community/questions/do-droplets-have-default-dns-names)
- Pack sources: `docker-compose.yml`, `deployment.yaml`, `apps/console/app.py`,
  `scripts/lib-stack.sh`, `scripts/console.sh`, `scripts/deploy.sh`,
  `infra/ci/remote-deploy.sh`, `infra/terraform/*`, `infra/DO-DEPLOYMENT.md`.
