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
# values this repo used to publish (docs/notes/reviews/2026-07-28-branch-review.md
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

# The PIN-fingerprint guard (a correctly-formatted PIN can still be the WRONG
# one -- every server's software token was initialised with whatever PIN was
# in .env the last time hurl/run-linkup.sh actually deployed) used to run
# here, at source time. That meant scripts with no reason to care --
# console.sh status, member.sh list -- paid for a `docker volume inspect`
# just because they happen to source this file. It is a deploy-time check,
# so it now lives as check_token_fingerprint() in hurl/run-linkup.sh, called
# immediately before that script brings containers up (and so covers
# scripts/deploy.sh, which execs into it).

# deployment.yaml is the analyst-facing spec (X-Road version pins, network
# bind); .env carries only secrets.
DEPLOY_SPEC="$PACK_DIR/deployment.yaml"
export XROAD_VERSION=$(yq_get "$DEPLOY_SPEC" xroad.version)
export XROAD_CS_TAG=$(yq_get "$DEPLOY_SPEC" xroad.cs_tag)
export TESTCA_TAG=$(yq_get "$DEPLOY_SPEC" xroad.testca_tag)
# Digest pins -- docker-compose.yml prefers these over XROAD_CS_TAG/
# XROAD_VERSION when set: an image tag can move to a different image later,
# a digest cannot, which is what a reproducible deploy needs.
export XROAD_CS_DIGEST=$(yq_get "$DEPLOY_SPEC" xroad.cs_digest)
export XROAD_SS_DIGEST=$(yq_get "$DEPLOY_SPEC" xroad.ss_digest)
export XROAD_BIND=$(yq_get "$DEPLOY_SPEC" network.bind)
# A non-loopback bind publishes, with no authentication, the X-Road proxy
# ports (X-Road-Client is a self-asserted header, not a credential), the
# admin UIs, and the Test CA's signing endpoint -- see the message below.
# Two statements (bind + an explicit acknowledgement) rather than one, so
# this cannot be flipped by someone skimming deployment.yaml.
case "$XROAD_BIND" in
  127.0.0.1|::1|localhost) ;;
  *)
    # The Test CA cannot be acknowledged onto a public bind -- unlike the
    # rest of this exposure, there is no legitimate reason to want it there.
    CA_IN_COMPOSE=$(python3 - "$PACK_DIR/docker-compose.yml" <<'PY'
import sys, yaml
print('ca' in (yaml.safe_load(open(sys.argv[1])).get('services') or {}))
PY
)
    if [ "$CA_IN_COMPOSE" = "True" ]; then
      echo "lib-stack.sh: deployment.yaml sets network.bind=$XROAD_BIND with the Test
CA (service \"ca\") still part of this stack. Refused -- no
acknowledge_public_exposure setting can override this one.

The xrddev-testca image's /testca/sign endpoint signs any CSR it is handed,
with no authentication. On a non-loopback interface that turns the
federation's own trust anchor into a public certificate factory: anyone who
can reach the endpoint can mint a certificate this federation will accept as
a member's identity.

Replace the Test CA with an accredited CA before binding to anything but
loopback -- see docs/deployment-targets.md." >&2
      exit 1
    fi
    ACK=$(yq_get "$DEPLOY_SPEC" network.acknowledge_public_exposure 2>/dev/null || echo false)
    if [ "$ACK" != "True" ]; then
      echo "lib-stack.sh: deployment.yaml sets network.bind=$XROAD_BIND without
network.acknowledge_public_exposure: true.

On a non-loopback interface this publishes, with no authentication:
  - the four Security Server :8080 proxy ports. X-Road's client-proxy
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
# generated once by hurl/generate.py (from configs/ + manifest.yaml) into
# hurl/topology.sh (declares SS_UI, SS_REST, SS_ORDER and HOST_SS with
# exactly the values this file used to hand-declare) and
# hurl/compose.members.yml (joined members' compose blocks, read below).
# apps/console reads the same generation run's hurl/topology.json -- one
# topology, not hand-kept copies. Must run before COMPOSE/COMPOSE_ALL are
# built: compose.members.yml has to exist before its `-f` flag can be added.
# ss-pnia's 5100/5180 (not 5000/5080, which collides with macOS's AirPlay
# Receiver) is pinned in hurl/generate.py's PINNED_PORTS table -- see its
# comment.
TOPOLOGY_SH="$PACK_DIR/hurl/topology.sh"
if [ ! -f "$TOPOLOGY_SH" ]; then
  ( cd "$PACK_DIR" && python3 hurl/generate.py >/dev/null )
fi
[ -f "$TOPOLOGY_SH" ] || { echo "lib-stack.sh: $TOPOLOGY_SH still missing after running generate.py" >&2; exit 1; }
. "$TOPOLOGY_SH"

# hurl/compose.members.yml (generated above -- joined members that own their
# own Security Server) is added whenever it exists, to both arrays: a volume
# it defines can only be removed by a `down -v` that names this file too,
# same reason hurl/compose.hurl.yml is already in COMPOSE_ALL below. Every
# service in docker-compose.yml that belongs to the federation itself (not
# the demo-only console/join-api, tagged profiles: ["demo"]) is unconditional
# now that "full" is the only topology -- no --profile flag is needed to
# bring ss-pnia up or to tear it down, unlike when it was gated behind
# profiles: ["full"].
COMPOSE_MEMBERS_YML="$PACK_DIR/hurl/compose.members.yml"
COMPOSE=(docker compose -f "$PACK_DIR/docker-compose.yml")
[ -f "$COMPOSE_MEMBERS_YML" ] && COMPOSE+=(-f "$COMPOSE_MEMBERS_YML")
# Teardown must see every service, and every compose file that can have
# defined a volume -- hurl/compose.hurl.yml's ca-certs volume mounts at
# /home/ca/certs, a subpath of the base file's ca-data:/home/ca mount.
# Omitting it here left `down -v` unable to remove ca-certs, so a "purged"
# reset still handed a fresh CA container stale certs from the previous run
# (found at P0).
COMPOSE_ALL=(docker compose -f "$PACK_DIR/docker-compose.yml" -f "$PACK_DIR/hurl/compose.hurl.yml")
[ -f "$COMPOSE_MEMBERS_YML" ] && COMPOSE_ALL+=(-f "$COMPOSE_MEMBERS_YML")

# The admin APIs authenticate by SESSION LOGIN, not by API key: POST /login with
# form params, keep the cookie jar, and send the XSRF-TOKEN cookie back as an
# X-XSRF-TOKEN header on every call. Verified against
# development/hurl/scenarios/setup.hurl at X-Road 7.7.0 — see
# docs/decisions/xroad-770-notes.md §1. (An earlier draft here used POST /api/v1/api-keys
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
