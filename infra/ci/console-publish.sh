#!/usr/bin/env bash
# Publish the console at https://<droplet-ip> behind basic auth (see
# infra/CONSOLE-EXPOSURE.md). Runs ON THE DROPLET, after
# remote-deploy.sh, as its own workflow step -- not appended to
# remote-deploy.sh, whose "acceptance only" early exit would skip it on
# exactly the most common invocation.
#
# Idempotent: safe on every deploy (the cert is renewed only when due, the
# htpasswd file is overwritten, `console.sh up` is a no-op on a healthy
# console). Rotating the password is: change the secret, re-run deploy.
#
# Requires: KP2_CONSOLE_HTPASSWD -- ONE pre-hashed htpasswd line, generated
# locally once with `htpasswd -nB kp2demo` and held as a GitHub secret, so
# CI never sees the cleartext.
set -euo pipefail
: "${KP2_CONSOLE_HTPASSWD:?pass the pre-hashed htpasswd line via env}"

PACK="/opt/kp2/repo/10-Knowledge-Products/KP2-GIF/KP2-build-pack"
IP=$(curl -sf http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address)

# 1. The thing being exposed must be running: remote-deploy.sh stops at
#    acceptance and never starts the demo-profile console, so without this
#    :443 would open onto a 502.
"$PACK/scripts/console.sh" up

# 2. Auth material before any listener that could serve the console.
install -m 640 -g www-data /dev/null /etc/nginx/kp2.htpasswd
printf '%s\n' "$KP2_CONSOLE_HTPASSWD" > /etc/nginx/kp2.htpasswd

# 3. :80 (ACME webroot + redirect only) so certbot --webroot can be
#    answered, here and on every unattended renewal.
cp "$PACK/infra/nginx/kp2-acme.conf" /etc/nginx/sites-available/kp2-acme.conf
ln -sf /etc/nginx/sites-available/kp2-acme.conf /etc/nginx/sites-enabled/kp2-acme.conf
nginx -t && systemctl reload nginx

# 4. IP certificate. The shortlived profile (160 h) is mandatory for IP
#    certs; renewal is the certbot snap's own timer plus this deploy hook.
certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --webroot --webroot-path /var/www/html \
  --ip-address "$IP" --preferred-profile shortlived \
  --cert-name kp2-console --deploy-hook 'systemctl reload nginx'

# 5. Only now, with both auth and TLS material on disk: the console itself.
cp "$PACK/infra/nginx/kp2-console.conf" /etc/nginx/sites-available/kp2-console.conf
ln -sf /etc/nginx/sites-available/kp2-console.conf /etc/nginx/sites-enabled/kp2-console.conf
nginx -t && systemctl reload nginx

echo "console published: https://$IP (basic auth)"
