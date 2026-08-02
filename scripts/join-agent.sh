#!/usr/bin/env bash
# Stand up ONE joined member's own Security Server -- the demo's stand-in for
# the joining agency's own infrastructure team (design spec S6.1). Run it when
# the console shows a join request BLOCKED; the request leaves that state on
# its own resume, once join-api's poll finds this server answering.
#
#   scripts/join-agent.sh <key>     # e.g. scripts/join-agent.sh ptsb
#
# It holds no credentials of its own and drives no admin API. Everything it
# does is one `docker compose up` against a service block hurl/generate.py
# ALREADY wrote (hurl/compose.members.yml), healthcheck included -- so this
# adds no topology code, and --wait means "healthy", not "started".
#
# Demo only, never production (docs/production-delta.md): in a real
# federation this server is installed by the member, on the member's own
# hardware, with the member's own CA-issued certificates, and takes days.
set -euo pipefail
. "$(dirname "$0")/lib-stack.sh"

KEY="${1:-}"
[ -n "$KEY" ] || fail "usage: scripts/join-agent.sh <member-key>   (e.g. ptsb)"
case "$KEY" in
  *[!a-z0-9]*) fail "'$KEY' is not a member key -- keys are [a-z0-9]+ (apps/join-api/validate.py's key_derivation check)" ;;
esac

MEMBER_DIR="$PACK_DIR/configs/member-$KEY"
[ -d "$MEMBER_DIR" ] || fail "no $MEMBER_DIR -- 'scripts/member.sh list' shows the members this pack knows about"
CFG=$(find "$MEMBER_DIR" -maxdepth 1 -name '*.yaml' | sort | head -1)
[ -n "$CFG" ] || fail "$MEMBER_DIR has no *.yaml config"

SS=$(yq_get "$CFG" security_server.dns_name)
HOSTED=$(yq_get "$CFG" security_server.hosted_on 2>/dev/null || true)
[ -z "$HOSTED" ] || fail "member '$KEY' is hosted on $HOSTED and owns no Security Server -- a hosted join never enters BLOCKED and needs no agent (design spec S6.2)"

UI="${SS_UI[$SS]:-}"
REST="${SS_REST[$SS]:-}"
{ [ -n "$UI" ] && [ -n "$REST" ]; } || fail "hurl/topology.sh has no ports for $SS -- run 'python3 hurl/generate.py' first"

# A busy host port is a FAILURE, naming the port and the process holding it --
# never a silent re-allocation (design spec open question 6). Re-allocating
# would change the ports hurl/topology.json, hurl/topology.sh, the console's
# "copy as curl" and hurl/compose.members.yml all already agree on, for one
# machine, at run time: the Global Constraint's determinism (same member set,
# same allocation, always) is worth more than saving the operator one `kill`.
# generate.py already refuses the AirPlay range (5000-5099, 7000) by
# construction; this is the check for everything else on this machine.
if [ -z "$(docker ps -q -f "name=^${SS}$")" ]; then
  if command -v lsof >/dev/null 2>&1; then
    for port in "$UI" "$REST"; do
      # `|| true`: lsof exits 1 when nothing holds the port, which under
      # `set -euo pipefail` would otherwise kill this script SILENTLY on the
      # happy path (found while testing this exact line).
      holder=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1" (pid "$2", user "$3")"; exit}') || true
      [ -z "$holder" ] || fail "host port $port -- allocated to $SS by hurl/generate.py -- is already in use by $holder. Free it and re-run; this script will not re-allocate the port, because every other file in the pack already names it."
    done
  else
    log "lsof not found -- skipping the host-port check; a busy port will surface as a compose bind error instead"
  fi
fi

log "bringing up $SS (admin UI :$UI, proxy :$REST) -- a cold Security Server image takes a few minutes to become healthy"
"${COMPOSE[@]}" up -d --wait --wait-timeout "${JOIN_AGENT_WAIT:-600}" "$SS"
log "$SS is healthy -- resume the BLOCKED join request (the console's Resume button, or POST /requests/<id>/resume)"
