#!/usr/bin/env bash
# Publish the console at https://<droplet-ip> behind basic auth, and
# join-api's applicant surface at https://<droplet-ip>/join/ behind its own
# bearer tokens (see infra/CONSOLE-EXPOSURE.md). Runs ON THE DROPLET, after
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

# THIS is the script that starts the console and join-api on the droplet --
# remote-deploy.sh never does, and this runs in its own ssh session, so
# nothing that script exported reaches here. Before this line both containers
# came up as UID 0 on every normal deploy and stayed there
# (`restart: unless-stopped`), which is exactly the posture
# docs/security-review-2026-08-23.md's finding H1 is about: at UID 0 the
# ownership/sticky-bit backstop is bypassed outright by CAP_DAC_OVERRIDE.
#
# scripts/lib-stack.sh now also resolves this from the `kp2` account
# directly, so this export is belt-and-braces rather than the only thing
# holding it up -- but it is the visible statement of intent at the two calls
# that matter, and remote-deploy.sh (which runs first in the same CI job) has
# guaranteed the account exists by now.
export KP2_CONTAINER_UID=10001
export KP2_CONTAINER_GID=10001

# Fails before the htpasswd file or any listener exists -- same fail-closed
# ordering as the KP2_CONSOLE_HTPASSWD guard above and `nginx -t` below.
# :443 is a production public surface; deployment.yaml must say so on
# purpose (security-review-remediation-plan.md Phase A, H3) rather than this
# script publishing it because it happened to run.
POSTURE=$(python3 -c "
import sys, yaml
print((yaml.safe_load(open(sys.argv[1])) or {}).get('posture', 'demo'))
" "$PACK/deployment.yaml")
if [ "$POSTURE" != "production" ]; then
  echo "console-publish.sh: deployment.yaml posture is ${POSTURE:-demo}, not
production. Refusing to publish :443 -- set posture: production in
deployment.yaml first (see docs/production-delta.md)." >&2
  exit 1
fi

IP=$(curl -sf http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address)

# 1. The thing being exposed must be running: remote-deploy.sh stops at
#    acceptance and never starts the demo-profile console, so without this
#    :443 would open onto a 502.
"$PACK/scripts/console.sh" up

# 1b. join-api serves the public /join/ applicant surface
#     (CONSOLE-EXPOSURE.md section 7) -- demo-profile like the console,
#     started by nothing else in the remote flow. Its two bearer tokens
#     exist because remote-deploy.sh's gen-secrets.sh append run creates
#     any missing KP2_JOIN_* keys.
"$PACK/scripts/join.sh" up

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
echo "join-api applicant surface: https://$IP/join/ (bearer token; fetch with:"
echo "  ssh root@$IP \"grep KP2_JOIN_APPLICANT_TOKEN $PACK/.env\")"
