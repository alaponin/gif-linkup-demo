#!/usr/bin/env bash
# Stack helpers for the KP2 build-pack scripts. Sourced, not executed.
# Basis: NIIS X-Road admin REST API (:4000 /api/v1, API-key auth).
# [confirm at P0]: exact endpoint paths/payloads against the xrd-dev-stack Hurl files.
#
# Builds on lib-core.sh: .env loading, the credential refusal,
# deployment.yaml parsing, the public-exposure policy, topology
# generation/loading, COMPOSE/COMPOSE_ALL, and the api_key/api helpers.
# Neither .env nor the topology is SOURCED any more -- both are parsed as
# data (lib-core.sh's kp2_load_env/kp2_load_topology).
# Safe to source only where Docker, .env and deployment.yaml are expected to
# be present -- unlike lib-core.sh, this file can exit.

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib-core.sh"

# PARSED, never sourced -- kp2_load_env's own comment in lib-core.sh has the
# reason: a sourced .env is arbitrary shell, and this file is writable by
# whoever can edit the checkout. This used to be
# `set -a && . "$PACK_DIR/.env" && set +a`, which executed the file.
kp2_load_env "$PACK_DIR/.env"

# Refuse a .env that is missing, still a placeholder, or still one of the
# values this repo used to publish -- the Central Server's own fixed
# xrd/secret is a separate,
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
#
# KP2_DEPLOY_SPEC overrides the path -- tests only (tests/test_tiers.py),
# same override scripts/check-exposure.sh already has and for the same
# reason: exercise a refusal against a throwaway deployment.yaml without
# touching the real one.
DEPLOY_SPEC="${KP2_DEPLOY_SPEC:-$PACK_DIR/deployment.yaml}"
export XROAD_VERSION=$(yq_get "$DEPLOY_SPEC" xroad.version)
export XROAD_CS_TAG=$(yq_get "$DEPLOY_SPEC" xroad.cs_tag)
export TESTCA_TAG=$(yq_get "$DEPLOY_SPEC" xroad.testca_tag)
# Digest pins -- docker-compose.yml prefers these over XROAD_CS_TAG/
# XROAD_VERSION when set: an image tag can move to a different image later,
# a digest cannot, which is what a reproducible deploy needs.
export XROAD_CS_DIGEST=$(yq_get "$DEPLOY_SPEC" xroad.cs_digest)
export XROAD_SS_DIGEST=$(yq_get "$DEPLOY_SPEC" xroad.ss_digest)
export XROAD_BIND=$(yq_get "$DEPLOY_SPEC" network.bind)
POSTURE=$(yq_get "$DEPLOY_SPEC" posture 2>/dev/null || echo demo)

# Both demo images run as `nobody` (their Dockerfiles), but console mounts
# ./out and join-api mounts the whole checkout READ-WRITE -- and on the droplet
# that tree is root-owned, because the workflow rsyncs it with --chown=root:root
# to satisfy git's dubious-ownership check. A `nobody` process cannot write a
# root-owned tree: join-api died on its first mkdir of out/join, and the
# console's ACL journal would have died the same way on the first revoke.
# Docker Desktop's macOS bind mounts virtualise ownership, which is why neither
# ever failed on a laptop or in --fast. So those two services run as whoever
# runs this script -- root on the droplet, the developer on a laptop -- which
# is in both cases the owner of the checkout they write to.
# ...on a laptop. On the droplet those two containers run as a dedicated
# unprivileged identity instead -- `kp2`, uid/gid 10001, created by
# infra/terraform/cloud-init.yaml, which infra/ci/remote-deploy.sh also
# chowns the handful of writable paths to. Fixed ids because they
# have to mean the same thing on both sides of a bind mount.
#
# RESOLVED FROM THE HOST, not from an exported variable. An earlier version of
# this change relied on infra/ci/remote-deploy.sh exporting
# KP2_CONTAINER_UID -- and that script never starts these two containers.
# infra/ci/console-publish.sh does, in its OWN ssh session, which never saw
# the export, so on every normal deploy both came up as UID 0 anyway and
# stayed there (`restart: unless-stopped`). At UID 0 the ownership and
# sticky-bit backstop is worth nothing -- CAP_DAC_OVERRIDE/CAP_FOWNER go
# straight through it, and this pack sets no cap_drop, security_opt or
# userns_mode -- so the whole chain stayed wide open at root regardless of any
# chown done elsewhere.
#
# So: if this host has the account, use it, whoever is running this script --
# CI, console-publish.sh, or an operator by hand. Nothing has to remember an
# export, which is the only version of this that cannot be forgotten once.
# `id -u kp2` rather than a hardcoded 10001: the ACCOUNT is the contract, and
# a wrong id here would be a loud "cannot write", never a quiet root.
# KP2_CONTAINER_UID still wins when set, and a laptop has no kp2 account, so
# there it resolves to `id -u` exactly as before.
if [ -z "${KP2_CONTAINER_UID:-}" ] && _kp2_uid=$(id -u kp2 2>/dev/null); then
  KP2_CONTAINER_UID=$_kp2_uid
  KP2_CONTAINER_GID=$(id -g kp2)
  unset _kp2_uid
