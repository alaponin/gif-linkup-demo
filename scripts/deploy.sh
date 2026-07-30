#!/usr/bin/env bash
# Deploy the KP2 pack: bring up the Linkup demonstration federation and apply
# every config in configs/ over the Central Server and Security Server admin
# REST APIs.
#
# v0.3: this script is now a thin wrapper. The federation is stood up by the
# Hurl scenario set in hurl/, which is a Progressa retargeting of
# development/hurl/scenarios/setup.hurl at X-Road tag 7.7.0 -- the sanctioned
# config-as-code path and the reference implementation of the call sequence.
# The bespoke admin-API bash that used to live here is superseded: it carried
# seven [confirm P0] markers, and the reference resolves every one of them (the
# API-key bootstrap, in particular, was never going to work -- the admin APIs
# authenticate by session login and XSRF token). See docs/xroad-770-notes.md
# and PLAN.md decision 3.
#
#   scripts/deploy.sh            # stand the federation up
#   scripts/deploy.sh --dry-run  # build the concatenated run file, execute nothing
#
# Then: scripts/seed.sh, scripts/acceptance.sh. Proving the pack is acceptance.sh's
# job, not this one -- it owns module 2.6's four assertions, two of which are
# beyond what a Hurl scenario can assert.
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

log "delegating to hurl/run-linkup.sh (X-Road 7.7.0 admin APIs)"
exec "$PACK_DIR/hurl/run-linkup.sh" "${1:---setup}"
