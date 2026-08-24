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

# Before anything else: lib-stack.sh itself needs python3+PyYAML (yq_get)
# and can shell out to docker compose, so a missing dependency has to be
# caught here, not partway into sourcing it.
scripts/preflight.sh

. scripts/lib-stack.sh

BUILD_DIR="$PACK_DIR/hurl/.build"
DRY=0
case "${1:---setup}" in
  --setup|"") ;;
  --dry-run)  DRY=1 ;;
  *) fail "unknown argument: $1 (try --setup or --dry-run; to prove the exchange, run scripts/seed.sh then scripts/acceptance.sh)" ;;
esac

# Regenerate first: the scenarios are artefacts of configs/, never hand-edited.
log "regenerating scenarios from configs/"
python3 -B hurl/generate.py

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
# minutes to discover; now it costs seconds.
"$PACK_DIR/scripts/verify.sh" --fast

# A correctly-formatted PIN can still be the WRONG one: every server's
# software token was initialised with whatever PIN was in .env the last
# time this script actually deployed (recorded below, as a fingerprint,
# never the value, in out/.token-fingerprint). Changing .env afterwards does
# not change the token -- confirmed live (docs/decisions/xroad-770-notes.md §9) that
# the mismatch surfaces as Server.ClientProxy.SslAuthenticationFailed, which
# reads like a certificate problem, not a PIN one. Only refuse while the
# federation's own volumes still exist: teardown.sh --purge deletes them but
# not this host-side file, and a stale fingerprint must not block a
# legitimate fresh redeploy with a new .env.
#
# A deploy-time check, not a source-time one: this used to run inside
# lib-stack.sh and fired for any script that merely sourced it (console.sh
# status, member.sh list), which have no business refusing over a PIN they
# never use. Called just below, immediately before containers come up --
# scripts/deploy.sh execs into this script, so it is covered too.
check_token_fingerprint() {
  local fp="$PACK_DIR/out/.token-fingerprint"
  if [ -f "$fp" ] && docker volume inspect kp2-cs-db >/dev/null 2>&1; then
    local current stored
    current=$(printf '%s' "$XROAD_TOKEN_PIN" | shasum -a 256 | cut -d' ' -f1)
    stored=$(cat "$fp")
    if [ "$current" != "$stored" ]; then
      echo "run-linkup.sh: .env's XROAD_TOKEN_PIN does not match the PIN this
federation's software token was initialised with. Changing .env alone does
not change the token -- the mismatch surfaces as X-Road errors that look
like certificate faults, not PIN errors (docs/decisions/xroad-770-notes.md §9).
Restore the original .env, or scripts/teardown.sh --purge and redeploy with
the new one." >&2
      exit 1
    fi
  fi
}
check_token_fingerprint

# Phase timings -- nobody knew which part of
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
# The server list is SS_ORDER (hurl/topology.sh, generated from configs/ +
# manifest.yaml and sourced by lib-stack.sh), never a hand-kept copy: it used
# to omit ss-pnia, so the one bounded wait that exists to survive a slow
# restart did not cover it -- a slow ss-pnia hit the Hurl run's own
# depends_on/handshake instead of this loop's clear failure. SS_ORDER also
# already includes a joined member's own Security Server, which
# `up -d` above brings up from hurl/compose.members.yml and which
# hurl/generate.py gives the same healthcheck as the canonical four.
_HEALTH_TARGETS=(cs ca "${SS_ORDER[@]}")
_HEALTH_WAITED=0
until [ "$(docker inspect -f '{{.State.Health.Status}}' "${_HEALTH_TARGETS[@]}" 2>/dev/null | sort -u)" = "healthy" ]; do
  [ "$_HEALTH_WAITED" -ge 660 ] && fail "${_HEALTH_TARGETS[*]} did not all report healthy within 660s -- docker ps to see which"
  sleep 2
  _HEALTH_WAITED=$((_HEALTH_WAITED + 2))
done
CONTAINERS_HEALTHY=$(date +%s)

# TOFU-pin every admin API's own :4000 certificate (security-review-
# remediation-plan.md Phase C, M1) -- captured here, not left to whichever
# caller happens to hit that host first, so console and join-api see a
# populated out/xroad-admin-certs/ the moment they start (docker-compose.yml
# mounts a read-only child over that subdirectory of the ./out mount in
# both). Regenerated on every cold deploy: a changed certificate after this
# point is a fact worth failing on later (apps/console/xroad.py and
# lib-stack.sh's api_key()/api() would simply stop verifying that one host,
# loudly, not silently trust the new one) -- it is never overwritten
# mid-run. _capture_admin_cert() (scripts/lib-stack.sh) is shared with
# scripts/join-agent.sh, which captures a newly joined member's own server
# the same way once IT comes up -- a server this run never sees, because it
# does not exist yet.
log "capturing each admin API's :4000 certificate for pinning"
_capture_admin_cert cs 4000
for _ss in "${SS_ORDER[@]}"; do
  _capture_admin_cert "$_ss" "${SS_UI[$_ss]}"
done
unset _ss

# --verbose, not --very-verbose: the latter prints full request BODIES, which
# for the login and token-init scenarios means cs_admin_password,
# ss_admin_password and token_pin land in the terminal on every deploy and in
# GitHub Actions logs via infra/ci/remote-deploy.sh. That contradicts the
# discipline gen-secrets.sh states in its own output ("Values are never
# printed here"). --verbose keeps the request lines, headers, timings and
# assert results -- everything the deploy is actually read for. Set
# HURL_VERY_VERBOSE=1 to opt back into bodies when debugging a payload, on a
# machine whose scrollback you own.
HURL_VERBOSITY=--verbose
if [ "${HURL_VERY_VERBOSE:-0}" = 1 ]; then
  HURL_VERBOSITY=--very-verbose
  log "HURL_VERY_VERBOSE=1 — request bodies (passwords, token PIN) will be printed"
fi

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
  "$HURL_VERBOSITY" \
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
# now initialised with this value. check_token_fingerprint() above refuses a
# later run whose .env disagrees with this fingerprint while the volumes
# still exist -- changing .env alone does not change the token (docs/decisions/xroad-770-notes.md
# §9), and this is what lets that be caught here instead of 20 minutes into
# a confusing SslAuthenticationFailed.
mkdir -p "$PACK_DIR/out"
printf '%s' "$XROAD_TOKEN_PIN" | shasum -a 256 | cut -d' ' -f1 > "$PACK_DIR/out/.token-fingerprint"
chmod 600 "$PACK_DIR/out/.token-fingerprint"

log "federation up — now: scripts/seed.sh, then scripts/acceptance.sh"
