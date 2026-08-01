#!/usr/bin/env bash
# Start, stop, or check the KP2 member-join API -- demo only, never
# production (docs/production-delta.md). Requires the federation already
# up (hurl/run-linkup.sh) and reachable on the linkup network; this script
# only manages the join-api container, never the federation itself -- it
# targets exactly one service, so it can never start or restart it.
#
#   scripts/join.sh up      # build + start, off the linkup network at :8091
#   scripts/join.sh down    # stop
#   scripts/join.sh status  # container + health check
#
# No `reset` subcommand: unlike the console, join-api has no ACL journal to
# reset (its later tasks add a job context instead, with its own recovery
# via resume, not reset).
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

JOIN_URL="http://localhost:8091"
# COMPOSE already carries --profile full/lite from deployment.yaml (lib-stack.sh);
# --profile demo is additive, and only join-api is ever targeted below, so
# this never starts or restarts the federation itself.
COMPOSE_DEMO=("${COMPOSE[@]}" --profile demo)

case "${1:-}" in
  up)
    # --wait: join-api has its own HEALTHCHECK (mirrors console's), so "up"
    # can wait for it and actually mean "serving" instead of just "process
    # started". Timeout matches console.sh's own.
    "${COMPOSE_DEMO[@]}" up -d --build --wait --wait-timeout 30 join-api
    log "join-api up at $JOIN_URL -- demo only, never production (docs/production-delta.md)"
    ;;
  down)
    "${COMPOSE_DEMO[@]}" stop join-api
    log "join-api stopped"
    ;;
  status)
    "${COMPOSE_DEMO[@]}" ps join-api
    curl -sf "$JOIN_URL/health" | python3 -m json.tool || fail "join-api not reachable at $JOIN_URL"
    ;;
  *)
    fail "usage: scripts/join.sh {up|down|status}"
    ;;
esac
