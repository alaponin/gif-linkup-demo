#!/usr/bin/env bash
# Stack helpers for the KP2 build-pack scripts. Sourced, not executed.
# Basis: NIIS X-Road admin REST API (:4000 /api/v1, API-key auth).
# [confirm at P0]: exact endpoint paths/payloads against the xrd-dev-stack Hurl files.
#
# Builds on lib-core.sh: .env sourcing, the credential refusal,
# deployment.yaml parsing, the public-exposure policy, topology
# generation/sourcing, COMPOSE/COMPOSE_ALL, and the api_key/api helpers.
# Safe to source only where Docker, .env and deployment.yaml are expected to
# be present -- unlike lib-core.sh, this file can exit.

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib-core.sh"

[ -f "$PACK_DIR/.env" ] && set -a && . "$PACK_DIR/.env" && set +a

# Refuse a .env that is missing, still a placeholder, or still one of the
# values this repo used to publish (docs/reviews/2026-07-28-branch-review.md
# finding S2) -- the Central Server's own fixed xrd/secret is a separate,
# unrotatable credential baked into the release image, never read from
# .env, and is not touched by this check.
for _cred_var in XROAD_TOKEN_PIN XROAD_ADMIN_PASSWORD; do
  case "${!_cred_var:-}" in
    ""|*CHANGEME*|Progressa123!|secret|Secret1234)
      echo "lib-stack.sh: $_cred_var is unset, a placeholder, or a value this repo
used to publish in .env.example. Run scripts/gen-secrets.sh to generate a
real .env (or scripts/gen-secrets.sh --force to replace an existing one --
read its warning about the software token's PIN first)." >&2
      exit 1
      ;;
  esac
done
unset _cred_var

# A correctly-formatted PIN can still be the WRONG one: every server's
# software token was initialised with whatever PIN was in .env the last
# time hurl/run-linkup.sh actually deployed (recorded there as a fingerprint,
# never the value, in out/.token-fingerprint). Changing .env afterwards does
# not change the token -- confirmed live (docs/xroad-770-notes.md §9) that
# the mismatch surfaces as Server.ClientProxy.SslAuthenticationFailed, which
# reads like a certificate problem, not a PIN one. Only refuse while the
# federation's own volumes still exist: teardown.sh --purge deletes them but
# not this host-side file, and a stale fingerprint must not block a
# legitimate fresh redeploy with a new .env.
TOKEN_FINGERPRINT="$PACK_DIR/out/.token-fingerprint"
if [ -f "$TOKEN_FINGERPRINT" ] && docker volume inspect kp2-cs-db >/dev/null 2>&1; then
  _current_fp=$(printf '%s' "$XROAD_TOKEN_PIN" | shasum -a 256 | cut -d' ' -f1)
  _stored_fp=$(cat "$TOKEN_FINGERPRINT")
  if [ "$_current_fp" != "$_stored_fp" ]; then
    echo "lib-stack.sh: .env's XROAD_TOKEN_PIN does not match the PIN this
federation's software token was initialised with. Changing .env alone does
not change the token -- the mismatch surfaces as X-Road errors that look
like certificate faults, not PIN errors (docs/xroad-770-notes.md §9).
Restore the original .env, or scripts/teardown.sh --purge and redeploy with
the new one." >&2
    exit 1
  fi
  unset _current_fp _stored_fp
fi

# deployment.yaml is the analyst-facing spec (topology profile, X-Road version
# pins); .env carries only secrets. See
# docs/superpowers/specs/2026-07-26-deployment-spec-and-lite-profile-design.md.
DEPLOY_SPEC="$PACK_DIR/deployment.yaml"
case "$(yq_get "$DEPLOY_SPEC" profile)" in
  lite) LITE=1 ;;
  full) LITE=0 ;;
  *) echo "lib-stack.sh: deployment.yaml profile must be 'full' or 'lite'" >&2; exit 1 ;;
esac
export XROAD_VERSION=$(yq_get "$DEPLOY_SPEC" xroad.version)
export XROAD_CS_TAG=$(yq_get "$DEPLOY_SPEC" xroad.cs_tag)
export TESTCA_TAG=$(yq_get "$DEPLOY_SPEC" xroad.testca_tag)
export XROAD_BIND=$(yq_get "$DEPLOY_SPEC" network.bind)
# A non-loopback bind publishes, with no authentication, the X-Road proxy
# ports (X-Road-Client is a self-asserted header, not a credential), the
# admin UIs, and the Test CA's signing endpoint -- see the message below.
# Two statements (bind + an explicit acknowledgement) rather than one, so
# this cannot be flipped by someone skimming deployment.yaml.
case "$XROAD_BIND" in
  127.0.0.1|::1|localhost) ;;
  *)
    ACK=$(yq_get "$DEPLOY_SPEC" network.acknowledge_public_exposure 2>/dev/null || echo false)
    if [ "$ACK" != "True" ]; then
      echo "lib-stack.sh: deployment.yaml sets network.bind=$XROAD_BIND without
network.acknowledge_public_exposure: true.

On a non-loopback interface this publishes, with no authentication:
  - the five Security Server :8080 proxy ports. X-Road's client-proxy
    interface has NO authentication -- the caller simply asserts who it is
    in the X-Road-Client header, because that interface is defined to sit on
    the agency's trusted internal network. Anyone who can reach it can
    impersonate any subsystem this server hosts.
  - the Central Server admin UI, whose credentials are fixed in the release
    image (xrd/secret) and cannot be rotated.
  - the Test CA, whose /testca/sign endpoint signs any CSR it is given.

