#!/usr/bin/env bash
# Core helpers for the KP2 build-pack scripts. Sourced, not executed.
# Safe to source from anywhere: no Docker, no .env, no deployment.yaml, no
# exit. Anything that needs to refuse belongs in lib-stack.sh or the script
# that cares.

PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PACK_DIR

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

# yq wrapper (python fallback: hard deps stay curl+jq+python3). Clean error
# on a missing key instead of a traceback. Values arrive via argv, never
# spliced into the program text -- a path or key containing a quote must not
# become a Python SyntaxError or worse.
yq_get() { python3 - "$1" "$2" <<'PY'
import sys, yaml
path, key = sys.argv[1], sys.argv[2]
try:
    doc = yaml.safe_load(open(path))
    node = doc
    for part in key.split('.'):
        node = node[int(part)] if part.isdigit() else node[part]
    print(node)
except (KeyError, IndexError, TypeError):
    sys.exit(f"yq_get: no key '{key}' in {path}")
PY
}

# -- reading files the join-api container can write ---------------------------
#
# join-api parses applicant-controlled payloads and bind-mounts part of this
# pack READ-WRITE (docker-compose.yml). Host scripts run as root on the
# droplet. So no host script may ever `source` or execute a file that
# container can reach: `. .env` and `. hurl/topology.sh` do not assign, they
# EXECUTE -- one appended line and the next `scripts/console.sh status` is
# root on the host (docs/security-review-2026-08-23.md, finding H1). The two
# helpers below read those files as DATA instead, with no shell evaluation
# anywhere in the path.
#
# Both fail closed. A line neither can read is refused, never skipped: a line
# this parser does not understand is exactly the shape an injected line has,
# and skipping it would hand back the silence the attacker wants.