fi
export KP2_HOST_UID=${KP2_CONTAINER_UID:-$(id -u)}
export KP2_HOST_GID=${KP2_CONTAINER_GID:-$(id -g)}
if [ "$KP2_HOST_UID" = 0 ]; then
  echo "lib-stack.sh: WARNING -- the console and join-api containers will run as
UID 0 against their bind mounts, which lets a compromised process in either
one write anywhere on the bind-mounted checkout, not just the paths it
actually needs. This host has no
`kp2` account and KP2_CONTAINER_UID is unset. On a droplet, run
infra/ci/remote-deploy.sh (it creates the account and chowns the writable
set) before bringing either container up." >&2
fi
# A non-loopback bind publishes, with no authentication, the X-Road proxy
# ports (X-Road-Client is a self-asserted header, not a credential), the
# admin UIs, and the Test CA's signing endpoint -- see the message below.
# Two statements (bind + an explicit acknowledgement) rather than one, so
# this cannot be flipped by someone skimming deployment.yaml.
case "$XROAD_BIND" in
  127.0.0.1|::1|localhost) ;;
  *)
    # posture: production cannot be acknowledged onto a public bind either --
    # a production deployment's public surface is nginx on 443
    # (infra/ci/console-publish.sh), never a published compose port, so there
    # is no legitimate shape where both are true. Checked first, and before
    # the ordinary acknowledge_public_exposure escape below: this refusal has
    # no escape at all, same as the Test CA rule immediately after it.
    if [ "$POSTURE" = "production" ]; then
      echo "lib-stack.sh: deployment.yaml sets posture: production with
network.bind=$XROAD_BIND. Refused -- no acknowledge_public_exposure setting
can override this one.

A production deployment's public surface is nginx on 443
(infra/ci/console-publish.sh), never a published compose port. Reach this
stack over an SSH tunnel or a VPN instead, or drop posture: production if
this really is a demo host." >&2
      exit 1
    fi
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
# hurl/topology.json and hurl/compose.members.yml (joined members' compose
# blocks, read below). apps/console reads the same file -- one topology, not
# hand-kept copies. Must run before COMPOSE/COMPOSE_ALL are built:
# compose.members.yml has to exist before its `-f` flag can be added.
# ss-pnia's 5100/5180 (not 5000/5080, which collides with macOS's AirPlay
# Receiver) is pinned in hurl/generate.py's PINNED_PORTS table -- see its
# comment.
#
# PARSED from topology.json, not sourced from the topology.sh generate.py
# still writes beside it: join-api can write hurl/, and `. hurl/topology.sh`
# would execute whatever it found there as root. See kp2_load_topology in
# lib-core.sh, including the one pair it deliberately does not carry over.
TOPOLOGY_JSON="$PACK_DIR/hurl/topology.json"
if [ ! -f "$TOPOLOGY_JSON" ]; then
  ( cd "$PACK_DIR" && python3 -B hurl/generate.py >/dev/null )
  # Its own message: kp2_load_topology's missing-file refusal says "run
  # generate.py first", which is the wrong advice on the one path where it
  # has just run and produced nothing.
  [ -f "$TOPOLOGY_JSON" ] || { echo "lib-stack.sh: $TOPOLOGY_JSON still missing after running hurl/generate.py" >&2; exit 1; }
fi
kp2_load_topology "$TOPOLOGY_JSON"

# The Test CA's PUBLIC certificate, on the host, for a caller that has to
# verify a TLS hop into the federation -- the consumer's client proxy on
# :8443 once its connection_type is HTTPS/HTTPS_NO_AUTH
# (docs/production-delta.md row 19). Fetched from the running `ca`
# container's own distribution point (nginx serves /home/ca/CA/certs) and
# cached under out/, which is gitignored and purged with the stack. Echoes
# the path; returns non-zero, silently, if the CA is not up -- the caller
# decides whether that is fatal, since a federation whose clients are all
# plain HTTP never needs this at all.
#
# DEMO ONLY, and the reason this is a download rather than a pinned file: a
# real deployment's trust anchor is distributed out of band, not fetched
# over plain HTTP from the CA it is supposed to authenticate.
testca_bundle() {
  local out="$PACK_DIR/out/testca/ca.pem"
  if [ ! -s "$out" ]; then
    mkdir -p "$(dirname "$out")"
    curl -fsS --max-time 10 "http://${XROAD_BIND}:8888/testca/certs/ca.cert.pem" -o "$out" \
      2>/dev/null || { rm -f "$out"; return 1; }
  fi
  printf '%s\n' "$out"
}

