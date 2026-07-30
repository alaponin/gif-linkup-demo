#!/usr/bin/env bash
# Assert every published port in the rendered Compose configuration binds to
# loopback -- the tested form of scripts/lib-stack.sh's own deploy-time refusal.
# S1 (docs/reviews/2026-07-28-branch-review.md) was a one-line-per-service
# mistake; without this, it is a one-line-per-service mistake again the next
# time a service is added. Reads the RENDERED config, not deployment.yaml's
# stated intent, so a bare, unbound `ports:` entry on a new service is
# caught even though no single field says "public". Every profile is
# activated (full/demo/tools) so the check covers the console and the Hurl
# runner too, not just whatever profile happens to be active right now.
set -euo pipefail
. "$(dirname "$0")/lib-core.sh"

# docker-compose.yml interpolates these four from deployment.yaml (see
# scripts/lib-stack.sh's equivalent export block) into image tags and every
# service's published-port host_ip. Read directly via yq_get (pure -- no
# Docker, no .env, no exit) rather than sourcing lib-stack.sh: this script's
# whole point is to be checkable with no federation and no .env in the
# picture, and lib-stack.sh's .env sourcing/credential refusal would defeat
# that. Left unexported, docker-compose.yml's own `${XROAD_BIND:-127.0.0.1}`
# fallback silently renders every port as loopback regardless of what
# deployment.yaml actually says -- which is exactly the exposure this script
# exists to catch.
DEPLOY_SPEC="$PACK_DIR/deployment.yaml"
export XROAD_VERSION=$(yq_get "$DEPLOY_SPEC" xroad.version)
export XROAD_CS_TAG=$(yq_get "$DEPLOY_SPEC" xroad.cs_tag)
export TESTCA_TAG=$(yq_get "$DEPLOY_SPEC" xroad.testca_tag)
export XROAD_BIND=$(yq_get "$DEPLOY_SPEC" network.bind)

COMPOSE_FILES=(-f "$PACK_DIR/docker-compose.yml" -f "$PACK_DIR/hurl/compose.hurl.yml")
[ -f "$PACK_DIR/hurl/compose.members.yml" ] && COMPOSE_FILES+=(-f "$PACK_DIR/hurl/compose.members.yml")

docker compose "${COMPOSE_FILES[@]}" --profile full --profile demo --profile tools \
  config --format json | python3 -c "
import json, sys, yaml

LOOPBACK = {'127.0.0.1', '::1', 'localhost'}
deployment = yaml.safe_load(open('$PACK_DIR/deployment.yaml'))
acknowledged = (deployment.get('network') or {}).get('acknowledge_public_exposure') is True

config = json.load(sys.stdin)
exposed = []
for name, svc in config.get('services', {}).items():
    for port in svc.get('ports', []):
        host_ip = port.get('host_ip')
        if host_ip not in LOOPBACK:
            exposed.append(f\"{name}: {host_ip or '0.0.0.0'}:{port['published']} -> {port['target']}/{port['protocol']}\")

if not exposed:
    print('check-exposure: every published port binds to loopback')
    sys.exit(0)

label = 'network.acknowledge_public_exposure is set -- exposed to the network' if acknowledged else 'FAIL -- exposed with no network.acknowledge_public_exposure: true'
print(f'check-exposure: {label}:')
for e in exposed:
    print(f'  {e}')
sys.exit(0 if acknowledged else 1)
"
