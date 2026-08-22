#!/usr/bin/env bash
# Start, stop, or check the KP2 member-join API -- demo only, never
# production (docs/production-delta.md). Requires the federation already
# up (hurl/run-linkup.sh) and reachable on the linkup network; this script
# only manages the join-api and spec-fetcher containers, never the
# federation itself -- it targets exactly those two services, so it can
# never start or restart the federation.
#
#   scripts/join.sh up      # build + start spec-fetcher and join-api
#   scripts/join.sh down    # stop both
#   scripts/join.sh status  # both containers + health checks
#
# spec-fetcher comes up alongside join-api, not as a separate entry point:
# join-api's own SPEC_FETCHER_URL (docker-compose.yml) points at it, and a
# join-api with no reachable fetcher fails every request at
# backend_reachability (validate.py's SpecFetcherUnavailable, docs/
# production-delta.md row 41) -- "join-api is up" has meant "spec-fetcher is
# up too" since the row 41 fix landed.
#
# No `reset` subcommand: unlike the console, join-api has no ACL journal to
# reset (its later tasks add a job context instead, with its own recovery
# via resume, not reset).
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

JOIN_URL="http://${XROAD_BIND}:8091"

# The one thing join-api's /repo mount cannot carry on its own: in a git
# WORKTREE the checkout's .git is a file pointing at an absolute host path
# inside the MAIN checkout's .git, which the container has no mount for --
# see docker-compose.yml's join-api volumes. Exported here, resolved to an
# absolute path, and identical to the plain checkout's own .git when this is
# not a worktree.
if git_common_dir=$(cd "$PACK_DIR" && git rev-parse --git-common-dir 2>/dev/null); then
  KP2_GIT_COMMON_DIR=$(cd "$PACK_DIR" && cd "$git_common_dir" && pwd)
  export KP2_GIT_COMMON_DIR
fi
# --profile demo is additive to COMPOSE (lib-stack.sh); only join-api is ever
# targeted below, so this never starts or restarts the federation itself.
COMPOSE_DEMO=("${COMPOSE[@]}" --profile demo)

case "${1:-}" in
  up)
    # --wait: both have their own HEALTHCHECK (join-api mirrors console's;
    # spec-fetcher's is apps/spec-fetcher/Dockerfile's own), so "up" can
    # wait for both and actually mean "serving" instead of just "process
    # started". spec-fetcher first: join-api's own healthcheck is only
    # /health (it does not probe spec-fetcher), but the very first real
    # request would fail closed at backend_reachability if spec-fetcher
    # were not there yet, so bringing it up first (not just "also") means
    # join-api is never reported ready before the service it depends on is.
    # Timeout matches console.sh's own.
    "${COMPOSE_DEMO[@]}" up -d --build --wait --wait-timeout 30 spec-fetcher join-api
    log "join-api up at $JOIN_URL (spec-fetcher up alongside it) -- demo only, never production (docs/production-delta.md)"
    ;;
  down)
    "${COMPOSE_DEMO[@]}" stop join-api spec-fetcher
    log "join-api and spec-fetcher stopped"
    ;;
  status)
    "${COMPOSE_DEMO[@]}" ps join-api spec-fetcher
    curl -sf "$JOIN_URL/health" | python3 -m json.tool || fail "join-api not reachable at $JOIN_URL"
    # spec-fetcher has no host-bound port (docker-compose.yml's own comment
    # on why: nothing outside the Compose network should reach it) --
    # docker compose exec is the only way in, mirroring the acceptance
    # suite's own 2.7.2 segregation check.
    "${COMPOSE_DEMO[@]}" exec -T spec-fetcher \
      python3 -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health').read().decode())" \
      || fail "spec-fetcher not reachable (docker compose exec spec-fetcher)"
    ;;
  *)
    fail "usage: scripts/join.sh {up|down|status}"
    ;;
esac
