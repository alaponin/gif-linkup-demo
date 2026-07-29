#!/usr/bin/env bash
# Stand the Linkup federation up by driving the Central Server and Security
# Server admin APIs with Hurl -- the sanctioned config-as-code path.
#
# Equivalent of Docker/xrd-dev-stack/local-dev-run.sh --initialize at X-Road
# 7.7.0, retargeted from DEV:COM to Progressa's education-sector federation.
#
#   hurl/run-linkup.sh            # stand up the federation
#   hurl/run-linkup.sh --dry-run  # build the concatenated file, run nothing
#
# Proving it is a separate job and a separate tool: scripts/seed.sh then
# scripts/acceptance.sh, which owns module 2.6's four assertions. Two of them
# (exact-set equality of the assembled application, and the seeded-record
# comparison) are beyond what a Hurl scenario can assert, so the pack keeps one
# implementation of its headline check rather than a weaker second copy.
#
# Captures do not cross Hurl file boundaries, so the scenarios are concatenated
# in lexical order into one file before the run -- the same thing NIIS's own
# run-hurl.sh does. Order matters and is encoded in the filenames:
#
#   00-03  Central Server: init, trust services, members, anchor
#   10     management Security Server (captures ca_name, tsa_name, tsa_url)
#   20-23  member Security Servers: PNIA, PLR, MoEYS, PNEA
#   30-32  service descriptions and access rights
set -euo pipefail

PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PACK_DIR"
. scripts/lib.sh

BUILD_DIR="$PACK_DIR/hurl/.build"
DRY=0
case "${1:---setup}" in
  --setup|"") ;;
  --dry-run)  DRY=1 ;;
  *) fail "unknown argument: $1 (try --setup or --dry-run; to prove the exchange, run scripts/seed.sh then scripts/acceptance.sh)" ;;
esac

# Regenerate first: the scenarios are artefacts of configs/, never hand-edited.
log "regenerating scenarios from configs/"
python3 hurl/generate.py

mkdir -p "$BUILD_DIR"
cat hurl/scenarios/*.hurl > "$BUILD_DIR/setup.hurl"
log "built .build/setup.hurl ($(grep -c '^\(GET\|POST\|PUT\|PATCH\|DELETE\) ' "$BUILD_DIR/setup.hurl") requests)"

if [ "$DRY" = 1 ]; then
  log "dry run — nothing executed. Inspect hurl/.build/setup.hurl"
  exit 0
fi

COMPOSE_HURL=("${COMPOSE[@]}" -f "$PACK_DIR/hurl/compose.hurl.yml")

# Fail fast: the --fast tier (static checks, the ship gate, exposure,
# pytest) runs before any container starts -- a typo used to cost fifteen
# minutes to discover; now it costs seconds (testing-strategy plan Task 2).
"$PACK_DIR/scripts/verify.sh" --fast

# Phase timings (testing-strategy plan Task 5) -- nobody knew which part of
# the ~918s deploy dominates: container boot, global-conf propagation, or
# the certificate sequences. Three timestamps, not more: "containers
# healthy" and "Hurl run start" bracket boot; "Hurl run end" brackets
# everything the admin-API sequence itself does.
DEPLOY_START=$(date +%s)

log "bringing the federation containers up"
"${COMPOSE_HURL[@]}" up -d --build

log "waiting for containers to report healthy"
# Bounded, not open-ended: hurl/compose.hurl.yml's own healthcheck budget is
# retries:120 at 5s (600s) per server -- wait a little past that, then fail
# clearly instead of hanging forever on a container that never recovers.
_HEALTH_WAITED=0
until [ "$(docker inspect -f '{{.State.Health.Status}}' cs ca ss-pdga ss-pnea ss-plr 2>/dev/null | sort -u)" = "healthy" ]; do
  [ "$_HEALTH_WAITED" -ge 660 ] && fail "cs/ca/ss-pdga/ss-pnea/ss-plr did not all report healthy within 660s -- docker ps to see which"
  sleep 2
  _HEALTH_WAITED=$((_HEALTH_WAITED + 2))
done
CONTAINERS_HEALTHY=$(date +%s)

mkdir -p "$PACK_DIR/out"
HURL_START=$(date +%s)
log "driving the admin APIs (expect a stretch of HTTP errors and retries —"
log "global configuration propagation is asynchronous and takes minutes)"
"${COMPOSE_HURL[@]}" run --rm hurl \
  --insecure \
  --variables-file /hurl-src/vars.env \
  --file-root /hurl-files \
  --report-json /hurl-out/hurl-report \
  /hurl-src/.build/setup.hurl \
  --very-verbose \
  --retry 12 \
  --retry-interval 10000
HURL_END=$(date +%s)

{
  echo "deploy_start=$DEPLOY_START"
  echo "containers_healthy=$CONTAINERS_HEALTHY"
  echo "hurl_run_start=$HURL_START"
  echo "hurl_run_end=$HURL_END"
  echo "phase_containers_boot_seconds=$((CONTAINERS_HEALTHY - DEPLOY_START))"
  echo "phase_hurl_run_seconds=$((HURL_END - HURL_START))"
  echo "total_seconds=$((HURL_END - DEPLOY_START))"
} | tee "$PACK_DIR/out/deploy-timings.txt"

# Fingerprint, never the PIN itself: the software token on every server is
# now initialised with this value. scripts/lib.sh refuses a later run whose
# .env disagrees with this fingerprint while the volumes still exist --
# changing .env alone does not change the token (docs/xroad-770-notes.md
# §9), and this is what lets that be caught here instead of 20 minutes into
# a confusing SslAuthenticationFailed.
mkdir -p "$PACK_DIR/out"
printf '%s' "$XROAD_TOKEN_PIN" | shasum -a 256 | cut -d' ' -f1 > "$PACK_DIR/out/.token-fingerprint"
chmod 600 "$PACK_DIR/out/.token-fingerprint"

log "federation up — now: scripts/seed.sh, then scripts/acceptance.sh"