If that is genuinely what you want, set acknowledge_public_exposure: true.
Otherwise leave bind at 127.0.0.1 and reach the stack over an SSH tunnel." >&2
      exit 1
    fi
    echo "lib-stack.sh: WARNING -- network.bind=$XROAD_BIND, acknowledged in deployment.yaml. This stack is reachable from outside this host with no authentication on its X-Road proxy ports." >&2
    ;;
esac

# One source of truth for topology (admin-UI port, REST port, stand-up order,
# which SS hosts which subsystem, and which joined members own a container) --
# generated once by hurl/generate.py (from configs/ + manifest.yaml +
# deployment.yaml's profile) into hurl/topology.sh (declares SS_UI, SS_REST,
# SS_ORDER and HOST_SS with exactly the lite/full-aware values this file used
# to hand-declare) and hurl/compose.members.yml (joined members' compose
# blocks, read below). apps/console reads the same generation run's
# hurl/topology.json -- one topology, not hand-kept copies. Must run before
# COMPOSE/COMPOSE_ALL are built: compose.members.yml has to exist before its
# `-f` flag can be added. ss-pnia's 5100/5180 (not 5000/5080, which collides
# with macOS's AirPlay Receiver) is pinned in hurl/generate.py's PINNED_PORTS
# table -- see its comment.
TOPOLOGY_SH="$PACK_DIR/hurl/topology.sh"
if [ ! -f "$TOPOLOGY_SH" ]; then
  ( cd "$PACK_DIR" && python3 hurl/generate.py >/dev/null )
fi
[ -f "$TOPOLOGY_SH" ] || { echo "lib-stack.sh: $TOPOLOGY_SH still missing after running generate.py" >&2; exit 1; }
. "$TOPOLOGY_SH"
# Regenerated only when missing, above -- a topology.sh left over from a
# different profile would otherwise be sourced silently. hurl/topology.json
# (same generation run) carries the profile it was built for; catch the
# mismatch here with a clear message rather than let SS_ORDER/HOST_SS be
# quietly wrong for whatever is about to run.
TOPO_JSON="$PACK_DIR/hurl/topology.json"
if [ -f "$TOPO_JSON" ]; then
  topo_profile=$(python3 -c "import json; print(json.load(open('$TOPO_JSON'))['profile'])")
  deploy_profile=$(yq_get "$DEPLOY_SPEC" profile)
  if [ "$topo_profile" != "$deploy_profile" ]; then
    echo "lib-stack.sh: hurl/topology.json was generated for profile '$topo_profile' but deployment.yaml now says '$deploy_profile' -- run python3 hurl/generate.py (hurl/run-linkup.sh does this for you) before continuing" >&2
    exit 1
  fi
fi

# Full topology by default; profile: lite (deployment.yaml) drops ss-pnia/
# ss-moeys (compose profile "full") and hosts their subsystems on ss-plr instead.
# hurl/compose.members.yml (generated above -- joined members that own their
# own Security Server) is added whenever it exists, to both arrays: a volume
# it defines can only be removed by a `down -v` that names this file too,
# same reason hurl/compose.hurl.yml is already in COMPOSE_ALL below.
COMPOSE_MEMBERS_YML="$PACK_DIR/hurl/compose.members.yml"
COMPOSE=(docker compose -f "$PACK_DIR/docker-compose.yml")
[ -f "$COMPOSE_MEMBERS_YML" ] && COMPOSE+=(-f "$COMPOSE_MEMBERS_YML")
[ "${LITE:-0}" != "1" ] && COMPOSE+=(--profile full)
# Teardown must always see every service, whatever LITE is set to now, AND
# every compose file that can have defined a volume -- hurl/compose.hurl.yml's
# ca-certs volume mounts at /home/ca/certs, a subpath of the base file's
# ca-data:/home/ca mount. Omitting it here left `down -v` unable to remove
# ca-certs, so a "purged" reset still handed a fresh CA container stale certs
# from the previous run (found at P0, 2026-07-25).
COMPOSE_ALL=(docker compose -f "$PACK_DIR/docker-compose.yml" -f "$PACK_DIR/hurl/compose.hurl.yml" --profile full)
[ -f "$COMPOSE_MEMBERS_YML" ] && COMPOSE_ALL+=(-f "$COMPOSE_MEMBERS_YML")

# The admin APIs authenticate by SESSION LOGIN, not by API key: POST /login with
# form params, keep the cookie jar, and send the XSRF-TOKEN cookie back as an
# X-XSRF-TOKEN header on every call. Verified against
# development/hurl/scenarios/setup.hurl at X-Road 7.7.0 — see
# docs/xroad-770-notes.md §1. (An earlier draft here used POST /api/v1/api-keys
# with basic auth. That was wrong and would have failed on the first call.)

# api_key <host:port> <user> <pass>  -> prints the path to a logged-in cookie jar.
# Kept under this name for call-site compatibility; it is a session handle.
api_key() {
  local hostport=$1 user=$2 pass=$3 jar
  jar=$(mktemp)
  curl -ksf -c "$jar" -X POST "https://${hostport}/login" \
    --data-urlencode "username=${user}" --data-urlencode "password=${pass}" \
    >/dev/null || { rm -f "$jar"; return 1; }
  printf '%s' "$jar"
}

# api <method> <host:port> <session-jar> <path> [json-body]
api() {
  local method=$1 hostport=$2 jar=$3 path=$4 body=${5:-} token
  token=$(awk '$6 == "XSRF-TOKEN" { print $7 }' "$jar")
  local args=(-ksf -b "$jar" -X "$method" "https://${hostport}/api/v1${path}" \
              -H "X-XSRF-TOKEN: ${token}" \
              -H 'Content-Type: application/json')
  [ -n "$body" ] && args+=(-d "$body")
  curl "${args[@]}"
}

# Exported so retry can be used from subshells if ever needed; acceptance.sh
# defines its checks as same-shell functions and does not depend on this.
export -f api_key api