# kp2_load_env <path> -- export KEY=VALUE from a dotenv file without sourcing
# it. Blank lines and `#` comments are skipped. Every other line must be
# KEY=VALUE with KEY matching [A-Z_][A-Z0-9_]*. One layer of matching single
# or double quotes is stripped and what is left is exported VERBATIM -- no
# expansion, no word splitting -- which is also what finally removes the
# `&`-in-a-DSN quoting fragility .env.example and infra/ci/db-sync-remote.sh
# both warn about in prose (db-sync-remote.sh writes KEY='value', so the
# single-quote stripping is load-bearing). An UNQUOTED value may not contain
# whitespace: `X=$(touch /tmp/pwned)` is refused here rather than executed,
# which is what sourcing would have done with it.
#
# A missing file is not an error (lib-stack.sh's `[ -f ... ] &&` guard used
# to say the same thing); an unreadable line is.
kp2_load_env() {
  local path="$1" line key value lineno=0
  [ -f "$path" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    line=${line%$'\r'}
    [[ $line =~ ^[[:space:]]*(#|$) ]] && continue
    if [[ ! $line =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
      fail "$path line $lineno is not a KEY=VALUE assignment:

  $line

This file is read as data, never sourced -- join-api can write the tree it
sits in and these scripts run as root on the droplet, so a line that is not
a plain assignment is refused rather than executed or skipped. Fix the line
(or re-run scripts/gen-secrets.sh) and try again."
    fi
    key=${BASH_REMATCH[1]}; value=${BASH_REMATCH[2]}
    if [[ $value =~ ^\'(.*)\'$ || $value =~ ^\"(.*)\"$ ]]; then
      value=${BASH_REMATCH[1]}
    elif [[ $value =~ [[:space:]] ]]; then
      fail "$path line $lineno assigns $key an unquoted value containing whitespace:

  $line

Sourcing this file would have run everything after the first space as a
command. Quote the value ($key='...') if the whitespace is real; this
parser refuses it rather than guessing."
    fi
    export "$key=$value"
  done < "$path"
}

# kp2_load_topology <topology.json> -- declare SS_UI, SS_REST, SS_REST_TLS,
# SS_ORDER, HOST_SS and CLIENT_CONN from hurl/topology.json, the same file
# apps/console/truth.py already reads as data. hurl/generate.py still WRITES
# hurl/topology.sh (other consumers and humans read it); the host simply
# stops sourcing it.
#
# One deliberate difference from topology.sh: the federation owner's own
# PDGA:MANAGEMENT pair. generate.py adds it to topology.sh from manifest.yaml
# + configs/x-road-bus/federation-core.yaml; it is not in topology.json, and
# re-deriving it here would be a second reading of those files to drift
# against. Nothing consumes it -- scripts/acceptance.sh is the only reader of
# ${!HOST_SS[@]} and skips that pair by name -- so it is dropped rather than
# re-derived.
#
# Every key and value is checked against [A-Za-z0-9_.:-] (ports additionally
# against ^[0-9]{1,5}$) BEFORE it reaches a bash assignment, so a topology
# whose host name is `; rm -rf /` is a refusal, not a command. The path
# arrives via argv, never spliced into the program text -- same rule as
# yq_get above.
kp2_load_topology() {
  local path="$1" rows name key value
  [ -f "$path" ] || fail "$path not found -- run 'python3 hurl/generate.py' first"
  rows=$(python3 - "$path" <<'PY'
import json, re, sys
path = sys.argv[1]
OK = re.compile(r'^[A-Za-z0-9_.:-]+$')
PORT = re.compile(r'^[0-9]{1,5}$')

def ck(what, node, key, pattern=OK):
    # A missing key is a malformed topology, not a traceback: same curated
    # refusal as a value that fails the charset check.
    if key not in node:
        sys.exit(f"kp2_load_topology: {path}: {what} has no '{key}' -- "
                 f"re-run 'python3 hurl/generate.py'")
    text = str(node[key])
    if not pattern.match(text):
        sys.exit(f"kp2_load_topology: {path}: {what} is not a plain "
                 f"[A-Za-z0-9_.:-] token: {text!r}")
    return text

try:
    topo = json.load(open(path))
    servers, subsystems = topo["security_servers"], topo["subsystems"]
except (OSError, ValueError, KeyError) as exc:
    sys.exit(f"kp2_load_topology: {path} is not a readable topology.json: {exc}")

# No servers is not a small federation, it is a broken file: scripts/
# acceptance.sh iterates SS_ORDER and HOST_SS, and empty ones would let it
# report green having checked nothing.
if not servers:
    sys.exit(f"kp2_load_topology: {path} declares no security_servers -- "
             f"re-run 'python3 hurl/generate.py'")

out = []
for s in servers:
    host = ck("security_servers[]", s, "host")
    out.append(("SS_ORDER", "-", host))
    out.append(("SS_UI", host, ck("security_servers[]", s, "host_ui_port", PORT)))
    out.append(("SS_REST", host, ck("security_servers[]", s, "host_proxy_port", PORT)))
    out.append(("SS_REST_TLS", host,
                ck("security_servers[]", s, "host_proxy_tls_port", PORT)))
for s in subsystems:
    pair = (ck("subsystems[]", s, "member_code") + ":"
            + ck("subsystems[]", s, "subsystem_code"))
    out.append(("HOST_SS", pair, ck("subsystems[]", s, "hosted_on")))
    out.append(("CLIENT_CONN", pair,
                ck("subsystems[]", {"connection_type": s.get("connection_type", "HTTP")},
                   "connection_type")))
print("\n".join("\t".join(row) for row in out))
PY
  ) || fail "could not read $path -- see the message above"

  declare -gA SS_UI=() SS_REST=() SS_REST_TLS=() HOST_SS=() CLIENT_CONN=()
  SS_ORDER=()
  while IFS=$'\t' read -r name key value; do
    [ -n "$name" ] || continue
    case "$name" in
      SS_ORDER) SS_ORDER+=("$value") ;;
      SS_UI|SS_REST|SS_REST_TLS|HOST_SS|CLIENT_CONN)
        # Indirect assignment, so the six array names stay a literal
        # whitelist above rather than something read out of the file.
        declare -g "$name[$key]=$value" ;;
      *) fail "kp2_load_topology: unknown topology array '$name' from $path" ;;
    esac
  done <<< "$rows"
}

export -f log fail retry