# TOFU-pin ONE admin host's :4000 certificate into out/xroad-admin-certs/<name>.pem -- the source
# _admin_curl_opts() and every Python caller (apps/console/xroad.py,
# scripts/member.sh) pin against. Shared here, not copied, because it has
# TWO callers with different lifecycles: hurl/run-linkup.sh captures the
# four canonical servers (and cs) right after a cold deploy, and
# scripts/join-agent.sh captures a NEWLY JOINED member's own server right
# after ITS `--wait` returns -- a server that did not exist at the last
# run-linkup.sh run and would otherwise stay permanently unpinned (every
# caller falling back to unverified TLS for it, silently until this
# change's own warnings). Found in review.
#
# A capture failure is a WARNING, not a script failure: the caller-side
# fallback (verify=False / curl -k alone, now all logged) is exactly
# today's behaviour, and a server that is healthy per Docker but whose TLS
# listener is not yet answering must not block whatever brought it up.
_capture_admin_cert() {
  local name="$1" port="$2" out
  out="$PACK_DIR/out/xroad-admin-certs/${name}.pem"
  mkdir -p "$(dirname "$out")"
  if ! { echo | openssl s_client -connect "${XROAD_BIND}:${port}" -servername "$name" 2>/dev/null \
       | openssl x509 -outform PEM > "${out}.tmp" 2>/dev/null; } || [ ! -s "${out}.tmp" ]; then
    rm -f "${out}.tmp"
    echo "$(basename "$0"): WARNING -- could not capture ${name}'s :4000 admin certificate ($XROAD_BIND:$port); callers fall back to unverified TLS for this host until the next successful capture." >&2
    return 0
  fi
  mv "${out}.tmp" "$out"
}

# Sets REST_BASE and the REST_OPTS curl-argument array for reaching one
# subsystem's OWN Security Server as that subsystem's information system.
# $1 is a MEMBER:SUBSYSTEM pair, the same key HOST_SS and CLIENT_CONN use.
#
# This is the one place that decides plain vs TLS, so no caller has to.
# HTTP -> the published :8080 mapping, exactly as before. Anything else ->
# the :8443 mapping, addressed by the server's DNS NAME rather than by
# ${XROAD_BIND}: the Security Server's internal TLS certificate is issued
# for that name (hurl's ss.internal_tls_cert step), so a URL naming a
# loopback ADDRESS would fail hostname verification. --resolve keeps the
# connection on loopback while letting the name be the one verified, which
# is exactly what --resolve is for -- and is why this is not `-k`.
rest_base() {
  local pair="$1" ss conn port
  ss=${HOST_SS[$pair]:-}
  conn=${CLIENT_CONN[$pair]:-HTTP}
  REST_OPTS=()
  if [ "$conn" = "HTTP" ]; then
    REST_BASE="http://${XROAD_BIND}:${SS_REST[$ss]}"
    return 0
  fi
  port=${SS_REST_TLS[$ss]}
  REST_BASE="https://${ss}:${port}"
  local bundle
  bundle=$(testca_bundle) || {
    echo "lib-stack.sh: $pair is $conn but the Test CA bundle could not be fetched -- is the stack up?" >&2
    return 1
  }
  REST_OPTS=(--cacert "$bundle" --resolve "${ss}:${port}:${XROAD_BIND}")
}

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

# Reverse of SS_UI:
# api_key()/api() only ever receive XROAD_BIND:port, never a dns name --
# every call site inherited that shape from when curl ran with plain -k and
# no host distinction mattered. Recovering the dns name from the one
# topology array that already maps it the other way is cheaper than
# changing every call site's signature for a fact this file already knows.
# The Central Server's admin port is fixed at 4000 and is not a member of
# SS_UI (it is not a Security Server) -- acceptance.sh's own
# `api_key ${XROAD_BIND}:4000 xrd secret` is the one caller that hits it.
_admin_host_for_port() {
  local port="$1" ss
  if [ "$port" = 4000 ]; then
    printf 'cs'
    return 0
  fi
  for ss in "${!SS_UI[@]}"; do
    if [ "${SS_UI[$ss]}" = "$port" ]; then
      printf '%s' "$ss"
      return 0
    fi
  done
  return 1
}

