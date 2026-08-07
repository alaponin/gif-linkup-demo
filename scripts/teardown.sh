#!/usr/bin/env bash
# Reset the KP2 slice. Default: stop containers, keep volumes (federation config
# persists). --purge: delete volumes too — full reset to zero. The P5
# reproducibility proof is: --purge, deploy, seed, acceptance green.
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

# COMPOSE_ALL (lib-stack.sh) covers every federation service unconditionally --
# no profile flag needed now that there is only one topology (design decision 5).
if [ "${1:-}" = "--purge" ]; then
  log "purging: containers + volumes (full reset)"
  "${COMPOSE_ALL[@]}" down -v
else
  log "stopping containers (volumes kept — rerun deploy.sh to resume)"
  "${COMPOSE_ALL[@]}" down
fi
