#!/usr/bin/env bash
# First contact: runbook.md's Steps 1-5, in order, with a stamp before each
# one so a long silent stretch reads as normal rather than as a hang. This
# wraps those steps; it does not replace them. Every script it calls is named
# below and documented in runbook.md -- read that for what any of them does.
#
#   scripts/demo.sh                 # preflight, gen-secrets, deploy, seed,
#                                    # acceptance, console
#   scripts/demo.sh --skip-console  # stop after acceptance
#
# Refuses if a federation is already deployed. The Hurl stand-up always runs
# the full sequence and is not idempotent against configured state (a
# persisted Central Server answers POST /api/v1/initialization with 409
# init_already_initialized), so re-running it is exactly the trap this script
# exists to keep a first-time reader out of.
set -euo pipefail
# lib-core.sh only: lib-stack.sh refuses on a missing .env, which is the state
# this script is meant to fix at step 1.
. "$(dirname "$0")/lib-core.sh"
cd "$PACK_DIR"

SKIP_CONSOLE=0
case "${1:-}" in
  "") ;;
  --skip-console) SKIP_CONSOLE=1 ;;
  *) echo "usage: scripts/demo.sh [--skip-console]" >&2; exit 1 ;;
esac

stage() { printf '\n\033[1;36m[demo %s]\033[0m %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }

BIND=$(yq_get "$PACK_DIR/deployment.yaml" network.bind)

# Two states have to be refused, and only one of them is reachable over HTTP:
# a running federation, and a stopped-but-not-purged one whose volumes still
# hold the configured /etc/xroad. The volume check catches both, so it runs
# first; the reachability probe (verify.sh --live's, single-shot here because
# this one is refusing on a hit rather than waiting for one) only picks which
# resume line to print.
if [ -n "$(docker volume ls -q --filter name=kp2-cs-conf)" ]; then
  if curl -sk --max-time 3 -o /dev/null "https://${BIND}:4000" 2>/dev/null; then
    fail "a federation is already up at https://${BIND}:4000 -- this script only stands one up from zero.
Run scripts/acceptance.sh against it, or scripts/verify.sh --live.
To start over: scripts/teardown.sh --purge, then re-run this script."
  fi
  fail "a federation is deployed but not running (its volumes still hold the configured /etc/xroad).
Resume it: docker compose -f docker-compose.yml -f hurl/compose.hurl.yml up -d
To start over: scripts/teardown.sh --purge, then re-run this script."
fi

stage "step 0 -- scripts/preflight.sh (host dependencies, .env, clock, RAM)"
"$PACK_DIR/scripts/preflight.sh"

stage "step 1 -- scripts/gen-secrets.sh (a real .env, mode 600)"
if [ -f "$PACK_DIR/.env" ]; then
  log "'.env' already present and complete (preflight checked it) -- keeping it, nothing rotated."
else
  "$PACK_DIR/scripts/gen-secrets.sh"
fi

stage "step 2 -- scripts/deploy.sh (the federation, from zero)"
log "expect ~9-10 minutes: ~156s to containers-healthy, then a ~395s Hurl run."
log "a stretch of HTTP errors and retries partway through is global-conf propagation, not a failure."
"$PACK_DIR/scripts/deploy.sh"

stage "step 3 -- scripts/seed.sh (Progressa demonstration data)"
"$PACK_DIR/scripts/seed.sh"

stage "step 4 -- scripts/acceptance.sh (the once-only exchange proves itself)"
"$PACK_DIR/scripts/acceptance.sh"

if [ "$SKIP_CONSOLE" = 0 ]; then
  stage "step 5 -- scripts/console.sh up (the demonstration console)"
  "$PACK_DIR/scripts/console.sh" up
fi

stage "done"
ARTEFACT=$(ls -t "$PACK_DIR"/out/application-*.json 2>/dev/null | head -1)
cat <<EOF
Console (demo only, never production):  http://${BIND}:8090
The once-only artefact acceptance just wrote:
  ${ARTEFACT:-(none found in out/)}

Admin UIs -- concurrent sessions in one browser log each other out, so use
separate browsers or profiles:
  Central Server (PDGA)   https://${BIND}:4000       xrd / secret (fixed, test image)
  Test CA                 http://${BIND}:8888/testca/
  ss-pdga / pnea / plr / pnia  https://${BIND}:1000 / 2000 / 3000 / 5100   .env admin user

Next: exercises.md -- the same operations, with the observations to expect.
EOF
