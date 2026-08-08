#!/usr/bin/env bash
# Pull every image deployment.yaml pins, by digest, so a firewalled host, a
# conference network, or an air-gapped demo machine can be prepared while it
# still has network -- deploy.sh itself pulls lazily and needs egress at
# deploy time otherwise. Does not touch apps/mock-registry (built locally,
# no pull) or hurl/compose.hurl.yml's runner image (pinned separately --
# see its own comment for why it isn't a deployment.yaml key).
#
#   scripts/preload-images.sh
set -euo pipefail
. "$(dirname "$0")/lib-core.sh"

DEPLOY_SPEC="$PACK_DIR/deployment.yaml"
CS_DIGEST=$(yq_get "$DEPLOY_SPEC" xroad.cs_digest)
SS_DIGEST=$(yq_get "$DEPLOY_SPEC" xroad.ss_digest)
TESTCA_TAG=$(yq_get "$DEPLOY_SPEC" xroad.testca_tag)

IMAGES=(
  "niis/xroad-central-server:${CS_DIGEST}"
  "niis/xroad-security-server-sidecar:${SS_DIGEST}"
  "ghcr.io/nordic-institute/xrddev-testca:${TESTCA_TAG}"
)

for image in "${IMAGES[@]}"; do
  log "pulling $image"
  docker pull "$image"
done

log "preloaded ${#IMAGES[@]} images:"
for image in "${IMAGES[@]}"; do
  echo "  $image"
done