# TOFU pin for one admin host's :4000. hurl/run-linkup.sh captures each
# server's own certificate at deploy time into
# out/xroad-admin-certs/<host>.pem. curl has no CLI-only
# flag for "verify the chain but skip the hostname" -- the pinned
# certificate's CN/SAN name the container's own runtime hostname, never
# XROAD_BIND, exactly the reason apps/console/xroad.py's
# _admin_ssl_context() runs with check_hostname=False rather than a
# --resolve trick. --pinnedpubkey is curl's equivalent: -k skips the
# (impossible, for a self-signed leaf) chain and hostname check, and
# --pinnedpubkey adds back a real one -- the SHA-256 hash of the CAPTURED
# certificate's public key must match what the server presents on this
# call, not merely "some self-signed certificate" (verified live: a wrong
# hash is curl exit 90, "SSL: public key does not match pinned public
# key"). Sets _ADMIN_CURL_OPTS, the same array-output convention
# rest_base() already uses for REST_OPTS. No captured certificate for this
# host (not yet deployed through run-linkup.sh, or a caller -- like
# capture-xroad-fixtures.sh's very first call -- that runs before this
# phase existed) falls back to -k alone, the old behaviour.
_admin_curl_opts() {
  local admin_host="$1" pem hash
  _ADMIN_CURL_OPTS=(-k)
  if [ -z "$admin_host" ]; then
    echo "lib-stack.sh: no admin host resolved for this call -- falling back to unverified TLS (-k alone) for it." >&2
    return 0
  fi
  pem="$PACK_DIR/out/xroad-admin-certs/${admin_host}.pem"
  if [ ! -f "$pem" ]; then
    echo "lib-stack.sh: no pinned certificate for admin host '$admin_host' ($pem) -- falling back to unverified TLS (-k alone) for it. Run hurl/run-linkup.sh to capture it." >&2
    return 0
  fi
  # The whole pipeline's success is tested by an `if`, never a bare
  # statement or a trailing `[ -n "$hash" ] && ...`: under the caller's
  # `set -e`, a failing openssl stage (an unreadable or malformed pem --
  # the exact case this function exists to fall back gracefully from) would
  # otherwise abort the WHOLE CALLING SCRIPT instead of just falling back
  # to -k alone, and so would this function returning non-zero when $hash
  # comes back empty -- api_key()/api() call this as a bare statement, not
  # inside an `if`. Found in review.
  #
  # `set -o pipefail` scoped to the inner subshell, not inherited from the
  # caller: without it, `openssl x509` failing (e.g. a truncated pem) still
  # lets `dgst`/`base64` run on EMPTY input and print the SHA-256 of zero
  # bytes -- a real, non-empty-looking hash that is not this certificate's
  # key at all (found live: a corrupt fixture produced
  # 47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=, the well-known empty-input
  # digest, and `[ -n "$hash" ]` alone did not catch it).
  if hash=$( (set -o pipefail; openssl x509 -in "$pem" -pubkey -noout \
      | openssl pkey -pubin -outform der \
      | openssl dgst -sha256 -binary | base64) 2>/dev/null) && [ -n "$hash" ]; then
    _ADMIN_CURL_OPTS+=(--pinnedpubkey "sha256//$hash")
  else
    echo "lib-stack.sh: could not compute a public-key pin from $pem (unreadable or malformed) -- falling back to unverified TLS (-k alone) for '$admin_host'." >&2
  fi
  return 0
}

# api_key <host:port> <user> <pass>  -> prints the path to a logged-in cookie jar.
# Kept under this name for call-site compatibility; it is a session handle.
api_key() {
  local hostport=$1 user=$2 pass=$3 jar admin_host
  admin_host=$(_admin_host_for_port "${hostport##*:}") || admin_host=""
  _admin_curl_opts "$admin_host"
  jar=$(mktemp)
  curl -sf "${_ADMIN_CURL_OPTS[@]}" -c "$jar" -X POST "https://${hostport}/login" \
    --data-urlencode "username=${user}" --data-urlencode "password=${pass}" \
    >/dev/null || { rm -f "$jar"; return 1; }
  printf '%s' "$jar"
}

# api <method> <host:port> <session-jar> <path> [json-body]
api() {
  local method=$1 hostport=$2 jar=$3 path=$4 body=${5:-} token admin_host
  token=$(awk '$6 == "XSRF-TOKEN" { print $7 }' "$jar")
  admin_host=$(_admin_host_for_port "${hostport##*:}") || admin_host=""
  _admin_curl_opts "$admin_host"
  local args=("${_ADMIN_CURL_OPTS[@]}" -sf -b "$jar" -X "$method" "https://${hostport}/api/v1${path}" \
              -H "X-XSRF-TOKEN: ${token}" \
              -H 'Content-Type: application/json')
  [ -n "$body" ] && args+=(-d "$body")
  curl "${args[@]}"
}

# Exported so retry can be used from subshells if ever needed; acceptance.sh
# defines its checks as same-shell functions and does not depend on this.
# _admin_host_for_port/_admin_curl_opts must travel with api_key/api -- both
# call them internally. _capture_admin_cert travels with both its callers
# (hurl/run-linkup.sh, scripts/join-agent.sh) for the same reason.
export -f api_key api _admin_host_for_port _admin_curl_opts _capture_admin_cert
