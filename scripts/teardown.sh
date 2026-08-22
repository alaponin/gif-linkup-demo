#!/usr/bin/env bash
# Reset the KP2 slice. Default: stop containers, keep volumes (federation config
# persists). --purge: delete volumes too — full reset to zero. The P5
# reproducibility proof is: --purge, deploy, seed, acceptance green.
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

# COMPOSE_ALL (lib-stack.sh) covers every federation service unconditionally --
# no profile flag needed now that there is only one topology.
if [ "${1:-}" = "--purge" ]; then
  log "purging: containers + volumes (full reset)"
  "${COMPOSE_ALL[@]}" down -v
  # `down -v` destroys ca-data, and with it the Test CA's private key -- the
  # next deploy generates a BRAND NEW CA. lib-stack.sh's testca_bundle()
  # caches the CA's public certificate under out/testca/ and only re-fetches
  # it when the file is missing, so leaving it behind hands every TLS caller
  # (rest_base(), member.sh, ...) the PREVIOUS CA's anchor and every
  # verification fails on the second `verify.sh --full` of a purge cycle.
  # Cache lifetime has to match the key's: purge one, purge the other.
  # Scoped to out/testca deliberately -- the join store elsewhere under out/
  # survives a purge on purpose (see scripts/acceptance.sh's 2.7.unjoin
  # comment for why).
  log "purging: out/testca (cached Test CA anchor -- the CA it belongs to is gone)"
  rm -rf "$PACK_DIR/out/testca"
else
  log "stopping containers (volumes kept — rerun deploy.sh to resume)"
  "${COMPOSE_ALL[@]}" down
fi
