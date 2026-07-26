#!/usr/bin/env bash
# Shared helpers for the KP2 build-pack scripts. Sourced, not executed.
# Basis: NIIS X-Road admin REST API (:4000 /api/v1, API-key auth).
# [confirm at P0]: exact endpoint paths/payloads against the xrd-dev-stack Hurl files.

PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$PACK_DIR/.env" ] && set -a && . "$PACK_DIR/.env" && set +a
export PACK_DIR

# yq wrapper (python fallback: hard deps stay curl+jq+python3). Defined here,
# ahead of its first use below, because deployment.yaml is now read before
# COMPOSE is built. Clean error on a missing key instead of a traceback.
yq_get() { python3 -c "
import sys, yaml
try:
    doc = yaml.safe_load(open('$1'))
    node = doc
    for part in '$2'.split('.'):
        node = node[int(part)] if part.isdigit() else node[part]
    print(node)
except (KeyError, IndexError, TypeError):
    sys.exit('yq_get: no key \\'$2\\' in $1')
"; }

# deployment.yaml is the analyst-facing spec (topology profile, X-Road version
# pins); .env carries only secrets. See
# docs/superpowers/specs/2026-07-26-deployment-spec-and-lite-profile-design.md.
DEPLOY_SPEC="$PACK_DIR/deployment.yaml"
case "$(yq_get "$DEPLOY_SPEC" profile)" in
  lite) LITE=1 ;;
  full) LITE=0 ;;
  *) echo "lib.sh: deployment.yaml profile must be 'full' or 'lite'" >&2; exit 1 ;;
esac
export XROAD_VERSION=$(yq_get "$DEPLOY_SPEC" xroad.version)
export XROAD_CS_TAG=$(yq_get "$DEPLOY_SPEC" xroad.cs_tag)
export TESTCA_TAG=$(yq_get "$DEPLOY_SPEC" xroad.testca_tag)

# Full topology by default; profile: lite (deployment.yaml) drops ss-pnia/
# ss-moeys (compose profile "full") and hosts their subsystems on ss-plr instead.
COMPOSE=(docker compose -f "$PACK_DIR/docker-compose.yml")
[ "${LITE:-0}" != "1" ] && COMPOSE+=(--profile full)
# Teardown must always see every service, whatever LITE is set to now, AND
# every compose file that can have defined a volume -- hurl/compose.hurl.yml's
# ca-certs volume mounts at /home/ca/certs, a subpath of the base file's
# ca-data:/home/ca mount. Omitting it here left `down -v` unable to remove
# ca-certs, so a "purged" reset still handed a fresh CA container stale certs
# from the previous run (found at P0, 2026-07-25).
COMPOSE_ALL=(docker compose -f "$PACK_DIR/docker-compose.yml" -f "$PACK_DIR/hurl/compose.hurl.yml" --profile full)

# One source of truth for topology (admin-UI port, REST port, stand-up order,
# which SS hosts which subsystem). acceptance.sh/deploy.sh must not re-derive these.
declare -A SS_UI=( [ss-pdga]=1000 [ss-pnea]=2000 [ss-plr]=3000 [ss-pnia]=5100 [ss-moeys]=6000 )
declare -A SS_REST=( [ss-pdga]=1080 [ss-pnea]=2080 [ss-plr]=3080 [ss-pnia]=5180 [ss-moeys]=6080 )
# ss-pnia is 5100/5180, not 5000/5080: port 5000 collides with macOS's AirPlay
# Receiver (ControlCenter), which hangs the connection instead of refusing it.
# See docker-compose.yml's ss-pnia comment. Confirmed at P0, 2026-07-25.
SS_ORDER=(ss-pdga ss-pnea ss-plr ss-pnia ss-moeys)   # management SS first
declare -A HOST_SS=( [PDGA:MANAGEMENT]=ss-pdga [PNEA:EXAMS]=ss-pnea \
                     [PLR:ENROLMENT]=ss-plr [PNIA:IDENTITY]=ss-pnia [MOEYS:PEMIS]=ss-moeys )
if [ "${LITE:-0}" = "1" ]; then
  SS_ORDER=(ss-pdga ss-pnea ss-plr)
  HOST_SS[PNIA:IDENTITY]=ss-plr
  HOST_SS[MOEYS:PEMIS]=ss-plr
fi

log()  { printf '\033[1;34m[kp2]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[kp2 FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

# retry <tries> <sleep_s> <description> <command...>
# Global-conf propagation and service start-up take minutes; retrying is normal
# (xrd-dev-stack's own init "gets HTTP errors and keeps retrying").
retry() {
  local tries=$1 sleep_s=$2 desc=$3; shift 3
  local i
  for ((i=1; i<=tries; i++)); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    log "waiting: $desc ($i/$tries)"; sleep "$sleep_s"
  done
  fail "timed out: $desc"
}

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
export -f log fail retry api_key api
