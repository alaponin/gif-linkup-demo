#!/usr/bin/env bash
# Start, stop, or reset the KP2 demonstration console -- demo only, never
# production (docs/production-delta.md). Requires the federation already
# up (hurl/run-linkup.sh) and reachable on the linkup network; this script
# only manages the console container, never the federation itself.
#
#   scripts/console.sh up      # build + start, off the linkup network at :8090
#   scripts/console.sh down    # stop (out/console-acl-journal.json, if any, is untouched)
#   scripts/console.sh reset   # force a reset now, without waiting for the watchdog
#   scripts/console.sh status  # container + health check
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

CONSOLE_URL="http://localhost:8090"
# COMPOSE already carries --profile full/lite from deployment.yaml (lib-stack.sh);
# --profile demo is additive, and only console is ever targeted below, so
# this never starts or restarts the federation itself.
COMPOSE_DEMO=("${COMPOSE[@]}" --profile demo)

case "${1:-}" in
  up)
    "${COMPOSE_DEMO[@]}" up -d --build console
    log "console up at $CONSOLE_URL -- demo only, never production (docs/production-delta.md)"
    ;;
  down)
    "${COMPOSE_DEMO[@]}" stop console
    log "console stopped"
    ;;
  reset)
    curl -sf -X POST "$CONSOLE_URL/api/reset" | python3 -m json.tool
    ;;
  status)
    "${COMPOSE_DEMO[@]}" ps console
    curl -sf "$CONSOLE_URL/api/health" | python3 -m json.tool || fail "console not reachable at $CONSOLE_URL"
    ;;
  *)
    fail "usage: scripts/console.sh {up|down|reset|status}"
    ;;
esac
