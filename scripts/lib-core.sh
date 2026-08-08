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
# become a Python SyntaxError or worse (C15, docs/notes/reviews/2026-08-01-branch-review.md).
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

export -f log fail retry
